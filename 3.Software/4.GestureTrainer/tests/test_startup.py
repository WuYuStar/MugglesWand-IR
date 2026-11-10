import errno
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import launch
from startup import SERVICE,find_running,running_at,remember_server


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name)/'user-data'

    def serve(self, routes):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                value=routes.get(self.path)
                self.send_response(200 if value else 404);self.end_headers()
                self.wfile.write(json.dumps(value or dict(error='not found')).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def close():server.shutdown();server.server_close()
        self.addCleanup(close)
        return server

    def test_identity_and_workspace_must_both_match(self):
        server=self.serve({'/api/health':dict(service=SERVICE,workspace=str(self.workspace))})
        self.assertEqual(running_at(server.server_port,self.workspace),f'http://127.0.0.1:{server.server_port}')
        self.assertIsNone(running_at(server.server_port,self.workspace/'different'))
        foreign=self.serve({'/api/health':dict(service='other',workspace=str(self.workspace))})
        self.assertIsNone(running_at(foreign.server_port,self.workspace))

    def test_old_running_trainer_without_health_is_recognized(self):
        server=self.serve({'/api/workflow/state':dict(profile='gesture-workflow-v1',minimum=120,
            gestures=['a','e','n','o','p','s','8','∞'],workspace=str(self.workspace/'gesture-workflow-v1'))})
        self.assertIsNotNone(running_at(server.server_port,self.workspace))
        self.assertIsNone(running_at(server.server_port,self.workspace/'different'))

    def test_duplicate_launch_opens_original_without_setup_or_data_initialization(self):
        server=self.serve({'/api/health':dict(service=SERVICE,workspace=str(self.workspace))})
        args=['--workspace',str(self.workspace),'--port',str(server.server_port)]
        with patch('app.Trainer') as trainer,patch('app.webbrowser.open') as browser:
            self.assertEqual(app.main(args),0)
            trainer.assert_not_called();browser.assert_called_once_with(f'http://127.0.0.1:{server.server_port}')
        with patch.object(sys,'argv',['launch.py',*args]),patch('launch.venv.EnvBuilder') as environment,patch('launch.subprocess.run') as install,patch('launch.subprocess.call') as start,patch('launch.webbrowser.open') as browser:
            self.assertEqual(launch.main(),0)
            environment.assert_not_called();install.assert_not_called();start.assert_not_called()
            browser.assert_called_once()
        self.assertFalse(self.workspace.exists())

    def test_foreign_occupied_address_automatically_uses_another(self):
        foreign=self.serve({})
        selected=[]
        def stop(server):selected.append(server.server_port);raise KeyboardInterrupt
        with patch.object(app.Server,'serve_forever',stop):
            app.main(['--workspace',str(self.workspace),'--port',str(foreign.server_port),'--no-browser'])
        self.assertEqual(len(selected),1)
        self.assertNotEqual(selected[0],foreign.server_port)
        self.assertEqual(json.loads((self.workspace/'server.json').read_text())['port'],selected[0])
        # The unrelated service was never stopped.
        self.assertIsNone(running_at(foreign.server_port,self.workspace))

    def test_remembered_address_reuses_an_automatically_selected_port(self):
        self.workspace.mkdir()
        server=self.serve({'/api/health':dict(service=SERVICE,workspace=str(self.workspace))})
        foreign=self.serve({})
        remember_server(self.workspace,server.server_port)
        self.assertEqual(find_running(foreign.server_port,self.workspace),f'http://127.0.0.1:{server.server_port}')
        (self.workspace/'server.json').write_text('{broken')
        self.assertIsNone(find_running(foreign.server_port,self.workspace))

    def test_windows_reserved_range_uses_os_selected_address_without_waiting(self):
        original=app.Server;selected=[]
        def reserve(address,trainer):
            if address[1]==8765:raise PermissionError(errno.EACCES,'Windows reserved range')
            return original(address,trainer)
        def stop(server):selected.append(server.server_port);raise KeyboardInterrupt
        with patch('app.find_running',return_value=None),patch('app.running_at') as probe,patch.object(original,'serve_forever',stop),patch('app.Server',side_effect=reserve) as create:
            app.main(['--workspace',str(self.workspace),'--no-browser'])
            self.assertEqual(create.call_args_list[1].args[0],('127.0.0.1',0))
            probe.assert_not_called()
        self.assertGreater(selected[0],0)

    def test_non_address_errors_do_not_trigger_a_second_instance(self):
        with patch('app.find_running',return_value=None),patch('app.Server',side_effect=OSError(errno.EIO,'injected IO failure')) as server,patch('app.Trainer') as trainer:
            with self.assertRaises(OSError):app.main(['--workspace',str(self.workspace),'--no-browser'])
            self.assertEqual(server.call_count,1);trainer.assert_not_called()
