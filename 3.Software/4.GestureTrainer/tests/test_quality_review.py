import csv
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer
from core import read_samples
from workflow import Workflow
from quality_review import sample_hash
from test_workflow import seed_data, fake_result


def review_fixture(workflow):
    seed_data(workflow)
    root=workflow.root/'fixture-evidence';root.mkdir()
    hashes={};entries={};offset=0
    for label in range(8):
        p=workflow.data/f'{label}.csv';(root/p.name).write_bytes(p.read_bytes())
        hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
        with p.open(newline='') as f:rows=list(csv.reader(f))
        for line,row in enumerate(rows,1):
            entries[label,line]=dict(id=f'{label} #{line:03d}',label=label,line=line,sha256=sample_hash(list(map(float,row[1:]))),
                medoid_index=offset+60,reason='测试用的筛查理由')
        offset+=len(rows)
    priority=[(0,119),(3,35),(3,46),(3,118),(3,119),(3,120),(5,67),(6,119)]
    review=[(0,12),(1,8),(1,34),(1,38),(1,49),(6,94),(7,12)]
    body=dict(date='2026-11-04',counts=[120]*8,original_sha256=hashes,
        priority=[entries[k] for k in priority],review=[entries[k] for k in review])
    report=root/'audit.json';report.write_text(json.dumps(body),encoding='utf-8')
    return report,root


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Trainer(Path(self.temp.name));self.addCleanup(self.app.close)
        self.w=self.app.workflow

    def originals(self):return [(self.w.data/f'{i}.csv').read_bytes() for i in range(8)]

    def test_adoption_backup_review_retention_restart_and_idempotence(self):
        report,root=review_fixture(self.w);before=self.originals()
        self.w.quality.import_review(report,root)
        self.assertEqual(self.originals(),before)
        self.assertFalse(self.w.state()['ready_to_train'])
        self.assertEqual(len(self.w.quality.details()['candidates']),15)
        result=self.w.quality.apply()
        self.assertEqual(result['counts'],[119,120,120,115,120,119,119,120])
        state=self.w.state()['quality'];self.assertEqual(state['missing_total'],8)
        self.assertEqual(state['review_count'],7)
        backup=Path(state['backup'])
        self.assertEqual([(backup/f'{i}.csv').read_bytes() for i in range(8)],before)
        values,_,_=read_samples(self.w.data,minimum=0)
        for record in self.w.quality.details()['candidates']:
            row=[v for frame in record['sample'] for v in frame]
            self.assertEqual(row in values,record['category']=='review')
        after=self.originals();self.w.quality.apply();self.assertEqual(self.originals(),after)
        reloaded=Workflow(self.app)
        self.assertEqual(reloaded.state()['quality']['missing_total'],8)
        self.assertTrue(reloaded.state()['quality']['applied'])

    def test_changed_source_invalid_evidence_and_active_task_are_rejected(self):
        report,root=review_fixture(self.w)
        body=json.loads(report.read_text());body['priority'][0]['sha256']='0'*64
        report.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError,'不一致'):self.w.quality.import_review(report,root)
        self.assertFalse(self.w.quality.state([120]*8)['available'])
        body['priority'][0]['sha256']=sample_hash(list(map(float,next(csv.reader((root/'0.csv').read_text().splitlines()[118:119]))[1:])))
        report.write_text(json.dumps(body));self.w.quality.import_review(report,root)
        self.app.job['running']=True
        with self.assertRaises(ValueError):self.w.quality.apply()
        self.app.job['running']=False
        with (self.w.data/'1.csv').open('a') as f:f.write('\n')
        before=self.originals()
        self.assertTrue(self.w.state()['quality']['source_changed'])
        with self.assertRaisesRegex(ValueError,'不同'):self.w.quality.apply()
        self.assertEqual(self.originals(),before)

    def test_partial_write_failure_restores_all_changed_files(self):
        report,root=review_fixture(self.w);self.w.quality.import_review(report,root)
        before=self.originals()
        import quality_review
        replace=quality_review.os.replace
        def fail_second(src,dst):
            if Path(src).parent.name=='prepared' and Path(src).name=='3.csv':raise OSError('injected failure')
            return replace(src,dst)
        with patch('quality_review.os.replace',side_effect=fail_second):
            with self.assertRaisesRegex(OSError,'injected'):self.w.quality.apply()
        self.assertEqual(self.originals(),before)
        self.assertFalse(self.w.state()['quality']['applied'])
        self.w.quality.apply();self.assertEqual(self.w.state()['quality']['missing_total'],8)

    def test_capture_rejects_isolated_replay_and_stops_after_one_new_sample(self):
        report,root=review_fixture(self.w);self.w.quality.import_review(report,root);self.w.quality.apply()
        isolated=self.w.quality.details()['candidates'][0]['sample']
        with (self.w.data/'0.csv').open() as f:row=next(csv.reader(f))
        fresh=list(map(float,row[1:]));fresh[5]+=.0123456
        frames=[fresh[i:i+2] for i in range(0,200,2)]
        extra=[row.copy() for row in frames];extra[2][1]+=.02345
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5);self.addCleanup(listener.close)
        commands=[]
        def device():
            c,_=listener.accept()
            with c:
                c.settimeout(5);commands.append(c.recv(64))
                for sample in [isolated,frames,extra]:
                    c.sendall(''.join(f'{a},{b},{4 if i==99 else 1 if i<20 else 3}\n' for i,(a,b) in enumerate(sample)).encode())
                commands.append(c.recv(64))
        threading.Thread(target=device,daemon=True).start()
        self.w.start('collecting',0,'127.0.0.1',listener.getsockname()[1])
        end=time.monotonic()+8
        while (self.w.session['running'] or len(commands)<2) and time.monotonic()<end:time.sleep(.02)
        self.assertFalse(self.w.session['running'])
        self.assertEqual(self.w.session['saved'],1)
        self.assertEqual(self.w.session['duplicates'],1)
        self.assertEqual(self.w.state()['counts'][0],120)
        self.assertEqual(self.w.state()['quality']['missing_total'],7)
        self.assertEqual(commands,[b'imu\n',b'inference\n'])

    def test_only_refilled_data_can_train_and_old_model_remains_unchanged(self):
        report,root=review_fixture(self.w);fake_result(self.w)
        model=self.w._run()/'gesture_model.tflite';before=model.read_bytes()
        self.w.quality.import_review(report,root);self.w.quality.apply()
        self.assertFalse(self.w.state()['model_fresh'])
        with self.assertRaises(ValueError):self.w.train(60)
        for label,count in enumerate(self.w.state()['counts']):
            with (self.w.data/f'{label}.csv').open() as f:row=next(csv.reader(f))
            for n in range(120-count):
                updated=row.copy();updated[1]=float(row[1])+.12345+n*.001
                with (self.w.data/f'{label}.csv').open('a',newline='') as f:csv.writer(f).writerow(updated)
        self.assertTrue(self.w.state()['quality']['complete'])
        self.assertTrue(self.w.state()['ready_to_train'])
        self.w.quality.require_resolved()
        self.assertEqual(model.read_bytes(),before)
        # Reintroducing an old rejected row is not a valid replacement.
        first=self.w.quality.details()['candidates'][0]
        with (self.w.data/'0.csv').open('a',newline='') as f:csv.writer(f).writerow([0,*[v for frame in first['sample'] for v in frame]])
        with self.assertRaisesRegex(ValueError,'又出现'):self.w.quality.require_resolved()
