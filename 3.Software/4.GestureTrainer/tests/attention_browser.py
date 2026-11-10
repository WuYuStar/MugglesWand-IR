"""UI attention/state transitions with explicit simulated API jobs, no hardware.
Actual API, file, TCP and inference regression tests run separately.
"""
import argparse,copy,json,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as folder:
 app=Trainer(Path(folder));base=app.workflow.state();app.close()
base['training_available']=True;base['build_available']=True
html=Path(__file__).resolve().parents[1].joinpath('index.html').read_text().replace('__TOKEN__','fixture-token')
model=dict(run='20261105-120000-fixture',report=dict(accuracy=.95,trigger_accuracy=.9))
state=copy.deepcopy(base);calls=[];connected=[False];errors=[]
def route(r):
 global state
 path=urlsplit(r.request.url).path
 if path=='/':return r.fulfill(body=html,content_type='text/html; charset=utf-8')
 if path=='/api/workflow/state':return r.fulfill(json=state)
 if r.request.method=='POST':
  data=r.request.post_data_json;calls.append((path,data))
  if path.endswith('/collect/start'):state['session'].update(running=True,connected=True,kind='collecting',label=data['label'],message='准备好了，现在画')
  elif path.endswith('/test/start'):state['session'].update(running=True,connected=True,kind='testing',label=data['expected'],message='准备好了，现在画')
  elif path.endswith('/stop'):state['session'].update(running=False,connected=False,message='已结束')
  elif path.endswith('/train'):state['job'].update(running=True,kind='gesture-training',phase='training',epoch=1,total=60)
  elif path.endswith('/build'):state['job'].update(running=True,kind='gesture-building',phase='building')
  elif path=='/api/cancel':state['job'].update(running=False,phase='cancelled',message='已取消')
  elif path.endswith('/ota'):state['ota'].update(running=True,phase='uploading',percent=50,message='正在发送')
  elif path.endswith('/connect/check'):
   state['connection']=dict(status='reachable' if connected[0] else 'unreachable',host=data['host'],port=data['port'],checked_at=time.time())
   return r.fulfill(json=dict(connection=state['connection'],message='连接检测正常' if connected[0] else '请连接魔杖Wi-Fi后重试。'))
  return r.fulfill(json=dict(message='操作已执行'))
 return r.fulfill(status=404,json=dict(error='fixture endpoint missing'))
def set_state(page,**values):
 state.update(values);page.evaluate('refresh()');page.wait_for_timeout(60)
def load(page,prepared):
 global state
 state=copy.deepcopy(prepared);page.goto('http://studio.test/');page.wait_for_function('initialized')
def primary(page):return page.locator('button.primary:visible,a.primary:visible')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
 page=browser.new_page(viewport=dict(width=1440,height=1000));page.route('http://studio.test/**',route);page.on('pageerror',lambda e:errors.append(str(e)))
 load(page,base)
 assert page.locator('#page-title').inner_text()=='录制手势'
 assert primary(page).count()==1 and primary(page).inner_text()=='录制 a'
 assert not page.locator('#epochs').is_visible() and not page.locator('#probabilities').is_visible()
 # Unverified connection is never presented as connected.
 assert page.locator('#connection-settings').inner_text()=='连接魔杖'
 page.locator('#connection-settings').click();assert not page.locator('#host').is_visible()
 page.locator('#check-connection').click();page.wait_for_function("document.getElementById('connection-state').textContent.includes('重试')")
 assert page.locator('#connection-settings').inner_text()=='检查连接'
 connected[0]=True;page.locator('#check-connection').click();page.wait_for_function("document.getElementById('connection-state').textContent==='连接检测正常'")
 page.locator('#connection-done').click()
 page.locator('#collect').click();page.wait_for_function("!document.getElementById('stop').hidden")
 assert page.locator('#connection-settings').inner_text()=='魔杖已连接'
 page.locator('#stop').click()
 full=copy.deepcopy(base);full.update(counts=[120]*8,ready_to_train=True)
 load(page,full);assert page.locator('#page-title').inner_text()=='训练'
 assert primary(page).count()==1 and primary(page).inner_text()=='开始训练'
 page.locator('#train').click();page.wait_for_function("!document.getElementById('cancel-job').hidden")
 assert page.locator('#training-title').inner_text()=='正在学习你的手势'
 assert page.locator('#training-progress').is_visible()
 page.locator('#cancel-job').click();page.wait_for_function("document.getElementById('training-title').textContent==='准备训练'")
 page.locator('#train').click()
 set_state(page,result=model,model_fresh=True,job=dict(running=False,kind='gesture-training',phase='complete',epoch=12,total=60,message='训练完成'))
 assert primary(page).count()==1 and primary(page).inner_text()=='测试识别'
 assert page.locator('#train').is_hidden() and page.locator('#training-title').inner_text()=='训练完成'
 page.locator('#train-next').click()
 assert page.locator('#page-title').inner_text()=='测试识别'
 assert page.locator('#expected').input_value()=='0'
 assert primary(page).count()==1 and primary(page).inner_text()=='开始测试 a'
 assert not page.locator('#confidence').is_visible() and not page.locator('#test-history').is_visible()
 page.locator('#test-start').click();page.wait_for_function("!document.getElementById('test-stop').hidden")
 v=copy.deepcopy(state['validation']);v.update(total=10,per_gesture=[10,0,0,0,0,0,0,0],classification_accuracy=.9,trigger_accuracy=.8,latest=dict(gesture='a',id=0,expected=0,accepted=True,correct=True,confidence=.9,probabilities=[.9,.1,0,0,0,0,0,0]))
 set_state(page,validation=v)
 assert page.locator('#prediction').inner_text()=='a'
 assert primary(page).count()==1 and primary(page).inner_text()=='测试 e'
 page.locator('#test-next').click();page.wait_for_function("document.getElementById('expected').value==='1'")
 assert calls[-1][1]['expected']==1 and page.locator('#prediction').inner_text()=='等待挥动'
 page.locator('#test-stop').click();page.locator('#expected').select_option('');page.locator('#test-start').click()
 assert calls[-1][1]['expected'] is None
 assert page.locator('#current-test-count').inner_text()=='自由识别'
 page.locator('#test-stop').click()
 # Stale model must offer retraining and prevent installation, with no long prose.
 stale=copy.deepcopy(full);stale.update(result=model,model_fresh=False,firmware_ready=False)
 load(page,stale);assert page.locator('#page-title').inner_text()=='训练'
 assert page.locator('#train').inner_text()=='更新模型'
 page.locator('[data-step="4"]').click();assert primary(page).inner_text()=='先完成训练'
 prepared=copy.deepcopy(full);prepared.update(result=model,model_fresh=True,firmware_ready=False)
 load(page,prepared);assert page.locator('#page-title').inner_text()=='测试识别'
 page.locator('[data-step="4"]').click();assert primary(page).inner_text()=='准备安装包'
 page.locator('#build').click();page.wait_for_function("!document.getElementById('cancel-build').hidden")
 set_state(page,job=dict(running=False,kind='gesture-building',phase='complete',message='已准备'),firmware_ready=True)
 assert primary(page).count()==1 and primary(page).inner_text()=='安装到魔杖'
 page.locator('#ota').click();page.locator('#confirm-cancel').click();assert not any(c[0].endswith('/ota') for c in calls)
 page.locator('#ota').click();page.locator('#confirm-ok').click();page.wait_for_function("document.getElementById('ota-progress').value===50")
 assert page.locator('#ota').is_hidden()
 set_state(page,ota=dict(running=False,phase='complete',percent=100,message='设备已确认'))
 assert primary(page).count()==1 and '设置手势功能' in primary(page).inner_text()
 assert page.locator('#step4-state').inner_text()=='已发送'
 # Completed review is available in details but does not compete with the task.
 state['quality']=dict(available=True,applied=True,complete=True,report_id='fixture',priority_count=8,review_count=7,missing=[0]*8,missing_total=0,can_apply=False,source_changed=False,backup='fixture backup')
 page.locator('[data-step="1"]').click();assert page.locator('#quality-panel').is_hidden()
 assert not page.locator('#open-quality').is_visible()
 for width,height,name in [(1440,1000,'desktop'),(768,1024,'tablet'),(390,844,'mobile'),(320,740,'small')]:
  page.set_viewport_size(dict(width=width,height=height))
  for stage in [1,2,3,4]:
   page.locator(f'[data-step="{stage}"]').click()
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,stage)
   if width!=320:page.screenshot(path=str(args.output/f'{name}-{stage}.png'),full_page=True)
 assert not errors,errors
 browser.close()
print('PASS: next-action routing, one primary task per state, collapsed technical detail, connection truth, record/train/test/install transitions, targeted/free test, stale-model gating, OTA confirmation and acknowledged handoff, completed-review cleanup, 1440/768/390/320 widths; no JavaScript errors. Jobs/device I/O are simulated in this UI test.')
