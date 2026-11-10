import copy,hashlib,http.server,json,socket,struct,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model_package import encode,decode,HEADER
from workflow_core import PROFILE,GESTURES
from model_transfer import upload

def fixture(names=None,tag=1):
    names=names or GESTURES;blob=b'\x1c\0\0\0TFL3'+bytes([tag])*64
    report=dict(profile=PROFILE,gestures=names,counts=[120]*len(names),mean=[.12,-.23],std=[1.2,2.4],model_sha256=hashlib.sha256(blob).hexdigest())
    return encode(blob,report),report

class ModelPackageTests(unittest.TestCase):
    def test_model_only_contract_hash_unicode_and_sidecars(self):
        data,r=fixture(GESTURES+['逆时针圆'])
        self.assertEqual(decode(data)['gestures'],r['gestures']);self.assertEqual(len(data),1088+72+32)
        self.assertNotIn(b'bindings',data);self.assertNotIn(b'firmware',data)
        for offset in [0,12,28,40,HEADER.size+10,-1]:
            corrupted=bytearray(data);corrupted[offset]^=1
            with self.assertRaises(ValueError):decode(corrupted)
        with self.assertRaises(ValueError):decode(data[:-1])
        bad=copy.deepcopy(r);bad['std']=[0,1]
        with self.assertRaises(ValueError):encode(data[1088:-32],bad)

    def test_model_transfer_has_no_ota_or_function_commands_and_waits_for_activation(self):
        data,_=fixture();target=decode(data)['sha256'];requests=[];polls=[0];current=['old']
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                requests.append(('GET',self.path));polls[0]+=1
                if polls[0]>2:current[0]=target
                state=dict(protocol=1,contract=1,storage_ready=True,updating=False,max_classes=16,max_model_bytes=49152,active_hash=current[0],job=dict(ticket=123,phase='complete',percent=100,next_hash=target))
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(state).encode())
            def do_POST(self):
                requests.append(('POST',self.path));self.server.payload=self.rfile.read(int(self.headers['Content-Length']));assert self.headers['X-Wand-Model']=='1'
                self.send_response(202);self.end_headers();self.wfile.write(b'{"accepted":true,"ticket":123}')
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();events=[]
        try:
            upload('127.0.0.1',data,lambda *args:events.append(args),http_port=server.server_port,_allow_loopback=True,wait_seconds=5)
            self.assertEqual(server.payload,data);self.assertEqual(events[-1][0],'complete');self.assertGreaterEqual(polls[0],3)
            self.assertTrue(all(path=='/api/model' for _,path in requests))
            n=len(requests);upload('127.0.0.1',data,lambda *a:None,http_port=server.server_port,_allow_loopback=True)
            self.assertEqual(requests[n:],[('GET','/api/model')])
        finally:server.shutdown();server.server_close()

    def test_incompatible_firmware_never_receives_model_or_ota_switch(self):
        data,_=fixture();calls=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):calls.append(self.path);self.send_response(404);self.end_headers()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with self.assertRaisesRegex(ValueError,'6.0'):
                upload('127.0.0.1',data,lambda *a:None,http_port=server.server_port,_allow_loopback=True)
            self.assertEqual(calls,['/api/model'])
        finally:server.shutdown();server.server_close()
