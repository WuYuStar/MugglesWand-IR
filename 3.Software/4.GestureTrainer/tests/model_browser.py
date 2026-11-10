"""Actual desktop HTTP flow, isolated data/model copies and a simulated model peer."""
import argparse,hashlib,json,shutil,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,Server,find_seed
from workflow import atomic_json
from model_package import decode
import model_transfer
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
entered=threading.Event();release=threading.Event();calls=[];errors=[];fail=[False]
def simulated_upload(host,data,progress):
 calls.append(decode(data));entered.set()
 if fail[0]:raise ValueError('当前魔杖还不支持独立模型更新，请先安装一次6.0基础固件。')
 progress('uploading',50,'正在上传手势模型');release.wait(20);progress('complete',100,'新模型已生效，可以脱离电脑使用魔杖')
model_transfer.upload=simulated_upload
with tempfile.TemporaryDirectory() as folder:
 app=Trainer(Path(folder),template=Path(folder)/'no-firmware-template');w=app.workflow
 for i in range(8):shutil.copy2(find_seed()/f'{i}.csv',w.data/f'{i}.csv')
 run=w.root/'runs/fixture';run.mkdir(parents=True)
 shutil.copy2(args.run/'gesture_model.tflite',run/'gesture_model.tflite')
 report=json.loads((args.run/'report.json').read_text(encoding='utf-8'));report['data_fingerprint']=w._stats()['fingerprint'];atomic_json(run/'report.json',report);w.result=dict(run='fixture',report=report)
 before={p.name:p.read_bytes() for p in w.data.glob('*.csv')}
 server=Server(('127.0.0.1',0),app);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1000));page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(url+'/#step4');page.wait_for_function('initialized')
   assert page.locator('#page-title').inner_text()=='上传模型'
   assert page.locator('#upload-model-button').is_enabled()
   assert page.locator('#build').count()==0 and page.locator('#ota').count()==0
   page.locator('#upload-model-button').click();assert entered.wait(3)
   page.wait_for_function('state.model_upload.running');assert page.locator('#upload-model-button').is_disabled()
   release.set();page.wait_for_function("state.model_upload.phase==='complete'")
   assert page.locator('#model-device-link').is_visible()
   fail[0]=True;page.locator('#upload-model-button').click();page.wait_for_function("state.model_upload.phase==='error'")
   assert '6.0' in page.locator('#upload-message').inner_text()
   for width in [1440,768,390,320]:
    page.set_viewport_size(dict(width=width,height=900));page.screenshot(path=str(args.output/f'upload-{width}.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   page.locator('[data-step="1"]').click();page.locator('#add-gesture').click();page.locator('#gesture-name').fill('z');page.locator('#add-save').click();page.wait_for_function('state.gestures.length===9')
   page.locator('[data-step="4"]').click();assert page.locator('#upload-model-button').is_hidden();assert page.locator('#upload-prerequisite').is_visible()
   assert all((w.data/name).read_bytes()==data for name,data in before.items());assert not (run/'firmware_project').exists()
   assert not errors,errors;browser.close()
  print(json.dumps(dict(model_only=True,has_firmware_controls=False,upload=True,activation=True,old_firmware_error=True,stale_gate=True,source_data_unchanged=True,widths=[1440,768,390,320],javascript_errors=errors)))
 finally:release.set();server.shutdown();server.server_close();app.close()
