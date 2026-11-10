"""Local desktop trainer. Runs only on 127.0.0.1; the browser is its interface."""
from __future__ import annotations
import argparse
from collections import deque
import csv
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import errno
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from core import ACTIONS, WINDOW, backup, dataset_counts, read_samples, stamp
from ota import application_bytes, device_address, install as install_ota
from screening import Screening
from workflow import Workflow
from startup import SERVICE, default_workspace, find_running, running_at, remember_server

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

def find_template():
    bundled = HERE / 'firmware_template'
    return bundled if bundled.is_dir() else REPO / '2.Firmware/MugglesWand-src'

def find_seed():
    bundled = HERE / 'default_data'
    return bundled if bundled.is_dir() else HERE.parent / '1.OriginalData/8分类手势数据集'

class Trainer:
    def __init__(self, workspace: Path, template=None, seed=None):
        self.workspace = workspace.resolve()
        self.data = self.workspace / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.template = Path(template or find_template()).resolve()
        self.seed = Path(seed or find_seed()).resolve()
        self.lock = threading.RLock()
        self.screening = Screening(self.workspace, self.lock)
        self.collect_stop = threading.Event()
        self.collect_thread = None
        self.sock = None
        self.collect = dict(running=False, label=None, message='连接魔杖 Wi-Fi 后，选择功能开始采集。', saved=0)
        self.job = dict(running=False, phase='idle', message='准备好样本后，就可以开始训练。', epoch=0, total=60)
        self.logs = deque(maxlen=100)
        self.process = None
        self.ota = dict(running=False, phase='idle', percent=0, message='生成升级固件后，可无线安装到魔杖。')
        self.result = None
        self.connection = dict(status='unchecked',host='',port=8080,checked_at=0)
        latest = self.workspace / 'latest.json'
        if latest.is_file():
            try:
                record = json.loads(latest.read_text(encoding='utf-8'))
                run = self.workspace / 'runs' / record['run']
                if run.parent == self.workspace / 'runs' and (run / 'report.json').is_file():
                    self.result = dict(run=run.name, report=json.loads((run / 'report.json').read_text(encoding='utf-8')))
            except (ValueError, KeyError, OSError):
                pass
        self.workflow = Workflow(self)

    def state(self):
        with self.lock:
            result = dict(self.result) if self.result else None
            if result:
                result['firmware_ready'] = (self.workspace / 'runs' / result['run'] / 'MugglesWand-custom-app.bin').is_file()
            return dict(actions=ACTIONS, counts=dataset_counts(self.data), collect=dict(self.collect), job=dict(self.job),
                        result=result, ota=dict(self.ota), screening_running=self.screening.session['running'], workflow_running=self.workflow.session['running'], workspace=str(self.workspace), logs=list(self.logs),
                        training_available=importlib.util.find_spec('tensorflow') is not None,
                        build_available=importlib.util.find_spec('platformio') is not None)

    def idle(self):
        if self.job['running'] or self.collect['running'] or self.ota['running'] or self.screening.session['running'] or self.workflow.session['running']:
            raise ValueError('请先结束当前采集或任务，再进行这项操作。')

    def check_connection(self, host, port):
        try:
            address=ipaddress.IPv4Address(host)
            if not address.is_private or address.is_unspecified or address.is_multicast:raise ValueError()
            if type(port) is not int or not 1<=port<=65535:raise ValueError()
        except (ValueError,TypeError):
            raise ValueError('请填写魔杖的局域网地址和有效端口。') from None
        with self.lock:self.idle()
        # Reachability check only: send no mode-changing command to the wand.
        try:
            with socket.create_connection((str(address),port),timeout=2):pass
            status='reachable'
        except OSError:
            status='unreachable'
        with self.lock:
            self.connection=dict(status=status,host=str(address),port=port,checked_at=time.time())
        return dict(connection=dict(self.connection),message='连接检测正常' if status=='reachable' else '请连接魔杖Wi-Fi后重试。')

    def seed_data(self):
        with self.lock:
            self.idle()
            imported = []
            for label in range(8):
                dest = self.data / f'{label}.csv'
                if not dest.exists() or dest.stat().st_size == 0:
                    shutil.copy2(self.seed / f'{label}.csv', dest)
                    imported.append(ACTIONS[label])
            return dict(message='已补充官方示例样本：' + '、'.join(imported) if imported else '已有样本均已保留，没有覆盖。')

    def reset_label(self, label):
        if type(label) is not int or not 0 <= label < 8:
            raise ValueError('请选择一个有效的功能。')
        with self.lock:
            self.idle()
            path = self.data / f'{label}.csv'
            backup(path, self.workspace / 'backups')
            path.write_text('', encoding='utf-8')
        return dict(message=f'已备份并清空“{ACTIONS[label]}”，现在可以录入你自己的动作。')

    def start_collect(self, label, host, port):
        if type(label) is not int or not 0 <= label < 8:
            raise ValueError('请选择一个有效的功能。')
        if not isinstance(host, str) or not host.strip() or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('魔杖地址或端口填写不正确。')
        with self.lock:
            self.idle()
            self.collect_stop.clear()
            self.collect = dict(running=True, label=label, saved=0, message='正在连接魔杖…')
            self.collect_thread = threading.Thread(target=self._collect, args=(label, host.strip(), port), daemon=True)
            self.collect_thread.start()
        return dict(message='正在连接魔杖，请先停稳。')

    def _collect(self, label, host, port):
        buffer = deque(maxlen=WINDOW)
        connection = None
        final = '采集已结束，已请求恢复正常施法。'
        try:
            connection = socket.create_connection((host, port), timeout=5)
            connection.settimeout(.5)
            with self.lock:
                self.sock = connection
            connection.sendall(b'imu\n')
            pending = b''
            last_data = time.monotonic()
            last_state = None
            feedback_until = 0.0
            while not self.collect_stop.is_set():
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    if time.monotonic() - last_data > 5:
                        with self.lock:
                            self.collect['message'] = '没有收到动作数据。请检查连接，或结束后重新采集。'
                    continue
                if not chunk:
                    raise ConnectionError('魔杖连接断开，已保存的样本不会丢失。')
                pending += chunk
                if len(pending) > 65536:
                    raise ValueError('收到的数据过长，请重新连接魔杖。')
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        pieces = line.decode('utf-8').strip().split(',')
                        if len(pieces) != 3:
                            continue
                        a, b, state = map(float, pieces)
                        import math
                        if not all(math.isfinite(v) for v in (a, b, state)) or state not in (0, 1, 2, 3, 4):
                            continue
                    except (ValueError, UnicodeDecodeError):
                        continue
                    last_data = time.monotonic()
                    buffer.append((a, b))
                    with self.lock:
                        if time.monotonic() >= feedback_until:
                            self.collect['message'] = {0:'先停稳魔杖，准备下一次动作。', 1:'准备好了，现在挥动！',
                            2:'正在记录，画完后停稳。', 3:'动作已检测到，请停稳收尾。', 4:'动作完成。'}[int(state)]
                        if state == 4 and last_state != 4:
                            if len(buffer) == WINDOW:
                                row = [label] + [value for pair in buffer for value in pair]
                                with (self.data / f'{label}.csv').open('a', newline='', encoding='utf-8') as file:
                                    csv.writer(file).writerow(row)
                                self.collect['saved'] += 1
                                feedback_until = time.monotonic() + 1.5
                                self.collect['message'] = f'已保存第 {self.collect["saved"]} 次动作！停稳后继续。'
                                buffer.clear()
                            else:
                                self.collect['message'] = '这次记录不足一秒，请停稳后再试一次。'
                    last_state = state
        except Exception as exc:
            final = '采集未完成：' + str(exc)
        finally:
            if connection:
                try:
                    connection.sendall(b'inference\n')
                except OSError:
                    pass
                connection.close()
            with self.lock:
                self.sock = None
                self.collect['running'] = False
                self.collect['message'] = final

    def stop_collect(self):
        self.collect_stop.set()
        thread = self.collect_thread
        if thread:
            thread.join(timeout=6)
        return dict(message='正在结束采集，已保存的数据会保留。')

    def train(self, epochs):
        if epochs not in (2, 10, 30, 60):
            raise ValueError('请选择有效的训练时长。')
        with self.lock:
            self.idle()
            if importlib.util.find_spec('tensorflow') is None:
                raise ValueError('还没有安装训练环境，请使用配套启动程序安装后重试。')
            read_samples(self.data)
            run = self.workspace / 'runs' / stamp()
            run.mkdir(parents=True)
            # Immutable snapshot protects the running job from later dataset changes.
            snapshot = run / 'training_data'
            shutil.copytree(self.data, snapshot)
            self._start_job([sys.executable, str(HERE / 'train_worker.py'), '--data', str(snapshot),
                             '--output', str(run), '--template', str(self.template), '--epochs', str(epochs)],
                            kind='training', run=run, total=epochs)
        return dict(message='训练已开始，你可以在下面查看进度。')

    def _start_job(self, command, kind, run, total=0):
        self.job = dict(running=True, kind=kind, phase=kind, message='正在启动…', epoch=0, total=total)
        self.logs.clear()
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        if kind.endswith('building'):
            # PlatformIO's tool postinstall uses pip; incomplete mirrors can make
            # it remove its own manifest. Scope the official index to this job.
            environment['PIP_INDEX_URL'] = 'https://pypi.org/simple'
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', cwd=str(HERE),
                env=environment,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0,
                start_new_session=(os.name != 'nt'))
        except Exception:
            self.job.update(running=False, phase='error', message='任务无法启动，请查看运行环境。')
            raise
        threading.Thread(target=self._watch_job, args=(self.process, kind, run), daemon=True).start()

    def _watch_job(self, process, kind, run):
        done = None
        with (run / f'{kind}.log').open('w', encoding='utf-8') as logfile:
            for line in process.stdout:
                logfile.write(line)
                logfile.flush()
                line = line.rstrip()
                with self.lock:
                    self.logs.append(line[-1200:])
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                    except ValueError:
                        continue
                    if event.get('type') in ('progress', 'epoch', 'error', 'done'):
                        self.job.update({k: event[k] for k in ('phase', 'message', 'epoch', 'total', 'accuracy', 'val_accuracy') if k in event})
                    if event.get('type') == 'done':
                        done = event.get('result')
        process.stdout.close()
        code = process.wait()
        with self.lock:
            cancelled = self.job.get('cancel_requested', False)
            self.job['running'] = False
            if kind.startswith('gesture-'):
                self.workflow.finished(kind,run,done,code,cancelled)
                return
            if cancelled:
                self.job.update(phase='cancelled', message='任务已取消，样本和之前的模型都已保留。')
            elif code == 0 and kind == 'training' and done and (run / 'report.json').exists():
                self.result = dict(run=run.name, report=done)
                (self.workspace / 'latest.json').write_text(json.dumps(dict(run=run.name)), encoding='utf-8')
                self.job.update(phase='complete', message='训练完成！可以导出模型，或生成升级固件。')
            elif code == 0 and kind == 'building' and (run / 'firmware_project/.pio/build/esp32-s3-fh4r2/firmware.bin').exists():
                shutil.copy2(run / 'firmware_project/.pio/build/esp32-s3-fh4r2/firmware.bin', run / 'MugglesWand-custom-app.bin')
                self.job.update(phase='complete', message='升级固件已生成，可以下载 .bin 文件。')
            else:
                message = self.job.get('message', '')
                self.job.update(phase='error', message=message if message.startswith('训练未完成') else '任务没有完成，请展开详细日志查看原因。')

    def cancel(self):
        with self.lock:
            if self.job['running'] and self.process:
                self.job['cancel_requested'] = True
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    import signal
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        return dict(message='正在取消，请稍候。')

    def build(self):
        with self.lock:
            self.idle()
            if not self.result:
                raise ValueError('请先训练出一个模型。')
            if importlib.util.find_spec('platformio') is None:
                raise ValueError('未安装固件编译环境，请通过配套启动程序安装。')
            run = self.workspace / 'runs' / self.result['run']
            dest = run / 'firmware_project'
            if not dest.exists():
                shutil.copytree(self.template, dest, ignore=shutil.ignore_patterns('.git', '.pio', '.vscode', '__pycache__', 'tools'))
            for name in ('gesture_model.h', 'gesture_inference.cpp'):
                shutil.copy2(run / 'firmware_patch' / name, dest / 'src/Model' / name)
            self._start_job([sys.executable, '-m', 'platformio', 'run', '-d', str(dest)], 'building', run)
        return dict(message='开始生成升级固件，首次编译需要联网下载依赖。')

    def model_archive(self):
        with self.lock:
            if not self.result:
                raise ValueError('还没有完成训练的模型。')
            run = self.workspace / 'runs' / self.result['run']
            import zipfile
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as file:
                for name in ('gesture_model.tflite','report.json','使用说明.txt','gesture-model.wandmodel'):
                    if (run/name).exists():file.write(run/name,name)
            return output.getvalue()

    def update_wand(self, host):
        host = device_address(host)
        with self.lock:
            self.idle()
            if not self.result:
                raise ValueError('请先训练模型并生成升级固件。')
            path = self.workspace / 'runs' / self.result['run'] / 'MugglesWand-custom-app.bin'
            if not path.is_file():
                raise ValueError('请先点击“生成升级固件”。')
            data = application_bytes(path)
            self.ota.update(running=True, phase='connecting', percent=0, message='正在连接魔杖…')
            threading.Thread(target=self._update_wand, args=(host, data), daemon=True).start()
        return dict(message='无线更新已开始，请保持供电和 Wi-Fi 连接。')

    def _update_wand(self, host, data):
        def progress(phase, percent, message):
            with self.lock:
                self.ota.update(phase=phase, percent=percent, message=message)
        try:
            install_ota(host, data, progress)
        except Exception as exc:
            progress('error', self.ota['percent'], str(exc))
        finally:
            with self.lock:
                self.ota['running'] = False

    def close(self):
        self.workflow.stop()
        self.screening.stop()
        self.stop_collect()
        self.cancel()

    def screen_action(self, action, data):
        if action == 'stop':
            return self.screening.stop()
        with self.lock:
            self.idle()
            if action == 'start':
                return self.screening.start(data.get('candidate'), data.get('host', '192.168.4.1'),
                                            data.get('port', 8080), data.get('total', 10))
            if action == 'reset':
                return self.screening.reset(data.get('candidate'))
            if action == 'analyze':
                try:
                    return self.screening.analyze()
                except ImportError:
                    raise ValueError('筛选需要 NumPy，请通过配套启动程序安装运行环境。') from None

class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR can let two listeners claim the same address.
    allow_reuse_address = os.name != 'nt'
    def __init__(self, address, trainer):
        self.trainer = trainer
        self.token = secrets.token_hex(24)
        super().__init__(address, Handler)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def respond(self, code, value, content_type='application/json; charset=utf-8', filename=None):
        data = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def allowed(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')
    def do_GET(self):
        if not self.allowed():
            return self.respond(403, dict(error='请通过本机地址打开工具。'))
        app = self.server.trainer
        try:
            if self.path == '/api/health':
                return self.respond(200,dict(service=SERVICE,workspace=str(app.workspace)))
            if self.path == '/':
                html = (HERE / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', self.server.token)
                return self.respond(200, html.encode(), 'text/html; charset=utf-8')
            if self.path == '/legacy':
                return self.respond(410,dict(error='旧版功能训练入口已归档，请使用手势模型工作台。'))
            if self.path == '/api/workflow/state':
                return self.respond(200,app.workflow.state())
            if self.path == '/api/workflow/quality/details':
                with app.lock:
                    return self.respond(200,app.workflow.quality.details())
            if self.path == '/api/workflow/model.wandmodel':
                with app.lock:return self.respond(200,app.workflow.model_path().read_bytes(),'application/octet-stream','gesture-model.wandmodel')
            if self.path == '/api/workflow/firmware.bin':
                raise ValueError('系统固件请在魔杖网页维护，电脑只上传模型。')
            if self.path == '/api/workflow/report.json':
                with app.lock:
                    if not app.workflow.result:
                        raise ValueError('尚未生成手势模型。')
                    return self.respond(200,json.dumps(app.workflow.result['report'],ensure_ascii=False).encode(),'application/json; charset=utf-8','gesture-report.json')
            if self.path == '/screening':
                html = (HERE / 'screening.html').read_text(encoding='utf-8').replace('__TOKEN__', self.server.token)
                return self.respond(200, html.encode(), 'text/html; charset=utf-8')
            if urlsplit(self.path).path == '/api/screen/state':
                with app.lock:
                    query = parse_qs(urlsplit(self.path).query)
                    candidate = int(query['candidate'][0]) if 'candidate' in query else None
                    result = app.screening.state(candidate)
                    result['other_busy'] = app.collect['running'] or app.job['running'] or app.ota['running'] or app.workflow.session['running']
                    return self.respond(200, result)
            if self.path == '/api/screen/export.zip':
                with app.lock:
                    return self.respond(200, app.screening.archive(), 'application/zip', 'MugglesWand-screening.zip')
            if self.path == '/api/state':
                return self.respond(200, app.state())
            if self.path == '/api/model.zip':
                return self.respond(200, app.model_archive(), 'application/zip', 'MugglesWand-model.zip')
            if self.path == '/api/firmware.bin':
                raise ValueError('系统固件请在魔杖网页维护，电脑只上传模型。')
            return self.respond(404, dict(error='没有找到这个页面。'))
        except (ValueError, OSError) as exc:
            self.respond(400, dict(error=str(exc)))
    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.allowed() or self.headers.get('X-Trainer-Token') != self.server.token or (origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')):
            return self.respond(403, dict(error='请求已过期，请刷新工具页面。'))
        app = self.server.trainer
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 16384:
                raise ValueError('请求过大。')
            data = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('请求格式无效。')
            if self.path in ('/api/seed','/api/reset','/api/collect/start','/api/collect/stop','/api/train'):
                raise ValueError('旧版入口已归档，请使用手势模型工作台。')
            routes = {
                '/api/seed': lambda: app.seed_data(),
                '/api/reset': lambda: app.reset_label(data.get('label')),
                '/api/collect/start': lambda: app.start_collect(data.get('label'), data.get('host', '192.168.4.1'), data.get('port', 8080)),
                '/api/collect/stop': lambda: app.stop_collect(),
                '/api/train': lambda: app.train(data.get('epochs', 60)),
                '/api/cancel': lambda: app.cancel(),
                '/api/build': lambda: app.workflow.build(),
                '/api/ota': lambda: app.workflow.ota(data.get('host', '192.168.4.1')),
                '/api/screen/start': lambda: app.screen_action('start', data),
                '/api/screen/stop': lambda: app.screen_action('stop', data),
                '/api/screen/reset': lambda: app.screen_action('reset', data),
                '/api/screen/analyze': lambda: app.screen_action('analyze', data),
                '/api/workflow/collect/start': lambda: app.workflow.start('collecting',data.get('label'),data.get('host','192.168.4.1'),data.get('port',8080)),
                '/api/workflow/test/start': lambda: app.workflow.start('testing',data.get('expected'),data.get('host','192.168.4.1'),data.get('port',8080)),
                '/api/workflow/stop': lambda: app.workflow.stop(),
                '/api/workflow/gestures/add': lambda: app.workflow.add_gesture(data.get('name'),data.get('instruction','')),
                '/api/workflow/quality/scan': lambda: app.workflow.quality.scan(),
                '/api/workflow/quality/keep': lambda: app.workflow.quality.keep(),
                '/api/workflow/quality/apply': lambda: app.workflow.quality.apply(),
                '/api/workflow/connect/check': lambda: app.check_connection(data.get('host','192.168.4.1'),data.get('port',8080)),
                '/api/workflow/reset': lambda: app.workflow.reset(data.get('label')),
                '/api/workflow/train': lambda: app.workflow.train(data.get('epochs',60)),
                '/api/workflow/bindings': lambda: app.workflow.save_bindings(data.get('bindings')),
                '/api/workflow/model/upload': lambda: app.workflow.upload_model(data.get('host','192.168.4.1')),
                '/api/workflow/build': lambda: app.workflow.build(),
                '/api/workflow/ota': lambda: app.workflow.ota(data.get('host','192.168.4.1')),
            }
            if self.path not in routes:
                return self.respond(404, dict(error='没有找到这个操作。'))
            self.respond(200, routes[self.path]())
        except (ValueError, OSError, KeyError) as exc:
            self.respond(400, dict(error=str(exc)))

def main(argv=None):
    parser = argparse.ArgumentParser(description='魔杖手势训练工具')
    parser.add_argument('--workspace', type=Path, default=default_workspace())
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 1<=args.port<=65535:parser.error('启动地址的端口必须在1到65535之间。')
    existing=find_running(args.port,args.workspace)
    if existing:
        print('训练工具已在运行，正在打开原来的页面：'+existing,flush=True)
        if not args.no_browser:webbrowser.open(existing)
        return 0
    # Reserve a listening address before touching the data. A second launch can
    # race the first one; it must never create another Trainer for the same port.
    server=None
    # Windows may reserve a whole range (including neighbouring ports). Port 0
    # asks the OS for a genuinely available address instead of guessing 8766.
    for port in (args.port,0):
        try:
            server=Server(('127.0.0.1',port),None)
            break
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE,errno.EACCES) and getattr(exc,'winerror',None) not in (10048,10013):raise
            existing=None
            if exc.errno==errno.EADDRINUSE or getattr(exc,'winerror',None)==10048:
                for _ in range(2):
                    existing=running_at(port,args.workspace)
                    if existing:break
                    time.sleep(.1)
            if existing:
                print('训练工具已在运行，正在打开原来的页面：'+existing,flush=True)
                if not args.no_browser:webbrowser.open(existing)
                return 0
    if server is None:raise SystemExit('暂时无法建立本机页面，请稍后重试。无需重新安装Python。')
    try:
        app=Trainer(args.workspace)
        server.trainer=app
        remember_server(app.workspace,server.server_port)
    except Exception:
        server.server_close()
        raise
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'魔杖训练工具已启动：{url}\n数据保存在：{app.workspace}\n关闭此窗口或按 Ctrl+C 结束。', flush=True)
    if server.server_port!=args.port:print('默认地址暂不可用，已自动选择可用地址；下次启动会沿用。',flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()

if __name__ == '__main__':
    main()
