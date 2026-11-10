import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop import DesktopSession

class DesktopSessionTests(unittest.TestCase):
    def test_owned_service_lifetime_and_occupied_port(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            session=DesktopSession(Path(folder),occupied.getsockname()[1])
            try:
                url=session.start();self.assertNotEqual(int(url.rsplit(':',1)[1]),occupied.getsockname()[1])
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url+'/api/health') as response:self.assertEqual(json.load(response)['service'],'muggleswand-trainer')
                self.assertIsNone(session.task())
                session.trainer.job['running']=True;self.assertEqual(session.task(),'task')
                session.trainer.job['running']=False;session.trainer.ota['running']=True;self.assertEqual(session.task(),'ota')
                session.trainer.ota['running']=False
            finally:session.close()
            session.close()  # A failed/repeated startup cleanup is safe.

    def test_second_window_reuses_service_and_never_stops_its_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            owner=DesktopSession(Path(folder));viewer=DesktopSession(Path(folder))
            try:
                url=owner.start();self.assertEqual(viewer.start(),url);self.assertIsNone(viewer.trainer)
                viewer.close();self.assertTrue(owner.thread.is_alive())
            finally:viewer.close();owner.close()
