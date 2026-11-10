"""Native Windows window around the local trainer; no browser or Python setup."""
import argparse
import ctypes
import errno
import hashlib
import json
import logging
from pathlib import Path
import sys
import threading
import time
import traceback
from startup import default_workspace, find_running, remember_server

TITLE='魔杖手势训练'


class DesktopSession:
    def __init__(self,workspace,port=8765):
        self.workspace=Path(workspace)
        self.port=port
        self.server=None
        self.trainer=None
        self.thread=None
        self.url=None

    def start(self):
        self.url=find_running(self.port,self.workspace)
        if self.url:return self.url
        from app import Server,Trainer
        for port in (self.port,0):
            try:
                self.server=Server(('127.0.0.1',port),None);break
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE,errno.EACCES) and getattr(exc,'winerror',None) not in (10048,10013):raise
                self.url=find_running(self.port,self.workspace)
                if self.url:return self.url
        if self.server is None:raise RuntimeError('暂时无法启动，请稍后重试。')
        try:
            self.trainer=Trainer(self.workspace)
            self.server.trainer=self.trainer
            remember_server(self.trainer.workspace,self.server.server_port)
            self.url=f'http://127.0.0.1:{self.server.server_port}'
            self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
            self.thread.start()
            return self.url
        except Exception:
            self.close();raise

    def task(self):
        if not self.trainer:return None
        with self.trainer.lock:
            if self.trainer.ota['running']:return self.trainer.ota.get('kind','ota')
            try:self.trainer.idle()
            except ValueError:return 'task'
        return None

    def close(self):
        if self.trainer:self.trainer.close()
        if self.server:
            if self.thread:self.server.shutdown()
            self.server.server_close()
        if self.thread:self.thread.join(timeout=3)
        self.trainer=self.server=self.thread=None


def native_window(title):
    # Windows 11 creates Explorer-owned tab proxy windows with the same title.
    # Focus only our real WinForms window, never the shell's matching proxy.
    user=ctypes.WinDLL('user32')
    callback_type=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
    user.GetClassNameW.argtypes=user.GetWindowTextW.argtypes
    user.EnumWindows.argtypes=[callback_type,ctypes.c_void_p]
    found=[]
    @callback_type
    def visit(handle,unused):
        text=ctypes.create_unicode_buffer(512);name=ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(handle,text,len(text));user.GetClassNameW(handle,name,len(name))
        if text.value==title and name.value.startswith('WindowsForms10.Window'):
            found.append(handle);return False
        return True
    user.EnumWindows(visit,None)
    return found[0] if found else None


def single_instance(workspace,title):
    """Hold a workspace-specific Windows mutex for the lifetime of the window."""
    if sys.platform!='win32':return None,True
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    name='Local\\MugglesWandTrainer-'+hashlib.sha256(str(workspace.resolve()).casefold().encode()).hexdigest()[:24]
    ctypes.set_last_error(0)
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()!=183:return handle,True
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int];user.SetForegroundWindow.argtypes=[ctypes.c_void_p]
    for _ in range(50):
        window=native_window(title)
        if window:
            user.ShowWindow(window,9);user.SetForegroundWindow(window);break
        time.sleep(.1)
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)
    return None,False


def main(argv=None):
    parser=argparse.ArgumentParser(description=TITLE)
    parser.add_argument('--workspace',type=Path,default=default_workspace())
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--smoke-output',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    title=TITLE+(' · 测试' if args.smoke_output else '')
    handle,first=single_instance(args.workspace,title)
    if not first:return 0
    session=DesktopSession(args.workspace,args.port)
    args.workspace.mkdir(parents=True,exist_ok=True)
    log=(args.workspace/'desktop.log').open('a',encoding='utf-8',buffering=1)
    # A GUI launch has no terminal. Keep all diagnostic output outside the app folder.
    original_stdout,original_stderr=sys.stdout,sys.stderr
    sys.stdout=sys.stderr=log
    try:
        if sys.platform=='win32':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MugglesWand.Trainer.1')
        import webview
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['ALLOW_FILE_URLS']=False
        url=session.start()
        screen=webview.screens[0]
        width=max(640,min(1280,screen.width-64));height=max(480,min(900,screen.height-96))
        window=webview.create_window(title,url,width=width,height=height,min_size=(min(840,width),min(620,height)),background_color='#f7f7f7',text_select=True)

        def closing():
            task=session.task()
            if task in ('ota','model'):
                window.create_confirmation_dialog('模型正在更新' if task=='model' else '魔杖正在升级','请等待更新完成后再关闭软件。')
                return False
            if task:
                return window.create_confirmation_dialog('任务仍在进行','退出会停止当前任务，已保存的记录会保留。确定退出？')
            return True
        window.events.closing+=closing

        def smoke():
            if not args.smoke_output:return
            result={}
            try:
                deadline=time.monotonic()+35
                while time.monotonic()<deadline:
                    result=window.evaluate_js("({ready:typeof initialized!=='undefined'&&initialized, title:document.title, counts:typeof state!=='undefined'&&state?state.counts:null, training:typeof state!=='undefined'&&state?state.training_available:null, model_protocol:typeof state!=='undefined'&&state?state.model_protocol:null, add:!!document.getElementById('add-gesture'), check:!!document.getElementById('scan-quality')})")
                    if result.get('ready'):break
                    time.sleep(.2)
                result.update(url=url,owned_backend=session.trainer is not None,python=sys.executable)
                if sys.platform=='win32':
                    try:
                        from PIL import ImageGrab
                        ImageGrab.grab(window=window.native.Handle.ToInt64()).save(args.smoke_output.with_suffix('.png'))
                    except Exception:traceback.print_exc()
                args.smoke_output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception as exc:
                args.smoke_output.write_text(json.dumps(dict(error=str(exc))),encoding='utf-8')
            finally:window.destroy()
        window.events.loaded+=smoke
        icon=Path(__file__).parent/'desktop'/'wand.ico'
        webview.start(gui='edgechromium',debug=False,private_mode=False,storage_path=str(args.workspace/'window-cache'),icon=str(icon) if icon.exists() else None)
        return 0
    except Exception:
        traceback.print_exc()
        if args.smoke_output:
            args.smoke_output.write_text(json.dumps(dict(error=traceback.format_exc()),ensure_ascii=False),encoding='utf-8')
        elif sys.platform=='win32':
            ctypes.windll.user32.MessageBoxW(None,'软件未能打开。请查看数据目录中的 desktop.log。\n\n如提示缺少 WebView2，请运行“显示组件安装.exe”后重试。',TITLE,0x10)
        return 1
    finally:
        session.close()
        sys.stdout,sys.stderr=original_stdout,original_stderr
        log.close()
        if handle:
            kernel=ctypes.WinDLL('kernel32');kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)


if __name__=='__main__':
    raise SystemExit(main())
