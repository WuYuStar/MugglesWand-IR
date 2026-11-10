import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota import install, device_address, application_bytes
from app import Trainer


class OTATests(unittest.TestCase):
    def test_address_and_firmware_validation(self):
        for host in ['localhost', '127.0.0.1', '0.0.0.0', '8.8.8.8', '192.168.4.1/ota', None]:
            with self.assertRaises(ValueError):
                device_address(host)
        self.assertEqual(device_address('192.168.4.1'), '192.168.4.1')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'firmware.bin'
            path.write_bytes(b'not an ESP32S3 application')
            with self.assertRaises(ValueError):
                application_bytes(path)

    def fake_install(self, response=b'OK', start_status=200):
        data = b'\xe9' + b'\0' * 11 + b'\x09\0' + bytes(range(256)) * 500
        state = dict(ready=False, received=b'', started=False)
        commands = []
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(5)
        self.addCleanup(listener.close)
        def tcp():
            conn, _ = listener.accept()
            with conn:
                commands.append(conn.recv(64))
                state['ready'] = True
        threading.Thread(target=tcp, daemon=True).start()

        class Device(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == '/update':
                    self.send_response(200 if state['ready'] else 404)
                else:
                    assert self.path == '/ota/start?mode=firmware&hash=' + hashlib.md5(data).hexdigest()
                    state['started'] = True
                    self.send_response(start_status)
                self.end_headers()
            def do_POST(self):
                assert self.path == '/ota/upload' and state['started']
                body = self.rfile.read(int(self.headers['Content-Length']))
                state['received'] = body.split(b'\r\n\r\n', 1)[1].rsplit(b'\r\n--', 1)[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Device)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        events = []
        def run():
            install('127.0.0.1', data, lambda *event: events.append(event),
                    http_port=server.server_port, tcp_port=listener.getsockname()[1], wait_seconds=2)
        return run, state, data, commands, events

    def test_switch_upload_and_verified_success(self):
        run, state, data, commands, events = self.fake_install()
        run()
        self.assertEqual(commands, [b'ota\n'])
        self.assertEqual(state['received'], data)
        self.assertEqual(events[-1][0:2], ('complete', 100))
        self.assertTrue(any(e[0] == 'verifying' and e[1] == 99 for e in events))

    def test_rejected_image_and_unconfirmed_reply_never_report_success(self):
        for response, status in [(b'failed', 200), (b'OK', 400)]:
            run, state, data, commands, events = self.fake_install(response, status)
            with self.assertRaises(OSError):
                run()
            self.assertFalse(any(e[0] == 'complete' for e in events))
            if status == 400:
                self.assertEqual(state['received'], b'')

    def test_ota_locks_dataset_and_preserves_model(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Trainer(Path(folder))
            run = app.workspace / 'runs' / 'test'
            run.mkdir(parents=True)
            binary = run / 'MugglesWand-custom-app.bin'
            binary.write_bytes(b'\xe9' + b'\0' * 11 + b'\x09\0' + b'\0' * 30)
            app.result = dict(run='test', report={})
            entered, release = threading.Event(), threading.Event()
            def update(host, data, progress):
                entered.set()
                release.wait(5)
                raise OSError('test disconnected')
            with patch('app.install_ota', update):
                app.update_wand('192.168.4.1')
                self.assertTrue(entered.wait(2))
                with self.assertRaises(ValueError):
                    app.reset_label(5)
                release.set()
                import time
                deadline = time.monotonic() + 2
                while app.state()['ota']['running'] and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertEqual(app.state()['ota']['phase'], 'error')
            self.assertTrue(binary.exists())


if __name__ == '__main__':
    unittest.main()
