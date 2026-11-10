import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Trainer
from alphabet import CANDIDATES, ROUTES, CATALOG_ID


def seed_candidates(screen, count=12, repeats=10):
    rng = np.random.default_rng(42)
    t = np.linspace(0, 1, 100)
    for candidate in range(count):
        # Candidates 0 and 1 deliberately have identical motion distributions.
        frequency = max(1, candidate)
        for trial in range(repeats):
            frame = np.c_[np.sin(t*2*np.pi*frequency)*4, np.cos(t*2*np.pi*frequency)*3,
                          np.r_[np.ones(20), np.full(70, 2), np.full(10, 4)]]
            frame[:, :2] += rng.normal(0, .015, (100, 2))
            screen._save(candidate, frame.tolist(), True, 'complete', f'group-{trial//5}', 1)


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Trainer(Path(self.temp.name))
        self.addCleanup(self.app.close)

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Screening did not finish')

    def tcp_device(self, completed=True):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(5)
        self.addCleanup(listener.close)
        commands = []
        def serve():
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(5)
                commands.append(conn.recv(128))
                raw = ('0,0,1\n'*100).encode()
                if completed:
                    raw += b'3,2,2\n-3,-2,3\n3,2,3\n0,0,4\n0,0,4\n'
                conn.sendall(raw[:19])
                conn.sendall(raw[19:])
                commands.append(conn.recv(128))
        threading.Thread(target=serve, daemon=True).start()
        return listener.getsockname()[1], commands

    def test_lowercase_catalog_does_not_relabel_old_candidate_data(self):
        self.assertEqual([c[1] for c in CANDIDATES[:20]], list('abcdeghlmnopqrsuvwyz'))
        self.assertEqual([c[1] for c in CANDIDATES[20:]], ['8','∞','○逆时针'])
        self.assertEqual(len(ROUTES), 23)
        self.assertTrue(all(r['path'].count('M') == 1 and not r['connector'] and len(r['start']) == 2 for r in ROUTES))
        old = self.app.workspace/'screening/0.jsonl'
        old.write_text('{"old_circle_record":true}\n', encoding='utf-8')
        old_report = self.app.workspace/'screening/analysis.json'
        old_report.write_text('{"old_circle_analysis":true}', encoding='utf-8')
        previous_alphabet = self.app.workspace/'screening/lowercase-v1/0.jsonl'
        previous_alphabet.parent.mkdir(parents=True, exist_ok=True)
        previous_alphabet.write_text('{"previous_alphabet":true}\n', encoding='utf-8')
        state = self.app.screening.state(0)
        self.assertEqual(state['catalog_id'], CATALOG_ID)
        self.assertEqual(len(state['candidates']), 23)
        self.assertEqual(state['candidates'][0]['trials'], 0)
        self.assertIsNone(state['report'])
        self.app.screening.reset(0)
        self.assertTrue(old.exists() and old_report.exists())
        self.assertTrue(previous_alphabet.exists())
        archive = zipfile.ZipFile(io.BytesIO(self.app.screening.archive()))
        catalog = json.loads(archive.read('candidates.json'))
        self.assertEqual(catalog['catalog_id'], CATALOG_ID)
        self.assertEqual(catalog['candidates'][19]['letter'], 'z')
        self.assertEqual(catalog['candidates'][-1]['letter'], '○逆时针')

    def test_append_symbols_keeps_letter_recordings_and_invalidates_old_report(self):
        from screening import Screening
        root = self.app.screening.root
        frame = [[0,0,1]]*100
        self.app.screening._save(0,frame,True,'complete','old-letter',1)
        raw = (root/'0.jsonl').read_bytes()
        (root/'analysis.json').write_text(json.dumps({'candidates':[{}]*20,'selected':[0]}),encoding='utf-8')
        updated = Screening(self.app.workspace,self.app.lock)
        self.assertIsNone(updated.report)
        self.assertEqual((root/'0.jsonl').read_bytes(),raw)
        self.assertEqual(updated.state(20)['candidates'][20]['accepted'],0)
        self.assertTrue(list((root/'backups').iterdir()))

    def test_capture_single_trial_once_restore_and_keep_training_separate(self):
        self.app.seed_data()
        before = (self.app.data/'5.csv').read_bytes()
        port, commands = self.tcp_device()
        self.app.screen_action('start', dict(candidate=4, host='127.0.0.1', port=port, total=1))
        self.wait(lambda:not self.app.screening.session['running'])
        self.wait(lambda:len(commands)==2)
        records = self.app.screening.records(4)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['accepted'])
        self.assertEqual(len(records[0]['frames']), 100)
        self.assertEqual(commands, [b'imu\n', b'inference\n'])
        self.assertEqual((self.app.data/'5.csv').read_bytes(), before)

    def test_timeout_counted_and_shared_operations_locked(self):
        port, commands = self.tcp_device(completed=False)
        with patch('screening.TRIAL_SECONDS', .15):
            self.app.screen_action('start', dict(candidate=12, host='127.0.0.1', port=port, total=1))
            with self.assertRaises(ValueError):
                self.app.seed_data()
            self.wait(lambda:not self.app.screening.session['running'])
        records = self.app.screening.records(12)
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]['accepted'])
        self.assertEqual(records[0]['reason'], 'timeout')

    def test_stop_does_not_count_unfinished_attempt(self):
        port, commands = self.tcp_device(completed=False)
        self.app.screen_action('start', dict(candidate=0, host='127.0.0.1', port=port, total=10))
        self.wait(lambda:self.app.screening.session['phase']=='armed')
        self.app.screen_action('stop', {})
        self.assertEqual(self.app.screening.records(0), [])
        self.assertFalse(self.app.screening.session['running'])

    def test_analysis_rejects_duplicates_and_separates_confusing_candidates(self):
        screen = self.app.screening
        seed_candidates(screen)
        result = self.app.screen_action('analyze', {})['report']
        self.assertEqual(len(result['selected']), 10)
        self.assertFalse(0 in result['selected'] and 1 in result['selected'])
        self.assertTrue(any({p['a'],p['b']}=={0,1} and p['confusion']>0 for p in result['confusing_pairs']))
        self.assertGreater(result['selected_loo_accuracy'], .8)
        self.assertEqual(result['recording_groups']['0'], 2)
        # State differences alone must not create additional usable samples.
        frame = screen.records(0)[0]['frames']
        for i in range(10):
            copied = [[a,b,(state+i)%5] for a,b,state in frame]
            screen._save(19, copied, True, 'complete', 'duplicates', 1)
        report = screen.analyze()['report']
        self.assertEqual(report['candidates'][19]['unique'], 1)
        self.assertNotIn(19, report['eligible'])
        self.assertTrue((screen.root/'analysis.json').is_file())
        archive = zipfile.ZipFile(io.BytesIO(screen.archive()))
        self.assertIn('analysis.json', archive.namelist())
        self.assertNotIn('data/5.csv', archive.namelist())
        screen.reset(0)
        self.assertIsNone(screen.report)
        self.assertFalse((screen.root/'analysis.json').exists())
        self.assertEqual(screen.records(0), [])
        self.assertTrue(list((screen.root/'backups').iterdir()))

    def test_fewer_candidates_are_explicitly_provisional(self):
        seed_candidates(self.app.screening, count=3)
        result = self.app.screening.analyze()['report']
        self.assertTrue(result['provisional'])
        self.assertEqual(len(result['selected']), 3)
        self.assertTrue(result['needs_review'])


if __name__ == '__main__':
    unittest.main()
