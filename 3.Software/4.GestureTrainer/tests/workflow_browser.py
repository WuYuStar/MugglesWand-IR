"""Four-stage UI against real API and real INT8 inference, isolated fixtures.
Training/build job progress and OTA transport are simulated here; workflow_e2e.py
separately verifies real training and two real mapped firmware builds.
"""
import argparse
import csv
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,Server
from test_workflow import seed_data
from workflow_core import GESTURES
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--browser',required=True)
parser.add_argument('--baseline',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)

with tempfile.TemporaryDirectory() as directory:
    app=Trainer(Path(directory));w=app.workflow
    server=Server(('127.0.0.1',0),app)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    listeners=[];connections=[];errors=[]
    def device(features,extra=None):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(30)
        listeners.append(listener);commands=[];connections.append(commands)
        def serve():
            c,_=listener.accept()
            with c:
                c.settimeout(30);commands.append(c.recv(64))
                for frame in [features]+([] if extra is None else [extra]):
                    raw=''.join(f'{frame[2*i]},{frame[2*i+1]},{4 if i==99 else 3 if i>=50 else 2 if i>=20 else 1}\n' for i in range(100)).encode()
                    c.sendall(raw[:27]);c.sendall(raw[27:])
                commands.append(c.recv(64))
        threading.Thread(target=serve,daemon=True).start()
        return listener.getsockname()[1]

    def fake_job(command,kind,run,total=0):
        app.job=dict(running=True,kind=kind,phase='training' if kind=='gesture-training' else 'building',message='正在处理测试任务',epoch=0,total=total)
        def finish():
            time.sleep(1.5)
            with app.lock:
                if kind=='gesture-training':
                    for name in ['gesture_model.tflite','report.json','使用说明.txt']:
                        shutil.copy2(args.baseline/name,run/name)
                    shutil.copytree(args.baseline/'firmware_patch',run/'firmware_patch')
                    report=json.loads((run/'report.json').read_text())
                    report['data_fingerprint']=w._stats()['fingerprint']
                    (run/'report.json').write_text(json.dumps(report))
                    done=report
                else:
                    target=run/'firmware_project/.pio/build/esp32-s3-fh4r2/firmware.bin'
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(args.baseline/'MugglesWand-gestures-app.bin',target)
                    done=None
                app.job['running']=False
                w.finished(kind,run,done,0,False)
        threading.Thread(target=finish,daemon=True).start()

    try:
        with sync_playwright() as p,patch.object(app,'_start_job',fake_job):
            browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.wait_for_function("document.querySelectorAll('.gesture-choice').length===8")
            assert page.locator('#train').is_disabled()
            assert page.locator('#sample-count').inner_text()=='0'
            # A duplicate cannot satisfy the 120 threshold.
            seed_data(w)
            last=w.data/'7.csv'
            with last.open() as f:rows=list(csv.reader(f))
            with last.open('w',newline='') as f:csv.writer(f).writerows(rows[:119]+[rows[0]])
            page.locator('.gesture-choice').nth(7).click()
            page.wait_for_function("document.getElementById('sample-count').textContent==='119'")
            assert page.locator('#train').is_disabled()
            fresh=[j*.001 for j in range(200)]
            port=device(fresh)
            page.locator('#connection-settings').click();page.locator('#connection-advanced').evaluate('(el)=>el.open=true')
            page.locator('#host').fill('127.0.0.1');page.locator('#port').fill(str(port))
            page.locator('#connection-done').click()
            page.locator('#collect').click()
            page.wait_for_function("document.getElementById('sample-count').textContent==='120'")
            page.wait_for_function("!document.getElementById('train').disabled")
            assert connections[0]==[b'imu\n',b'inference\n']
            page.locator('[data-step="2"]').click()
            page.locator('#train').click()
            page.wait_for_function("!document.getElementById('cancel-job').hidden")
            assert page.locator('#collect').is_disabled()
            page.wait_for_function("document.getElementById('training-message').textContent.includes('下一步')")
            assert w.result and w.state()['model_fresh']
            page.locator('#train-next').click()
            with (w.data/'0.csv').open() as f:sample=list(map(float,next(csv.reader(f))[1:]))
            fresh_sample=sample.copy();fresh_sample[17]+=.0123456
            original_file=(w.data/'0.csv').read_bytes()
            # Replay remains known even after it is removed from the current
            # dataset: independence must use the MODEL'S immutable snapshot.
            lines=original_file.splitlines(keepends=True)
            (w.data/'0.csv').write_bytes(b''.join(lines[1:]))
            port=device(sample,extra=fresh_sample)
            page.locator('#connection-settings').click();page.locator('#connection-advanced').evaluate('(el)=>el.open=true');page.locator('#port').fill(str(port));page.locator('#connection-done').click()
            page.locator('#expected').select_option('0')
            before=[(w.data/f'{i}.csv').read_bytes() for i in range(8)]
            page.locator('#test-start').click()
            page.wait_for_function("document.getElementById('prediction').textContent!=='—'")
            page.wait_for_function("document.querySelectorAll('#test-history .history-row').length===2")
            assert page.locator('#prediction').inner_text() in GESTURES
            assert w.validation_state()['total']==1
            assert [(w.data/f'{i}.csv').read_bytes() for i in range(8)]==before
            page.locator('#test-stop').click()
            page.wait_for_function("document.getElementById('test-stop').hidden")
            assert connections[1]==[b'imu\n',b'inference\n']
            (w.data/'0.csv').write_bytes(original_file)
            page.locator('[data-step="4"]').click()
            assert page.locator('#open-wand-settings').get_attribute('href').endswith('/#gesture-settings')
            assert page.locator('#mapping').count()==0
            model=w.result['report']['model_sha256']
            page.locator('[data-step="4"]').click()
            assert page.locator('#ota').is_disabled()
            # No desktop binding save is required to build the model firmware.
            page.wait_for_function("!document.getElementById('build').disabled")
            page.locator('#build').click()
            page.wait_for_function("!document.getElementById('ota').disabled")
            page.locator('[data-step="3"]').click()
            page.locator('[data-step="4"]').click()
            assert not page.locator('#ota').is_disabled()
            assert w.result['report']['model_sha256']==model
            with page.expect_download() as download:page.locator('#download-firmware').evaluate('(el)=>el.click()')
            assert download.value.suggested_filename=='MugglesWand-gestures-app.bin'
            page.locator('#connection-settings').click();page.locator('#connection-advanced').evaluate('(el)=>el.open=true');page.locator('#host').fill('192.168.4.1');page.locator('#connection-done').click()
            def simulated_install(host,data,progress):
                assert host=='192.168.4.1' and data==w.firmware_path().read_bytes()
                progress('uploading',50,'正在发送测试固件');time.sleep(.5)
                progress('complete',100,'设备已确认更新，正在重启。')
            with patch('app.install_ota',simulated_install):
                page.locator('#ota').click();page.locator('#confirm-cancel').click()
                assert app.ota['phase']=='idle'
                page.locator('#ota').click();page.locator('#confirm-ok').click()
                page.wait_for_function("document.getElementById('ota-progress').value===100")
            # Screens use isolated software fixtures, not user results.
            for width,height,name in [(1440,1000,'desktop'),(768,1024,'tablet'),(390,844,'mobile'),(320,740,'small')]:
                page.set_viewport_size({'width':width,'height':height})
                for stage in [1,2,3,4]:
                    page.locator(f'[data-step="{stage}"]').click()
                    page.wait_for_timeout(80)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,stage)
                    assert page.locator(f'#stage-{stage}').is_visible()
                    if width!=320:page.screenshot(path=str(args.output/f'{name}-step{stage}.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();app.close()
        for listener in listeners:listener.close()
print('PASS: 120 unique gate, collection, four-stage navigation, model verification, independent live inference, device settings link, firmware without desktop bindings, download, simulated OTA, responsive layouts; no JS errors.')
