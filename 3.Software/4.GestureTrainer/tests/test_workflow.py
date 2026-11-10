import csv
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer, find_seed, find_template
from workflow_core import PROFILE,GESTURES,SCREEN_IDS,prepare_runtime_firmware,RUNTIME_MAPPING
from workflow import Workflow,atomic_json


def seed_data(workflow):
    for i in range(8):shutil.copy2(find_seed()/f'{i}.csv',workflow.data/f'{i}.csv')


def fake_result(workflow):
    # Protocol fixture only; never used for real inference or hardware updates.
    seed_data(workflow)
    run=workflow.root/'runs'/'fixture';run.mkdir(parents=True,exist_ok=True)
    model=b'workflow test model'
    (run/'gesture_model.tflite').write_bytes(model)
    report=dict(profile=PROFILE,gestures=GESTURES,counts=[120]*8,data_fingerprint=workflow._stats()['fingerprint'],
                model_sha256=hashlib.sha256(model).hexdigest(),accuracy=.9,trigger_accuracy=.8,mean=[0,0],std=[1,1])
    atomic_json(run/'report.json',report)
    workflow.result=dict(run=run.name,report=report)
    return run


def fake_firmware(workflow,run):
    data=b'\xe9'+b'\0'*11+b'\x09\0'+b'\0'*30
    (run/'MugglesWand-gestures-app.bin').write_bytes(data)
    atomic_json(run/'firmware-manifest.json',dict(mapping_mode=RUNTIME_MAPPING,
        model_sha256=workflow.result['report']['model_sha256'],firmware_sha256=hashlib.sha256(data).hexdigest()))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Trainer(Path(self.temp.name));self.addCleanup(self.app.close)
        self.w=self.app.workflow

    def wait(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.02)
        self.fail('Workflow timed out')

    def test_120_unique_threshold_and_legacy_model_not_reused(self):
        seed_data(self.w)
        path=self.w.data/'7.csv'
        with path.open() as f:rows=list(csv.reader(f))
        with path.open('w',newline='') as f:csv.writer(f).writerows(rows[:119]+[rows[0]]*5)
        self.assertEqual(self.w.state()['counts'][7],119)
        self.assertFalse(self.w.state()['ready_to_train'])
        with self.assertRaisesRegex(ValueError,'120'):
            self.w.train(60)
        self.app.result=dict(run='legacy',report=dict(counts=[120]*8))
        with self.assertRaises(ValueError):self.w.start('testing',None,'127.0.0.1',8080)
        self.assertIsNone(self.w.result)

    def test_import_maps_gestures_not_actions_and_preserves_source(self):
        root=self.app.workspace/'screening/lowercase-one-stroke-v1';root.mkdir(parents=True,exist_ok=True)
        rows=[]
        for label,screen_id in enumerate(SCREEN_IDS):
            frames=[[label+.1,j/100,4 if j==99 else 1] for j in range(100)]
            record=dict(accepted=True,frames=frames,session='test',time=1)
            p=root/f'{screen_id}.jsonl';p.write_text(json.dumps(record)+'\n')
            rows.append(p.read_bytes())
        self.w.import_screening();self.w.import_screening()
        self.assertEqual(self.w.state()['counts'],[1]*8)
        self.assertEqual([p.read_bytes() for p in [root/f'{i}.jsonl' for i in SCREEN_IDS]],rows)
        self.assertFalse(any(self.app.data.glob('*.csv')))

    def test_normal_workflow_only_handles_models_not_function_or_firmware(self):
        run=fake_result(self.w);fake_firmware(self.w,run)
        state=self.w.state()
        self.assertEqual(state['model_protocol'],1)
        for field in ['actions','firmware_ready','mapping_mode','build_available']:self.assertNotIn(field,state)
        with self.assertRaisesRegex(ValueError,'移到魔杖'):self.w.save_bindings([5]*8)
        before=(run/'MugglesWand-gestures-app.bin').read_bytes()
        with self.assertRaisesRegex(ValueError,'只管理手势模型'):self.w.build()
        with self.assertRaisesRegex(ValueError,'上传模型'):self.w.ota('192.168.4.1')
        self.assertEqual((run/'MugglesWand-gestures-app.bin').read_bytes(),before)

    def test_changed_data_blocks_old_upgrade_but_exact_duplicate_does_not(self):
        run=fake_result(self.w);fake_firmware(self.w,run)
        p=self.w.data/'0.csv'
        with p.open() as f:first=next(csv.reader(f))
        with p.open('a',newline='') as f:csv.writer(f).writerow(first)
        self.assertTrue(self.w.state()['model_package_ready'])
        first[1]=float(first[1])+.012345
        with p.open('a',newline='') as f:csv.writer(f).writerow(first)
        self.assertFalse(self.w.state()['model_fresh'])
        with self.assertRaisesRegex(ValueError,'重新训练'):self.w.model_path()

    def test_generated_firmware_has_runtime_mapping_and_correct_profile(self):
        run=fake_result(self.w);template=find_template();dest=Path(self.temp.name)/'project'
        (dest/'src/Model').mkdir(parents=True)
        # An old exported inference file cannot restore the old static bindings.
        (dest/'src/Model/gesture_inference.cpp').write_text('stale static model code')
        prepare_runtime_firmware(dest,template,self.w.result['report'])
        header=(dest/'src/Model/GestureProfile.h').read_text()
        self.assertIn('"letters-v1"',header)
        self.assertIn('"a","e","n","o","p","s","8","∞"',header)
        self.assertIn('{-1,-1,-1,-1,-1,-1,-1,-1}',header)
        inference=(dest/'src/Model/gesture_inference.cpp').read_text()
        self.assertIn('EVENT_GESTURE_TRIGGER,config.actions[gesture_id],config.effects[gesture_id]',inference)
        self.assertIn('if (accepted) EventBus::publish(EVENT_GESTURE_TRIGGER',inference)
        self.assertIn('roundf(',inference)
        self.assertIn('gesture 5',(template/'src/Service/WebPage.h').read_text())
        self.assertIn('"legacy-v1"',(template/'src/Model/GestureProfile.h').read_text())

    def test_training_needs_no_firmware_project_or_compiler(self):
        seed_data(self.w)
        self.app.template=Path(self.temp.name)/'no-template'
        with patch('workflow.importlib.util.find_spec',return_value=object()),patch.object(self.app,'_start_job') as start:
            self.w.train(10)
        command=start.call_args.args[0]
        self.assertNotIn('--template',command)
        self.assertFalse(any('platformio' in str(arg) for arg in command))
        self.assertFalse(self.app.template.exists())

    def test_tcp_capture_stops_at_120_and_locks_other_operations(self):
        seed_data(self.w)
        p=self.w.data/'0.csv'
        with p.open() as f:rows=list(csv.reader(f))
        with p.open('w',newline='') as f:csv.writer(f).writerows(rows[:119])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        self.addCleanup(listener.close);received=[]
        def device():
            c,_=listener.accept()
            with c:
                c.settimeout(5);received.append(c.recv(64));time.sleep(.1)
                c.sendall(b'0,0,1\n'*100+b'3,2,2\n-3,-2,3\n0,0,4\n0,0,4\n')
                received.append(c.recv(64))
        threading.Thread(target=device,daemon=True).start()
        self.w.start('collecting',0,'127.0.0.1',listener.getsockname()[1])
        with self.assertRaises(ValueError):self.app.seed_data()
        self.wait(lambda:not self.w.session['running'])
        self.wait(lambda:len(received)==2)
        self.assertEqual(self.w.state()['counts'][0],120)
        self.assertEqual(self.w.session['saved'],1)
        self.assertEqual(received,[b'imu\n',b'inference\n'])

    def test_validation_statistics_are_separate_and_version_specific(self):
        run=fake_result(self.w)
        records=[dict(gesture='a',expected=0,correct=True,accepted=True,counts_for_score=True),
                 dict(gesture='e',expected=0,correct=False,accepted=False,counts_for_score=True),
                 dict(gesture='a',expected=0,correct=True,accepted=True,counts_for_score=False),
                 dict(gesture='a',expected=None,correct=None,accepted=True,counts_for_score=True)]
        (run/'validation.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
        state=self.w.validation_state()
        self.assertEqual(state['total'],2)
        self.assertEqual(state['classification_accuracy'],.5)
        self.assertEqual(state['trigger_accuracy'],.5)
        self.assertEqual(self.w.state()['counts'],[120]*8)


if __name__=='__main__':unittest.main()
