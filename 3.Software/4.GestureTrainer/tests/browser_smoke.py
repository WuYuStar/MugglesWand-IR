"""Real local server + simulated TCP wand + real exported baseline model."""
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Trainer, Server
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--browser', required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--baseline', type=Path, required=True)
args=parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    app=Trainer(Path(directory))
    server=Server(('127.0.0.1',0),app)
    server_thread=threading.Thread(target=server.serve_forever,daemon=True)
    server_thread.start()
    listener=socket.socket()
    listener.bind(('127.0.0.1',0));listener.listen()
    received=[]
    def wand():
        conn,_=listener.accept()
        with conn:
            conn.settimeout(8)
            received.append(conn.recv(64))
            for i in range(110):
                conn.sendall(f'{i/10},{-i/10},{4 if i==109 else 1}\n'.encode())
            received.append(conn.recv(64))
    threading.Thread(target=wand,daemon=True).start()
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1060})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/legacy')
        page.wait_for_function("document.getElementById('action').options.length===8")
        assert page.locator('#train').is_disabled()
        page.locator('#seed').click()
        page.wait_for_function("document.getElementById('sample-count').textContent==='120'")
        assert not page.locator('#train').is_disabled()
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.locator('#reset').click()
        assert app.state()['counts'][5]==120
        page.once('dialog',lambda dialog:dialog.accept())
        page.locator('#reset').click()
        page.wait_for_function("document.getElementById('sample-count').textContent==='0'")
        assert app.state()['counts'][0]==120
        page.get_by_text('连接地址与采集说明',exact=True).click()
        page.locator('#host').fill('127.0.0.1')
        page.locator('#port').fill(str(listener.getsockname()[1]))
        page.locator('#collect').click()
        page.wait_for_function("document.getElementById('sample-count').textContent==='1'")
        assert page.locator('#action').is_disabled()
        page.locator('#stop').click()
        page.wait_for_function("!document.getElementById('collect').hidden")
        assert received==[b'imu\n',b'inference\n']
        assert page.locator('#train').is_disabled()
        page.once('dialog',lambda dialog:dialog.accept())
        page.locator('#reset').click()
        page.wait_for_function("document.getElementById('sample-count').textContent==='0'")
        page.locator('#seed').click()
        page.wait_for_function("document.getElementById('sample-count').textContent==='120'")
        run=app.workspace/'runs'/'baseline'
        shutil.copytree(args.baseline,run)
        with app.lock:
            app.result=dict(run=run.name,report=json.loads((run/'report.json').read_text()))
            app.job.update(phase='complete',message='训练完成！可以导出模型，或生成升级固件。')
        page.locator('#result').wait_for(state='visible')
        assert page.locator('#accuracy').inner_text().endswith('%')
        with page.expect_download() as download:
            page.locator('#download-model').click()
        assert download.value.suggested_filename=='MugglesWand-model.zip'
        # Exercise the local OTA endpoint without touching a physical wand.
        (run/'MugglesWand-custom-app.bin').write_bytes(b'\xe9'+b'\0'*11+b'\x09\0'+b'\0'*30)
        page.locator('#install').evaluate('(el)=>el.open=true')
        page.wait_for_function("!document.getElementById('ota').disabled")
        page.locator('#host').fill('192.168.4.1')
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.locator('#ota').click()
        assert app.state()['ota']['phase']=='idle'
        def simulated_update(host,data,progress):
            assert host=='192.168.4.1'
            progress('uploading',50,'正在发送测试固件')
            time.sleep(2)
            progress('complete',100,'魔杖已校验并接受升级，正在重启。')
        with patch('app.install_ota',simulated_update):
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('#ota').click()
            page.wait_for_function("document.getElementById('ota-message').textContent.includes('正在发送测试固件')")
            assert page.locator('#collect').is_disabled()
            assert page.locator('#build').is_disabled()
            assert page.locator('#ota').is_disabled()
            page.wait_for_function("document.getElementById('ota-progress').value===100")
        page.screenshot(path=str(args.output/'ota-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.locator('.workflow [data-stage-go="3"]').click()
        assert page.locator('#ota').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(args.output/'ota-mobile.png'),full_page=True)
        page.locator('.workflow [data-stage-go="1"]').click()
        page.set_viewport_size({'width':1440,'height':1060})
        page.evaluate("document.querySelectorAll('details').forEach(el=>el.open=false);document.getElementById('toast').hidden=true;window.scrollTo(0,0)")
        page.screenshot(path=str(args.output/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#stage-1').is_visible()
        assert page.locator('#stage-2').is_hidden()
        page.locator('.workflow [data-stage-go="2"]').click()
        assert page.locator('#collect').is_visible()
        assert page.locator('#capture-action').inner_text() == '当前功能：照明魔法'
        page.screenshot(path=str(args.output/'mobile-capture.png'),full_page=True)
        page.locator('.workflow [data-stage-go="3"]').click()
        assert page.locator('#result').is_visible()
        page.screenshot(path=str(args.output/'mobile-training.png'),full_page=True)
        page.locator('.workflow [data-stage-go="1"]').click()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(args.output/'mobile.png'),full_page=True)
        page.set_viewport_size({'width':320,'height':740})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width':768,'height':1024})
        page.wait_for_function("document.getElementById('stage-2').hidden")
        assert page.locator('#action').bounding_box()['width'] >= 500
        page.screenshot(path=str(args.output/'tablet.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1060})
        page.wait_for_function("!document.getElementById('stage-2').hidden && !document.getElementById('stage-3').hidden")
        assert page.locator('#stage-2').is_visible()
        assert page.locator('#stage-3').is_visible()
        assert not errors,errors
        browser.close()
    listener.close()
    server.shutdown();server.server_close();app.close()
print('PASS: real server, seed/reset/backup flow, simulated TCP capture, control locking, model download, desktop/mobile layout; no browser errors.')
