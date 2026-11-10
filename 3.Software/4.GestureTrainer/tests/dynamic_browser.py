"""Actual HTTP/quality worker in a throwaway workspace; no device connection."""
import argparse,csv,json,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,Server,find_seed
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as folder:
 app=Trainer(Path(folder));w=app.workflow
 for i in range(8):
  with (find_seed()/f'{i}.csv').open() as f:rows=list(csv.reader(f))[:10]
  with (w.data/f'{i}.csv').open('w',newline='') as f:csv.writer(f).writerows(rows)
 original={p.name:p.read_bytes() for p in w.data.glob('*.csv')}
 server=Server(('127.0.0.1',0),app);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
 errors=[]
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1000));page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(url+'/#step1');page.wait_for_function('initialized')
   page.locator('#add-gesture').click();page.locator('#gesture-name').fill('z');page.locator('#gesture-note').fill('从左上角起笔');page.locator('#add-save').click()
   page.wait_for_function("state.gestures.length===9 && document.querySelector('#selected-letter').textContent==='z'")
   assert page.locator('.gesture-choice').count()==9 and page.locator('#collect').inner_text()=='录制 z'
   assert page.locator('#route').get_attribute('d')
   page.locator('#add-gesture').click();page.locator('#gesture-name').fill('z');page.locator('#add-save').click();page.wait_for_function("!document.getElementById('add-error').hidden")
   page.locator('#add-cancel').click();assert len(w.gestures)==9
   page.locator('#add-gesture').click();page.locator('#gesture-name').fill('自选动作');page.locator('#add-save').click()
   page.wait_for_function("state.gestures.length===10 && document.querySelector('#selected-letter').textContent==='自选动作'")
   assert not page.locator('svg.route').is_visible()
   page.evaluate('selectGesture(8)')
   page.locator('#scan-quality').click();page.wait_for_function("state.job.kind==='gesture-quality'&&!state.job.running",timeout=30000)
   assert w.owner.job['phase']=='complete',w.owner.job
   assert 'z' in w.quality.state(w._stats()['counts'])['skipped']
   page.locator('summary').filter(has_text='记录管理').click();page.locator('#open-quality').click();page.wait_for_function("document.getElementById('quality-dialog').open")
   page.locator('#quality-close').click()
   for width in [1440,768,390,320]:
    page.set_viewport_size(dict(width=width,height=1000));page.screenshot(path=str(args.output/f'{width}.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
   browser.close()
  assert not errors,errors
  for name,data in original.items():assert (w.data/name).read_bytes()==data,name
  assert w.gestures==['a','e','n','o','p','s','8','∞','z','自选动作']
  print(json.dumps(dict(browser_errors=errors,added=True,duplicate_rejected=True,quality_scan=True,source_unchanged=True,widths=[1440,768,390,320])))
 finally:server.shutdown();server.server_close();app.close()
