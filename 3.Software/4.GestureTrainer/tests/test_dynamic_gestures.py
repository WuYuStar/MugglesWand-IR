import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,find_template
from workflow import Workflow
from workflow_core import GESTURES,check_report,prepare_runtime_firmware
from test_workflow import fake_result,fake_firmware
from test_quality_review import review_fixture

class DynamicGestureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Trainer(Path(self.temp.name));self.addCleanup(self.app.close);self.w=self.app.workflow

    def test_append_preserves_ids_files_and_model_but_blocks_stale_upgrade(self):
        run=fake_result(self.w);fake_firmware(self.w,run)
        original={p.name:p.read_bytes() for p in self.w.data.glob('*.csv')}
        self.assertTrue(self.w.state()['model_package_ready'])
        self.assertEqual(self.w.add_gesture('z','从左向右')['label'],8)
        state=self.w.state()
        self.assertEqual(state['counts'],[120]*8+[0]);self.assertFalse(state['model_fresh']);self.assertFalse(state['model_package_ready'])
        self.assertEqual(state['validation']['per_gesture'],[0]*8)
        for name,data in original.items():self.assertEqual((self.w.data/name).read_bytes(),data)
        self.assertEqual(Workflow(self.app).gestures,GESTURES+['z'])
        with self.assertRaises(ValueError):self.w.model_path()
        with self.assertRaisesRegex(ValueError,'未训练'):self.w.start('testing',8,'127.0.0.1',8080)
        with self.assertRaises(ValueError):self.w.add_gesture('Z')
        with self.assertRaises(ValueError):self.w.add_gesture('')

    def test_dynamic_firmware_count_labels_and_prefix_mapping(self):
        import shutil
        project=Path(self.temp.name)/'firmware';(project/'src/Model').mkdir(parents=True)
        report=dict(profile='gesture-workflow-v1',gestures=GESTURES+['z','u'],counts=[120]*10,mean=[0,0],std=[1,1])
        check_report(report);prepare_runtime_firmware(project,find_template(),report)
        header=(project/'src/Model/GestureProfile.h').read_text()
        self.assertIn('kGestureCount = 10',header);self.assertIn('"letters-v1",8',header)
        self.assertIn('gesture_model_count()',(project/'src/Model/gesture_inference.cpp').read_text())
        self.assertTrue((project/'src/Model/BuiltinModelProfile.h').exists())

    def test_rescan_archives_decisions_and_preserves_quarantined_replay_guard(self):
        audit,source=review_fixture(self.w);self.w.quality.import_review(audit,source);self.w.quality.apply()
        excluded=self.w.quality.excluded_samples();self.assertTrue(excluded)
        current=self.w.quality._report();report=dict(current,id='new-scan',candidates=[],original_counts=self.w._stats()['counts'],source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.w.data.glob('*.csv')})
        run=Path(self.temp.name)/'scan';run.mkdir();(run/'review.json').write_text(json.dumps(report))
        self.w.quality.accept_scan(run)
        self.assertEqual(self.w.quality.excluded_samples(),excluded)
        self.assertTrue(self.w.quality.state(self.w._stats()['counts'])['applied'])

    def test_keep_decision_does_not_isolate_or_block_and_new_gesture_not_replenishment(self):
        audit,source=review_fixture(self.w);self.w.quality.import_review(audit,source)
        before={p.name:p.read_bytes() for p in self.w.data.glob('*.csv')}
        self.w.quality.keep();self.w.quality.require_resolved()
        self.assertFalse(self.w.quality.excluded_samples())
        self.w.add_gesture('u')
        self.assertEqual(self.w.quality.state(self.w._stats()['counts'])['missing_total'],0)
        for name,data in before.items():self.assertEqual((self.w.data/name).read_bytes(),data)

    def test_scan_isolation_removes_repeated_copies_of_same_suspect(self):
        audit,source=review_fixture(self.w);self.w.quality.import_review(audit,source)
        report=dict(self.w.quality._report(),id='duplicate-scan')
        candidate=report['candidates'][0]
        path=self.w.data/f"{candidate['label']}.csv"
        with path.open('a',newline='') as f:csv.writer(f).writerow([candidate['label'],*[v for frame in candidate['sample'] for v in frame]])
        report['source_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.w.data.glob('*.csv')}
        run=Path(self.temp.name)/'duplicate-scan';run.mkdir();(run/'review.json').write_text(json.dumps(report))
        self.w.quality.accept_scan(run);self.w.quality.apply()
        self.w.quality.require_resolved()
        self.assertEqual(self.w._stats()['counts'],[119,120,120,115,120,119,119,120])
