"""Firmware UI with simulated device WebSocket; no physical device commands.
Actual C++ persistence/parsing has a separate native service test.
"""
import argparse
import copy
import re
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--firmware',type=Path)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
firmware=args.firmware or Path(__file__).resolve().parents[3]/'2.Firmware/MugglesWand-src'
html=(firmware/'src/Service/WebPage.h').read_text().split('R"raw(\n',1)[1].rsplit(')raw";',1)[0].encode()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
choices=[dict(id=int(i),name=n,group=g) for i,n,g in re.findall(r'\{(-?\d+),"([^"]+)","([^"]+)"\}',(firmware/'src/Service/GestureEffects.h').read_text())]
state=dict(gestureEffects=[-1]*8,lightEffects=choices,type='status',version='5.1-map1',ssid="Muggles' Wand",password='12345678',bleName="Muggles' Wand",mode=2,bleEnabled=True,bleConnected=False,charging=False,receiver=True,patterns=['MugglesWand','Star','Rainbow'],gestureProfile='legacy-v1',gestureProfileTitle='原版手势模型',gestureLabels=['原手势1 · 关灯','原手势2 · 开灯','原手势3 · 向上','原手势4 · 向下','原手势5 · 鼠标','原手势6 · 照明','原手势7 · 音量','原手势8 · 光绘'],bindings=list(range(8)),bindingRevision=1,bindingsWritable=True)
commands=[];sockets=[];errors=[];hold=[True]
def route(ws):
    sockets.append(ws)
    def message(cmd):
        commands.append(cmd)
        if cmd.startswith('web:bindings '):
            if hold[0]:return
            _,revision,values,*effects=cmd.split();values=list(map(int,values.split(',')))
            if int(revision)!=state['bindingRevision']:
                ws.send(json.dumps(dict(type='bindings',ok=False,message='配置已在其他页面改变，请重新读取后再修改。')))
            else:
                state['bindings']=values;state['gestureEffects']=list(map(int,effects[0].split(','))) if effects else state['gestureEffects'];state['bindingRevision']+=1
                ws.send(json.dumps(dict(type='bindings',ok=True,message='已保存，立即生效，断电后保留。')))
        if cmd.startswith('web:effect '):ws.send(json.dumps(dict(type='effect',ok=True,message='灯效预览已发送，不执行功能。')))
        ws.send(json.dumps(state))
    ws.on_message(message)
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
        page.route_web_socket('**/ws',route)
        page.goto(f'http://127.0.0.1:{server.server_port}/#gesture-settings')
        page.wait_for_function("document.querySelectorAll('.binding-action-field select').length===8")
        assert page.locator('[data-page]:visible').get_attribute('id') == 'gesture-settings'
        assert '原版模型' in page.locator('#binding-profile-note').inner_text()
        assert page.locator('#save-bindings').is_disabled()
        selects=page.locator('.binding-action-field select');fxselects=page.locator('.binding-effect-field select')
        assert selects.nth(0).locator('option[value="2"], option[value="3"]').count()==0
        assert selects.nth(2).input_value()=='-1'
        selects.nth(5).select_option('-1')
        page.get_by_role('link', name='发送指令', exact=True).click()
        page.locator('#gesture-settings').wait_for(state='hidden')
        page.go_back()
        page.locator('#gesture-settings').wait_for()
        page.wait_for_timeout(3200)  # Real periodic status cannot overwrite an edit.
        assert selects.nth(5).input_value()=='-1'
        assert state['bindings'][5]==5
        page.locator('#save-bindings').click()
        assert page.locator('#save-bindings').is_disabled()
        assert '正在保存' in page.locator('#binding-feedback').inner_text()
        sockets[-1].send(json.dumps(dict(type='bindings',ok=False,message='保存失败，原配置保留，请重试。')))
        sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert '保存失败' in page.locator('#binding-feedback').inner_text()
        assert state['bindings'][5]==5
        hold[0]=False;page.locator('#save-bindings').click()
        page.wait_for_function("document.getElementById('binding-feedback').textContent.startsWith('已保存')")
        assert state['bindings'][5]==-1
        page.reload();page.wait_for_function("document.querySelectorAll('.binding-action-field select').length===8")
        assert selects.nth(5).input_value()=='-1'  # Reload uses device state.
        # A genuine letter model switches the labels and its own profile values.
        state.update(gestureProfile='letters-v1',gestureProfileTitle='自定义8手势模型',gestureLabels=['a','e','n','o','p','s','8','∞'],bindings=[-1]*8,bindingRevision=1)
        sockets[-1].send(json.dumps(state));page.wait_for_function("document.querySelector('.binding-gesture').textContent==='a'")
        selects.nth(6).select_option('5');selects.nth(7).select_option('4');page.locator('#save-bindings').click()
        page.wait_for_function("document.getElementById('save-bindings').disabled")
        assert state['bindings']==[-1]*6+[5,4]
        fxselects.nth(0).select_option('5')
        before=len(commands);page.locator('.binding-preview').nth(0).click();page.wait_for_timeout(150)
        assert commands[before:]==['web:effect 5'],commands[before:]
        assert state['gestureEffects'][0]==-1  # Preview never saves or executes a function.
        page.wait_for_timeout(3200);assert fxselects.nth(0).input_value()=='5'
        page.locator('#save-bindings').click();page.wait_for_timeout(100)
        assert state['gestureEffects'][0]==5 and state['bindings'][0]==-1
        page.reload();page.wait_for_function("document.querySelector('.binding-effect-field select')?.value==='5'")
        # Another client changes the configuration after this page starts editing.
        selects.nth(0).select_option('6')
        state['bindings'][0]=7;state['bindingRevision']+=1;sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert selects.nth(0).input_value()=='6'
        page.locator('#save-bindings').click();page.wait_for_timeout(100)
        assert '其他页面' in page.locator('#binding-feedback').inner_text()
        assert state['bindings'][0]==7
        page.locator('#reload-bindings').click();page.wait_for_timeout(100)
        assert selects.nth(0).input_value()=='7'
        # Manual lamp buttons remain action5 even when class5 is disabled.
        page.get_by_role('link', name='发送指令', exact=True).click()
        page.locator('[data-cmd="gesture 5"]').click()
        assert 'gesture 5' in commands
        page.get_by_role('link', name='挥杖练习', exact=True).click();page.locator('#practice-start').click()
        sockets[-1].send(json.dumps(dict(type='recognition',id=5,action=-1,effect=0,accepted=True,confidence=.9)));page.wait_for_timeout(100)
        assert '不执行功能' in page.locator('#practice-detail').inner_text()
        page.locator('#practice-end').click()
        page.get_by_role('link', name='配置', exact=True).click()
        page.get_by_role('link', name='手势与灯效', exact=False).click()
        page.wait_for_timeout(4600)
        for width,height,name in [(1440,1000,'desktop'),(768,1024,'tablet'),(390,844,'mobile'),(320,740,'small')]:
            page.set_viewport_size({'width':width,'height':height});page.locator('#gesture-settings').scroll_into_view_if_needed();page.wait_for_timeout(100)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            if width!=320:
                page.screenshot(path=str(args.output/f'{name}-settings.png'),full_page=True)
                page.locator('#gesture-settings').evaluate('(e)=>e.scrollIntoView({block:"start"})');page.screenshot(path=str(args.output/f'{name}-fold.png'))
        state.update(gestureProfile='g-sixteen-fixture',gestureLabels=['a','e','n','o','p','s','8','∞','z','u','v','w','c','d','l','m'],bindings=[-1]*16,gestureEffects=[25]*16,bindingRevision=1)
        sockets[-1].send(json.dumps(state));page.wait_for_function("document.querySelectorAll('.binding-effect-field select').length===16")
        summaries=page.locator('.binding-summary')
        assert selects.nth(15).is_hidden()
        summaries.nth(0).click()
        assert selects.nth(0).is_visible()
        summaries.nth(15).click()
        assert selects.nth(0).is_hidden() and selects.nth(15).is_visible()
        fxselects.nth(15).select_option('2');selects.nth(15).select_option('7');page.locator('#save-bindings').click();page.wait_for_timeout(100)
        assert state['gestureEffects'][15]==2 and state['bindings'][15]==7
        assert len(next(c for c in reversed(commands) if c.startswith('web:bindings ')))<128
        # Mode2 alone must not claim readiness when the sensor stream stops.
        state.update(imuReady=False,inferenceReady=True,mode=2);sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert '恢复中' in page.locator('#mode').inner_text()
        state['imuReady']=True;sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert '已就绪' in page.locator('#mode').inner_text()
        # Updating the model disables both feature and binding controls.
        assert page.locator('#practice-target option').count()==16
        state['modelUpdating']=True;sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert selects.nth(0).is_disabled() and page.locator('[data-cmd="gesture 5"]').is_disabled()
        assert page.locator('#practice-target').is_disabled()
        state['modelUpdating']=False;sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        # The reboot may happen between polls: success must use the package hash.
        target='ab'*32;posts=[]
        def model_api(route):
            if route.request.method=='POST':
                posts.append(route.request.headers);route.fulfill(status=202,json=dict(accepted=True,ticket=7))
            else:route.fulfill(json=dict(source='stored',gestures=state['gestureLabels'],active_hash=target if posts else 'cd'*32,job=dict(ticket=0,phase='idle')))
        page.route('**/api/model',model_api)
        page.get_by_role('link', name='配置', exact=True).click()
        page.get_by_role('link', name='更新手势模型', exact=False).click()
        page.locator('#model-file').set_input_files(dict(name='test.wandmodel',mimeType='application/octet-stream',buffer=bytes(1120)+bytes.fromhex(target)))
        page.locator('#upload-model-file').click();page.wait_for_function("document.getElementById('model-upload-message').textContent==='新模型已生效'")
        assert len(posts)==1 and posts[0]['x-wand-model']=='1'
        page.get_by_role('link', name='返回配置', exact=True).click()
        page.get_by_role('link', name='手势与灯效', exact=False).click()
        state['bindingsWritable']=False;sockets[-1].send(json.dumps(state));page.wait_for_timeout(100)
        assert selects.nth(0).is_disabled() and page.locator('#save-bindings').is_disabled()
        assert '存储不可用' in page.locator('#binding-feedback').inner_text()
        assert not errors,errors
        browser.close()
finally:server.shutdown();server.server_close()
print('PASS: firmware UI, legacy/letter identity, edit retention, save acknowledgement/failure, reload persistence, conflict/reload, no-action recognition, manual action IDs, storage failure, responsive widths; no JS errors.')
