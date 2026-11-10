import socket
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Trainer(Path(self.temp.name));self.addCleanup(self.app.close)

    def test_reachability_sends_no_mode_changing_command(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(3)
        self.addCleanup(listener.close);received=[];done=threading.Event()
        def device():
            connection,_=listener.accept()
            with connection:received.append(connection.recv(64))
            done.set()
        threading.Thread(target=device,daemon=True).start()
        result=self.app.check_connection('127.0.0.1',listener.getsockname()[1])
        self.assertEqual(result['connection']['status'],'reachable')
        self.assertTrue(done.wait(3));self.assertEqual(received,[b''])
        self.assertEqual(self.app.workflow.state()['connection']['status'],'reachable')
        self.assertFalse(self.app.workflow.session['running'])

    def test_closed_port_never_claims_connection(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        result=self.app.check_connection('127.0.0.1',port)
        self.assertEqual(result['connection']['status'],'unreachable')

    def test_invalid_addresses_and_busy_tasks_are_rejected(self):
        for host,port in [('8.8.8.8',8080),('0.0.0.0',8080),('192.168.4.1/path',8080),('192.168.4.1',True),('192.168.4.1',65536)]:
            with self.assertRaises(ValueError):self.app.check_connection(host,port)
        self.app.job['running']=True
        with self.assertRaises(ValueError):self.app.check_connection('192.168.4.1',8080)
        self.app.job['running']=False
