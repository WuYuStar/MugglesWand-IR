"""Exercise the maintenance client against a local WebSocket peer, never a wand."""
import base64
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template

class DiagnosticTests(unittest.TestCase):
    def module(self):
        path=find_template()/'tools/verify_device.py'
        spec=importlib.util.spec_from_file_location('wand_diagnostic',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

    def test_health_log_and_mode_cycles_do_not_write_settings(self):
        commands=[];finished=threading.Event()
        class Peer(socketserver.StreamRequestHandler):
            def handle(self):
                lines=[]
                while True:
                    line=self.rfile.readline()
                    if line in (b'\r\n',b''):break
                    lines.append(line)
                key=next(x.split(b':',1)[1].strip() for x in lines if x.lower().startswith(b'sec-websocket-key:'))
                accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                self.wfile.write(b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                mode,count=2,100
                while True:
                    head=self.rfile.read(2)
                    if len(head)!=2:return
                    n=head[1]&127
                    if n==126:n=struct.unpack('!H',self.rfile.read(2))[0]
                    mask=self.rfile.read(4);raw=self.rfile.read(n);command=bytes(v^mask[i%4] for i,v in enumerate(raw)).decode();commands.append(command)
                    if command=='imu':mode=1
                    elif command=='back':
                        mode=2
                        if commands.count('back')==3:finished.set()
                    elif command=='web:status':
                        count+=100
                        status=dict(type='status',version='6.1-rc1',mode=mode,imuReady=True,inferenceReady=True,sampleCount=count,bindings=[-1]*8,gestureEffects=[-1]*8,gestureLabels=list('aenops')+['8','∞'],password='PRIVATE')
                        data=json.dumps(status).encode();self.wfile.write(b'\x81\x7e'+struct.pack('!H',len(data))+data)
                    else:raise AssertionError(command)
        server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            m=self.module();real=m.Device
            with tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'health.jsonl'
                with patch.object(m,'Device',lambda host:real('127.0.0.1',server.server_address[1])),patch.object(sys,'argv',['verify','--seconds','1','--cycles','2','--output',str(output)]),redirect_stdout(io.StringIO()):m.main()
                self.assertTrue(finished.wait(2))
                rows=[json.loads(s) for s in output.read_text().splitlines()];self.assertGreater(len(rows),2);self.assertEqual(rows[0]['type'],'baseline');self.assertNotIn('PRIVATE',output.read_text())
                self.assertEqual(commands.count('imu'),2);self.assertEqual(commands.count('back'),3);self.assertEqual(set(commands),{'web:status','imu','back'})
        finally:server.shutdown();server.server_close()

    def test_incomplete_handshake_fails_instead_of_hanging(self):
        class Close(socketserver.BaseRequestHandler):
            def handle(self):self.request.recv(4096)
        server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Close);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with self.assertRaises(EOFError):self.module().Device('127.0.0.1',server.server_address[1])
        finally:server.shutdown();server.server_close()
