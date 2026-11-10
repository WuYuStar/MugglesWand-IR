import csv
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import io

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import Trainer, Server, find_template, find_seed
from core import read_samples, patched_inference, export_model

class TrainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Trainer(Path(self.temp.name))
        self.addCleanup(self.app.close)

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.03)
        self.fail('Timed out waiting for background task')

    def test_seed_preserves_existing_and_reset_makes_backup(self):
        self.app.seed_data()
        original = (self.app.data / '5.csv').read_bytes()
        self.assertEqual(self.app.state()['counts'], [120]*8)
        self.app.seed_data()
        self.assertEqual((self.app.data/'5.csv').read_bytes(), original)
        self.app.reset_label(5)
        self.assertEqual(self.app.state()['counts'][5], 0)
        self.assertEqual(next((self.app.workspace/'backups').glob('5-*.csv')).read_bytes(), original)

    def test_validation_rejects_nan_wrong_label_and_short_row(self):
        for broken in ([5]+[float('nan')]*200, [4]+[0]*200, [5, 1]):
            with self.subTest(broken=broken[:2]):
                self.app.seed_data()
                with (self.app.data/'5.csv').open('w', newline='') as f:
                    csv.writer(f).writerow(broken)
                with self.assertRaises(ValueError):
                    read_samples(self.app.data)
                (self.app.data/'5.csv').unlink()

    def test_duplicates_do_not_leak_between_splits(self):
        self.app.seed_data()
        path = self.app.data/'0.csv'
        with path.open(newline='') as f:
            row = next(csv.reader(f))
        with path.open('a', newline='') as f:
            csv.writer(f).writerow(row)
        _, _, counts = read_samples(self.app.data)
        self.assertEqual(counts[0], 120)
        with (self.app.data/'1.csv').open('a', newline='') as f:
            row[0] = 1
            csv.writer(f).writerow(row)
        with self.assertRaisesRegex(ValueError, '不同功能'):
            read_samples(self.app.data)

    def test_model_export_is_independent_of_firmware_and_preserves_parameters(self):
        from model_package import export_runtime_model,decode
        from workflow_core import PROFILE,GESTURES
        out=self.app.workspace/'export'
        report=dict(profile=PROFILE,gestures=GESTURES,counts=[120]*8,accuracy=1)
        export_runtime_model(out,b'\x1c\0\0\0TFL3'+bytes(64),[1.25,-2.5],[3,4],report)
        meta=decode((out/'gesture-model.wandmodel').read_bytes())
        self.assertEqual(meta['mean'],[1.25,-2.5]);self.assertEqual(meta['std'],[3,4])
        self.assertFalse((out/'firmware_patch').exists())
        self.assertNotIn('actions',json.loads((out/'report.json').read_text()))

    def test_tcp_fragments_capture_exact_window_and_restore_mode(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        self.addCleanup(listener.close)
        received = []
        def device():
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(6)
                received.append(conn.recv(64))
                conn.sendall(b'log not csv\ninvalid,record\n')
                for i in range(110):
                    line = f'{i},{-i},{4 if i==109 else 1}\n'.encode()
                    conn.sendall(line[:3])
                    conn.sendall(line[3:])
                received.append(conn.recv(64))
        thread = threading.Thread(target=device, daemon=True)
        thread.start()
        self.app.start_collect(5, '127.0.0.1', listener.getsockname()[1])
        self.wait(lambda: self.app.state()['counts'][5] == 1)
        with self.assertRaises(ValueError):
            self.app.reset_label(5)
        self.app.stop_collect()
        thread.join(2)
        self.assertEqual(received, [b'imu\n', b'inference\n'])
        with (self.app.data/'5.csv').open() as file:
            row = next(csv.reader(file))
        self.assertEqual(len(row), 201)
        self.assertEqual(row[:3], ['5', '10.0', '-10.0'])
        self.assertEqual(row[-2:], ['109.0', '-109.0'])
        self.assertFalse(self.app.state()['collect']['running'])

    def test_http_loopback_token_and_origin(self):
        self.app.seed_data()
        server = Server(('127.0.0.1', 0), self.app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(conn.close)
        conn.request('POST', '/api/cancel', '{}', {'Content-Type':'application/json'})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.request('POST', '/api/cancel', '{}', {'X-Trainer-Token': server.token, 'Origin':'https://example.com'})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.request('POST', '/api/cancel', '{}', {'X-Trainer-Token': server.token})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        conn.request('GET', '/api/state')
        response = conn.getresponse()
        self.assertEqual(json.loads(response.read())['counts'], [120]*8)

    def test_cancel_retains_previous_result(self):
        self.app.result = dict(run='previous', report=dict(accuracy=.9))
        run = self.app.workspace/'runs'/'cancel-test'
        run.mkdir(parents=True)
        self.app._start_job([sys.executable, '-c', 'import time; time.sleep(30)'], 'training', run, 60)
        self.app.cancel()
        self.wait(lambda: not self.app.state()['job']['running'])
        self.assertEqual(self.app.state()['job']['phase'], 'cancelled')
        self.assertEqual(self.app.result['run'], 'previous')

    def test_model_archive_has_no_training_data_or_arbitrary_files(self):
        run = self.app.workspace/'runs'/'good'
        from model_package import export_runtime_model
        from workflow_core import PROFILE,GESTURES
        export_runtime_model(run,b'\x1c\0\0\0TFL3'+bytes(64),[0,0],[1,1],dict(profile=PROFILE,gestures=GESTURES,counts=[120]*8,accuracy=1))
        (run/'private.txt').write_text('not for export')
        self.app.result = dict(run='good', report={})
        archive = zipfile.ZipFile(io.BytesIO(self.app.model_archive()))
        self.assertNotIn('private.txt', archive.namelist())
        self.assertIn('gesture-model.wandmodel',archive.namelist())
        self.assertFalse(any(name.startswith('firmware_patch/') for name in archive.namelist()))

if __name__ == '__main__':
    unittest.main()
