"""Integrate a real audited snapshot in an isolated workspace; fake device TCP.
No user training file is edited and no physical device is contacted by this test.
"""
import argparse
import csv
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,Server
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--browser',required=True)
parser.add_argument('--review',type=Path,required=True)
parser.add_argument('--snapshot',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)

with tempfile.TemporaryDirectory() as directory:
    app=Trainer(Path(directory));w=app.workflow
    for path in args.snapshot.glob('*.csv'):shutil.copy2(path,w.data/path.name)
    w.quality.import_review(args.review,args.snapshot)
    server=Server(('127.0.0.1',0),app);threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[];listeners=[];commands=[]
    def device(label,count):
        with (w.data/f'{label}.csv').open() as f:base=list(map(float,next(csv.reader(f))[1:]))
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(20);listeners.append(listener)
        recorded=[];commands.append(recorded)
        def serve():
            connection,_=listener.accept()
            with connection:
                connection.settimeout(10);recorded.append(connection.recv(64))
                samples=[]
                if label==0:samples.append([v for row in w.quality.details()['candidates'][0]['sample'] for v in row])
                # One extra buffered record proves automatic stopping is exact.
                for n in range(count+1):
                    sample=base.copy();sample[7]+=(label+1)*.0123456+n*.000123;samples.append(sample)
                raw=''.join(f'{s[2*i]},{s[2*i+1]},{4 if i==99 else 3 if i>=50 else 2 if i>=20 else 1}\n' for s in samples for i in range(100)).encode()
                connection.sendall(raw[:29]);connection.sendall(raw[29:])
                recorded.append(connection.recv(64))
        threading.Thread(target=serve,daemon=True).start()
        return listener.getsockname()[1]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.wait_for_function("!document.getElementById('quality-panel').hidden")
            assert page.locator('#train').is_disabled() and page.locator('#collect').is_disabled()
            assert page.request.post(f'http://127.0.0.1:{server.server_port}/api/workflow/quality/apply',data={}).status==403
            page.locator('.capture-sheet details').nth(1).evaluate('(el)=>el.open=true')
            page.locator('#open-quality').click()
            page.wait_for_function("document.getElementById('quality-select').options.length===15")
            for i in range(15):
                page.locator('#quality-select').select_option(str(i))
                assert page.locator('#quality-gx svg path').count()==2
                assert page.locator('#quality-gz svg path').count()==2
            page.locator('#quality-close').click()
            page.locator('#apply-quality').click()
            page.wait_for_function("document.getElementById('quality-title').textContent.includes('待补录 8 次')")
            assert w.state()['counts']==[119,120,120,115,120,119,119,120]
            assert page.locator('#collect').inner_text()=='补录 a（还需1次）'
            assert page.locator('#quality-tasks button').count()==4
            # Applied state survives refresh. Every screen width retains an action.
            page.reload();page.wait_for_function("document.getElementById('quality-tasks').children.length===4")
            for width,height,name in [(1440,1000,'desktop'),(768,1024,'tablet'),(390,844,'mobile'),(320,740,'small')]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                if width!=320:page.screenshot(path=str(args.output/f'{name}-replenish.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('.capture-sheet details').nth(1).evaluate('(el)=>el.open=true')
            page.locator('#open-quality').click()
            page.wait_for_function("document.getElementById('quality-select').options.length===15")
            page.locator('#quality-select').select_option('4')
            assert '已单独留存' in page.locator('#quality-record-status').inner_text()
            page.screenshot(path=str(args.output/'desktop-review-dialog.png'))
            page.locator('#quality-close').click()
            for label,count in [(0,1),(3,5),(5,1),(6,1)]:
                page.locator(f'#quality-tasks button[data-label="{label}"]').click()
                port=device(label,count)
                page.locator('#connection-settings').click();page.locator('#connection-advanced').evaluate('(el)=>el.open=true');page.locator('#host').fill('127.0.0.1');page.locator('#port').fill(str(port));page.locator('#connection-done').click()
                page.locator('#collect').click()
                page.wait_for_function(f"document.querySelectorAll('.gesture-choice span')[{label}].textContent==='120 / 120'")
                deadline=time.monotonic()+5
                while w.session['running'] and time.monotonic()<deadline:time.sleep(.02)
                assert w.state()['counts'][label]==120
                assert w.session['saved']==count
                if label==0:assert w.session['duplicates']==1
                assert not w.session['running']
                w.quality.apply()  # Repeating adoption cannot erase new recordings.
            page.wait_for_function("document.getElementById('quality-title').textContent.includes('已补齐')")
            page.wait_for_function("!document.getElementById('train').disabled")
            assert w.state()['counts']==[120]*8 and w.state()['quality']['complete']
            assert len(w.quality.details()['candidates'])==15
            assert all(c==[b'imu\n',b'inference\n'] for c in commands),commands
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();app.close()
        for listener in listeners:listener.close()
print('PASS: imported real review, authenticated adoption, 960->952 backup/quarantine, 15 waveform comparisons, persisted progress, all four replenishment tasks, duplicate rejection, exact stop at120, no extra buffered samples, training gate, responsive widths; no JS errors.')
