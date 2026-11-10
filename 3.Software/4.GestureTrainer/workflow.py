"""Four-stage gesture workflow; intentionally isolated from legacy function data."""
from collections import deque
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from core import ACTIONS, backup, read_samples, stamp
from workflow_core import PROFILE, GESTURES, MINIMUM, SCREEN_IDS, dataset_digest, bindings_digest, validate_bindings, prepare_runtime_firmware, RUNTIME_MAPPING, check_report, valid_catalog
from ota import device_address, application_bytes
from alphabet import CANDIDATES, ROUTES
from quality_review import QualityReview


def atomic_json(path, value):
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2),encoding='utf-8')
    temp.replace(path)


class Workflow:
    def __init__(self, owner):
        self.owner = owner
        self.root = owner.workspace/PROFILE
        self.data = self.root/'data'
        self.data.mkdir(parents=True,exist_ok=True)
        catalog_path=self.root/'gestures.json'
        catalog=json.loads(catalog_path.read_text(encoding='utf-8')) if catalog_path.exists() else dict(names=list(GESTURES),instructions=['']*8)
        if not valid_catalog(catalog.get('names')):raise ValueError('手势目录损坏，请恢复备份。')
        self.gestures=catalog['names']
        self.instructions=catalog.get('instructions',['']*len(self.gestures))
        for label in range(len(self.gestures)):
            (self.data/f'{label}.csv').touch(exist_ok=True)
        self.model_transfer=dict(running=False,phase='idle',percent=0,message='')
        self.result = None
        latest = self.root/'latest.json'
        if latest.is_file():
            try:
                name = json.loads(latest.read_text())['run']
                run = self.root/'runs'/name
                if run.parent != self.root/'runs':
                    raise ValueError('无效模型路径')
                report = json.loads((run/'report.json').read_text(encoding='utf-8'))
                check_report(report)
                if hashlib.sha256((run/'gesture_model.tflite').read_bytes()).hexdigest() != report['model_sha256']:
                    raise ValueError('模型校验失败')
                self.result = dict(run=name, report=report)
            except (ValueError,KeyError,OSError):
                pass
        self.session = dict(running=False,connected=False,kind=None,label=None,saved=0,duplicates=0,message='选择手势，开始采集。')
        self.stop_event = threading.Event()
        self.thread = None
        self.predictor = None
        self.trace = []
        self.latest_prediction = None
        self._stats_cache = None
        self.quality = QualityReview(self)
        self.import_message = ''
        if not (self.root/'screening-import.json').exists():
            try:
                self.import_screening()
            except (ValueError,OSError) as exc:
                self.import_message = '已有筛选记录暂未导入：'+str(exc)

    def _stats(self):
        signature = tuple((p.stat().st_mtime_ns,p.stat().st_size) for p in [self.data/f'{i}.csv' for i in range(len(self.gestures))])
        if self._stats_cache and self._stats_cache[0] == signature:
            return self._stats_cache[1]
        samples, labels, counts = read_samples(self.data,minimum=0,names=self.gestures)
        result = dict(counts=counts,fingerprint=dataset_digest(samples,labels),samples=samples,labels=labels)
        self._stats_cache = signature,result
        return result

    def import_screening(self):
        samples,labels,_ = read_samples(self.data,minimum=0,names=self.gestures)
        seen = {tuple(row):label for row,label in zip(samples,labels)}
        imported = [0]*8
        for label,screen_id in enumerate(SCREEN_IDS):
            expected = self.gestures[label]
            if CANDIDATES[screen_id][1] != expected:
                raise ValueError('筛选目录编号不匹配，没有导入。')
            source = self.owner.workspace/'screening/lowercase-one-stroke-v1'/f'{screen_id}.jsonl'
            if not source.exists():
                continue
            additions = []
            for line in source.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                if not row.get('accepted'):
                    continue
                frames = row.get('frames',[])
                if len(frames)!=100 or any(len(f)!=3 for f in frames):
                    continue
                values = tuple(float(v) for f in frames for v in f[:2])
                if not all(math.isfinite(v) for v in values) or values in seen:
                    continue
                seen[values]=label
                additions.append([label,*values])
            if additions:
                with (self.data/f'{label}.csv').open('a',newline='',encoding='utf-8') as file:
                    csv.writer(file).writerows(additions)
                imported[label] = len(additions)
        atomic_json(self.root/'screening-import.json',dict(profile=PROFILE,imported=imported,source_ids=SCREEN_IDS))
        self.import_message = f'已导入 {sum(imported)} 条已筛选的有效记录，继续补足每类120条即可。'
        return dict(message=self.import_message)

    def _run(self):
        if not self.result:
            raise ValueError('请先为每个手势采集120条有效记录，再训练模型。')
        check_report(self.result['report'])
        run=self.root/'runs'/self.result['run']
        if hashlib.sha256((run/'gesture_model.tflite').read_bytes()).hexdigest()!=self.result['report']['model_sha256']:
            raise ValueError('模型文件校验失败，请重新训练或恢复完整模型文件。')
        return run

    def _require_fresh(self):
        run = self._run()
        if (self.result['report'].get('gestures') != self.gestures or self.result['report'].get('data_fingerprint') != self._stats()['fingerprint']):
            raise ValueError('采集数据已变化，请重新训练后再上传模型。')
        return run

    def firmware_path(self):
        run = self._require_fresh()
        manifest = json.loads((run/'firmware-manifest.json').read_text())
        path = run/'MugglesWand-gestures-app.bin'
        if manifest.get('mapping_mode')!=RUNTIME_MAPPING or manifest['model_sha256']!=self.result['report']['model_sha256'] or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['firmware_sha256']:
            raise ValueError('固件版本或模型已变化，请重新生成支持魔杖配置的升级固件。')
        return path

    def validation_state(self):
        records = []
        if self.result:
            path = self._run()/'validation.jsonl'
            if path.exists():
                records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        scored = [r for r in records if r['expected'] is not None and r['counts_for_score']]
        counts = [sum(r['expected']==i for r in scored) for i in range(len(self.result['report']['gestures']) if self.result else len(self.gestures))]
        n = len(scored)
        return dict(total=n,per_gesture=counts,classification_accuracy=sum(r['correct'] for r in scored)/n if n else None,
                    trigger_accuracy=sum(r['correct'] and r['accepted'] for r in scored)/n if n else None,
                    latest=self.latest_prediction or (records[-1] if records else None),history=records[-12:][::-1])

    def state(self):
        with self.owner.lock:
            error = ''
            try:
                stats = self._stats()
            except (ValueError,OSError) as exc:
                error = str(exc)
                stats=dict(counts=[0]*len(self.gestures),fingerprint='')
            fresh = bool(self.result and self.result['report'].get('gestures')==self.gestures and self.result['report'].get('data_fingerprint')==stats['fingerprint'])
            quality=self.quality.state(stats['counts'])
            quality_pending=quality.get('available') and not quality.get('applied')
            return dict(profile=PROFILE,gestures=self.gestures,minimum=MINIMUM,
                        routes=[next((ROUTES[i] for i,c in enumerate(CANDIDATES) if c[1]==g),dict(path='',start=[0,0])) for g in self.gestures],
                        instructions=[self.instructions[j] or next((c[2] for c in CANDIDATES if c[1]==g),'按你设定的动作录制，每次保持相同笔顺。') for j,g in enumerate(self.gestures)],
                        counts=stats['counts'],dataset_error=error,ready_to_train=not error and not quality_pending and min(stats['counts'])>=MINIMUM,
                        quality=quality,
                        import_message=self.import_message,session=dict(self.session),trace=list(self.trace),
                        job=dict(self.owner.job),other_busy=self.owner.collect['running'] or self.owner.screening.session['running'],
                        result=self.result,model_fresh=fresh,model_package_ready=fresh,validation=self.validation_state(),
                        connection=dict(self.owner.connection),
                        ota=dict(self.owner.ota),workspace=str(self.root),
                        training_available=importlib.util.find_spec('tensorflow') is not None,
                        model_protocol=1,model_upload=dict(self.model_transfer),logs=list(self.owner.logs))

    def add_gesture(self,name,instruction=''):
        with self.owner.lock:
            self.owner.idle()
            if not isinstance(name,str) or not isinstance(instruction,str) or len(instruction)>120:raise ValueError('请填写手势名称，动作备注限120字。')
            name=name.strip()
            names=self.gestures+[name]
            if not valid_catalog(names):raise ValueError('名称限1–16字且不能重复，最多16个手势。')
            path=self.data/f'{len(self.gestures)}.csv'
            if path.exists() and path.stat().st_size:raise ValueError('发现同编号原记录，请先恢复手势目录。')
            path.touch(exist_ok=True)
            instructions=self.instructions+[instruction.strip()]
            atomic_json(self.root/'gestures.json',dict(names=names,instructions=instructions))
            self.gestures=names;self.instructions=instructions;self._stats_cache=None
        return dict(label=len(names)-1,message='手势已添加，录满120次后更新模型。')

    def reset(self,label):
        self._label(label)
        with self.owner.lock:
            self.owner.idle()
            backup(self.data/f'{label}.csv',self.root/'backups')
            (self.data/f'{label}.csv').write_text('')
        return dict(message=f'已备份并清空 {self.gestures[label]}，其他手势保留。')

    def _label(self,label,optional=False):
        if optional and label is None:
            return
        if type(label) is not int or not 0<=label<len(self.gestures):
            raise ValueError('请选择有效的手势。')

    def start(self,kind,label,host,port):
        self._label(label,optional=kind=='testing')
        if not isinstance(host,str) or not host.strip() or type(port) is not int or not 1<=port<=65535:
            raise ValueError('请检查魔杖地址和端口。')
        with self.owner.lock:
            self.owner.idle()
            if kind=='testing':
                self._run()
                if label is not None and label>=len(self.result['report']['gestures']):raise ValueError('新增手势还未训练，请先更新模型。')
                if importlib.util.find_spec('tensorflow') is None:
                    raise ValueError('请通过配套启动程序安装识别环境。')
            self.stop_event.clear()
            self.session.update(running=True,connected=False,kind=kind,label=label,saved=0,duplicates=0,message='正在准备连接…')
            self.latest_prediction=None
            self.thread=threading.Thread(target=self._capture,args=(kind,label,host.strip(),port),daemon=True)
            self.thread.start()
        return dict(message='正在连接魔杖，请先停稳。')

    def _capture(self,kind,label,host,port):
        connection=None
        worker=None
        worker_log=None
        final='已结束，已请求魔杖恢复手势识别。'
        try:
            with self.owner.lock:
                stats=self._stats()
                seen={tuple(row):i for row,i in zip(stats['samples'],stats['labels'])}
                if kind=='collecting':seen.update(self.quality.excluded_samples())
                target=(MINIMUM if stats['counts'][label]<MINIMUM else stats['counts'][label]+20) if kind=='collecting' else None
                run=self._run() if kind=='testing' else None
                if kind=='testing':
                    known,known_labels,_=read_samples(run/'training_data',minimum=0,names=self.result['report']['gestures'])
                    seen={tuple(row):i for row,i in zip(known,known_labels)}
            test_seen=set()
            if kind=='testing':
                path=run/'validation.jsonl'
                if path.exists():
                    test_seen={r['sample_sha256'] for r in [json.loads(l) for l in path.read_text().splitlines() if l.strip()]}
                worker_log=(run/'validation-worker.log').open('a',encoding='utf-8')
                worker=subprocess.Popen([sys.executable,str(Path(__file__).with_name('predict_worker.py')),'--run',str(run)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=worker_log,text=True,encoding='utf-8',
                    env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1'),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                with self.owner.lock:
                    self.predictor=worker
                    self.session['message']='正在载入最新模型，稍后即可画手势。'
                ready=json.loads(worker.stdout.readline() or '{}')
                if ready.get('type')!='ready':
                    raise ValueError(ready.get('message','验证模型无法启动。'))
            if self.stop_event.is_set():
                return
            connection=socket.create_connection((host,port),timeout=5)
            connection.settimeout(.3)
            connection.sendall(b'imu\n')
            ring=deque(maxlen=100)
            pending=b''
            last_state=None
            last_data=time.monotonic()
            feedback_until=0
            with self.owner.lock:
                self.session.update(connected=True,message='已连接，停稳后画手势。')
            while not self.stop_event.is_set():
                try:
                    chunk=connection.recv(4096)
                except socket.timeout:
                    if time.monotonic()-last_data>6:
                        raise OSError('没有收到动作数据。请检查魔杖 Wi-Fi 后重试。')
                    continue
                if not chunk:
                    raise OSError('连接已断开，已完成的记录保留。')
                pending+=chunk
                if len(pending)>65536:
                    raise ValueError('收到异常数据，请重新连接。')
                while b'\n' in pending and not self.stop_event.is_set():
                    line,pending=pending.split(b'\n',1)
                    try:
                        values=[float(v) for v in line.decode('utf-8').strip().split(',')]
                        if len(values)!=3 or not all(math.isfinite(v) for v in values) or values[2] not in (0,1,2,3,4):
                            continue
                    except (UnicodeDecodeError,ValueError):
                        continue
                    last_data=time.monotonic()
                    ring.append(values)
                    state=int(values[2])
                    with self.owner.lock:
                        self.trace=list(ring)
                        if time.monotonic()>feedback_until:
                            self.session['message']={0:'先停稳，准备下一次。',1:'准备好了，现在画。',2:'正在记录，画完后停稳。',3:'动作已捕捉，请停稳收尾。',4:'动作完成。'}[state]
                    if state==4 and last_state!=4 and len(ring)==100:
                        features=tuple(v for frame in ring for v in frame[:2])
                        with self.owner.lock:
                            if kind=='collecting':
                                if features in seen:
                                    self.session['duplicates']+=1
                                    self.session['message']='这条记录与已有或已隔离样本完全相同，未重复计数。'
                                else:
                                    with (self.data/f'{label}.csv').open('a',newline='',encoding='utf-8') as file:
                                        csv.writer(file).writerow([label,*features])
                                    seen[features]=label
                                    self.session['saved']+=1
                                    count=self._stats()['counts'][label]
                                    self.session['message']=f'已保存 {self.gestures[label]}，当前 {count} / {MINIMUM} 条。'
                                    if count>=target:
                                        final=f'{self.gestures[label]} 已录到 {count} 条，本组自动结束。'
                                        self.stop_event.set()
                        if kind=='testing':
                            worker.stdin.write(json.dumps(dict(features=features))+'\n');worker.stdin.flush()
                            result=json.loads(worker.stdout.readline() or '{}')
                            if result.get('type')!='prediction':
                                raise ValueError(result.get('message','没有收到模型结果。'))
                            sample_sha=hashlib.sha256(json.dumps(features,separators=(',',':')).encode()).hexdigest()
                            distinct=features not in seen and sample_sha not in test_seen
                            test_seen.add(sample_sha)
                            result.update(time=time.time(),expected=label,correct=result['id']==label if label is not None else None,
                                          counts_for_score=distinct,sample_sha256=sample_sha)
                            with self.owner.lock:
                                with (run/'validation.jsonl').open('a',encoding='utf-8') as file:
                                    file.write(json.dumps(result,ensure_ascii=False)+'\n')
                                self.latest_prediction=result
                                self.session['saved']+=1
                                self.session['message']='识别到 '+result['gesture']+('，达到触发门槛。' if result['accepted'] else '，未达到触发门槛，请再试。')
                                if not distinct:
                                    self.session['message']+=' 与已有记录重复，不计验证准确率。'
                        feedback_until=time.monotonic()+1.5
                        ring.clear()
                    last_state=state
        except Exception as exc:
            final='已停止：'+str(exc)
        finally:
            if connection:
                try:
                    connection.sendall(b'inference\n')
                    # A full close with unread IMU frames can reset TCP on Windows
                    # and discard the final restore command. Half-close, then drain
                    # the short tail without accepting additional recordings.
                    connection.shutdown(socket.SHUT_WR)
                    deadline=time.monotonic()+.4
                    while time.monotonic()<deadline:
                        connection.settimeout(.1)
                        try:
                            if not connection.recv(4096):break
                        except socket.timeout:break
                except OSError:
                    pass
                connection.close()
            if worker:
                if worker.poll() is None:
                    worker.terminate()
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill();worker.wait()
                for stream in (worker.stdin,worker.stdout):
                    if stream:
                        try:stream.close()
                        except OSError:pass
            if worker_log:worker_log.close()
            with self.owner.lock:
                self.predictor=None
                self.session.update(running=False,connected=False,message=final)

    def stop(self):
        self.stop_event.set()
        worker=self.predictor
        if worker and worker.poll() is None:
            worker.terminate()
        if self.thread:self.thread.join(timeout=6)
        return dict(message='正在结束，已完成的记录保留。')

    def train(self,epochs):
        if epochs not in (10,30,60):
            raise ValueError('请选择有效训练方式。')
        with self.owner.lock:
            self.owner.idle()
            self.quality.require_resolved()
            read_samples(self.data,minimum=MINIMUM,names=self.gestures)
            if importlib.util.find_spec('tensorflow') is None:
                raise ValueError('请通过配套启动程序安装训练环境。')
            stats=self._stats()
            run=self.root/'runs'/stamp();run.mkdir(parents=True)
            shutil.copytree(self.data,run/'training_data')
            atomic_json(run/'gestures.json',dict(names=self.gestures))
            self.owner._start_job([sys.executable,str(Path(__file__).with_name('train_worker.py')),'--data',str(run/'training_data'),
                '--output',str(run),'--epochs',str(epochs),'--profile',PROFILE,
                '--dataset-sha',stats['fingerprint'],'--catalog',str(run/'gestures.json')],kind='gesture-training',run=run,total=epochs)
        return dict(message='120条门槛检查通过，开始训练全部手势。')

    def model_path(self):
        from model_package import prepare
        run=self._require_fresh()
        return prepare(run,self.result['report'])[0]

    def upload_model(self,host):
        from ota import device_address
        host=device_address(host)
        with self.owner.lock:
            self.owner.idle()
            data=self.model_path().read_bytes()
            self.model_transfer.update(running=True,phase='connecting',percent=0,message='正在连接魔杖')
            self.owner.ota.update(running=True,kind='model',phase='connecting',percent=0,message='正在连接魔杖')
            threading.Thread(target=self._upload_model,args=(host,data),daemon=True).start()
        return dict(message='正在上传模型，功能及灯效配置由魔杖保留。')

    def _upload_model(self,host,data):
        from model_transfer import upload
        def progress(phase,percent,message):
            with self.owner.lock:
                self.model_transfer.update(phase=phase,percent=percent,message=message)
                self.owner.ota.update(phase=phase,percent=percent,message=message)
        try:upload(host,data,progress)
        except Exception as exc:progress('error',self.model_transfer['percent'],str(exc))
        finally:
            with self.owner.lock:self.model_transfer['running']=False;self.owner.ota['running']=False

    def save_bindings(self,values):
        raise ValueError('功能对应已移到魔杖自己的网页，请连接魔杖 Wi-Fi 后打开 http://192.168.4.1 配置。')

    def build(self):
        raise ValueError('电脑工作台只管理手势模型，固件维护请在魔杖网页进行。')

    def finished(self,kind,run,done,code,cancelled):
        if cancelled:
            self.owner.job.update(phase='cancelled',message='已取消，之前的模型与记录保留。');return
        if code==0 and kind=='gesture-quality' and done:
            try:
                self.quality.accept_scan(run)
                self.owner.job.update(phase='complete',message='数据检查完成。');return
            except (ValueError,KeyError,OSError) as exc:self.owner.logs.append(str(exc))
        if code==0 and kind=='gesture-training' and done:
            try:
                report=json.loads((run/'report.json').read_text(encoding='utf-8'))
                check_report(report)
                if hashlib.sha256((run/'gesture_model.tflite').read_bytes()).hexdigest()!=report['model_sha256']:
                    raise ValueError('模型校验失败')
                self.result=dict(run=run.name,report=report)
                self.latest_prediction=None
                self.model_transfer.update(running=False,phase='idle',percent=0,message='')
                atomic_json(self.root/'latest.json',dict(run=run.name,profile=PROFILE))
                self.owner.job.update(phase='complete',message='模型训练完成。下一步用新动作验证识别。');return
            except (ValueError,KeyError,OSError) as exc:
                self.owner.logs.append(str(exc))
        if code==0 and kind=='gesture-building':
            try:
                source=run/'firmware_project/.pio/build/esp32-s3-fh4r2/firmware.bin'
                data=application_bytes(source)
                manifest=json.loads((run/'build-request.json').read_text())
                manifest['firmware_sha256']=hashlib.sha256(data).hexdigest()
                (run/'MugglesWand-gestures-app.bin').write_bytes(data)
                atomic_json(run/'firmware-manifest.json',manifest)
                self.owner.job.update(phase='complete',message='升级固件已生成。连接魔杖 Wi-Fi 后可无线更新。');return
            except (ValueError,KeyError,OSError) as exc:
                self.owner.logs.append(str(exc))
        self.owner.job.update(phase='error',message='任务未完成，记录与之前的模型保留。请查看日志。')

    def ota(self,host):
        raise ValueError('日常更新请上传模型。系统固件升级在魔杖网页进行。')
