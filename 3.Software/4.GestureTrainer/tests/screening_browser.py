"""Real local API and simulated TCP device; no physical wand changes."""
import argparse
import io
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Trainer, Server
from test_screening import seed_candidates
from alphabet import CANDIDATES
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--browser', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    app = Trainer(Path(directory))
    seed_candidates(app.screening)
    server = Server(('127.0.0.1', 0), app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0));listener.listen();listener.settimeout(10)
    commands=[]
    def wand():
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            commands.append(conn.recv(64))
            conn.sendall(b'0,0,1\n'*100)
            time.sleep(.6)
            conn.sendall(b'3,2,2\n-3,-2,3\n3,2,3\n0,0,4\n0,0,4\n')
            commands.append(conn.recv(64))
    threading.Thread(target=wand, daemon=True).start()
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.get_by_role('link',name='候选筛选工具',exact=True).click()
            page.wait_for_function("document.querySelector('#candidate').options.length===23")
            assert page.locator('#group').is_enabled()
            for candidate in range(23):
                page.locator('#candidate').select_option(str(candidate))
                assert page.locator('#route').evaluate('(el)=>el.getTotalLength()>0')
                assert page.locator('#title').inner_text()==CANDIDATES[candidate][0]
            assert page.locator('#next').is_disabled()
            page.locator('#candidate').select_option('8')
            assert not page.locator('#transfer').get_attribute('d')
            page.locator('#candidate').select_option('4')
            assert page.locator('#title').inner_text()=='小写 e'
            assert '捕捉到 10 次' in page.locator('#counts').inner_text()
            page.locator('#analyze').click()
            page.locator('#report').wait_for(state='visible')
            assert page.locator('#selection span').count()==10
            assert '不是训练完成后的识别准确率' in page.locator('#score').inner_text()
            page.get_by_text('连接设置与重新录制',exact=True).click()
            page.once('dialog',lambda d:d.dismiss())
            page.locator('#reset').click()
            assert len(app.screening.records(4))==10
            page.locator('#candidate').select_option('19')
            page.locator('#host').fill('127.0.0.1')
            page.locator('#port').fill(str(listener.getsockname()[1]))
            page.locator('#once').click()
            page.wait_for_function("document.getElementById('counts').textContent.includes('捕捉到 1 次')")
            assert commands==[b'imu\n',b'inference\n']
            assert page.locator('#report').is_hidden()
            assert len(app.screening.records(19))==1
            page.locator('#analyze').click()
            page.locator('#report').wait_for(state='visible')
            with page.expect_download() as download:
                page.locator('#export').click()
            archive=zipfile.ZipFile(download.value.path())
            assert '19.jsonl' in archive.namelist() and 'analysis.json' in archive.namelist()
            page.locator('#candidate').select_option('4')
            page.screenshot(path=str(args.output/'desktop.png'),full_page=True)
            for width in [768,390,320]:
                page.set_viewport_size({'width':width,'height':900})
                page.wait_for_timeout(100)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                assert page.locator('#group').is_visible()
                assert page.locator('#catalog').is_hidden()
                page.screenshot(path=str(args.output/f'{width}.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();app.close();listener.close()
print('PASS: screening navigation, 23 continuous letter and symbol routes, no transfer strokes, real analysis, confirmation, TCP trial, invalidation, ZIP export, 1440/768/390/320 layouts; no JS errors.')
