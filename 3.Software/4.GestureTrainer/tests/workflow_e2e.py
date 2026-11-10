"""Real train -> INT8 live test -> mapped firmware builds, with isolated fixtures.
The seed dataset is relabelled ONLY for this pipeline test, not for user training.
No serial flashing or OTA to a physical device occurs.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Trainer,find_seed
from workflow_core import PROFILE,GESTURES

parser=argparse.ArgumentParser()
parser.add_argument('--workspace',type=Path,required=True)
args=parser.parse_args()
if args.workspace.exists():
    raise SystemExit('Use a fresh isolated workspace; no existing data will be replaced.')
app=Trainer(args.workspace)
w=app.workflow
def wait(predicate,timeout=300):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise AssertionError('Timed out: '+str(app.job))
try:
    for i in range(8):shutil.copy2(find_seed()/f'{i}.csv',w.data/f'{i}.csv')
    w.train(60)
    wait(lambda:not app.job['running'])
    assert app.job['phase']=='complete',list(app.logs)
    assert w.result['report']['profile']==PROFILE and w.result['report']['counts']==[120]*8
    run=w._run()
    print('PASS: actual training with 120 unique/class, gesture labels, INT8 export',flush=True)
    with (w.data/'0.csv').open() as f:features=list(map(float,next(csv.reader(f))[1:]))
    fresh=features.copy();fresh[15]+=.0123456
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(60)
    commands=[]
    def wand():
        c,_=listener.accept()
        with c:
            c.settimeout(30);commands.append(c.recv(64))
            for sample in [features,fresh]:
                lines=[]
                for i in range(100):
                    phase=1 if i<20 else 2 if i<50 else 3 if i<99 else 4
                    lines.append(f'{sample[i*2]},{sample[i*2+1]},{phase}\n')
                raw=''.join(lines).encode();c.sendall(raw[:31]);c.sendall(raw[31:])
            commands.append(c.recv(64))
    threading.Thread(target=wand,daemon=True).start()
    before=[(w.data/f'{i}.csv').read_bytes() for i in range(8)]
    w.start('testing',0,'127.0.0.1',listener.getsockname()[1])
    wait(lambda:w.session['saved']>=2,timeout=90)
    w.stop();listener.close()
    assert commands==[b'imu\n',b'inference\n'],commands
    validation=w.validation_state()
    assert validation['total']==1  # Exact training replay is shown but not scored.
    assert all((w.data/f'{i}.csv').read_bytes()==before[i] for i in range(8))
    assert validation['latest']['gesture'] in GESTURES
    print('PASS: actual latest-model inference, serial-fragmented TCP, replay exclusion, independent test records, restoration',flush=True)
    w.build();wait(lambda:not app.job['running'])
    assert app.job['phase']=='complete',list(app.logs)
    original=w.firmware_path().read_bytes()
    model_hash=w.result['report']['model_sha256']
    header=(run/'firmware_project/src/Model/GestureProfile.h').read_text()
    assert '"letters-v1"' in header and '{-1,-1,-1,-1,-1,-1,-1,-1}' in header
    assert '#define WS2812_LED_COUNT 39' in (run/'firmware_project/src/Config.h').read_text()
    print('PASS: real firmware compile, letter labels, runtime NVS mapping, 39 LEDs, verified manifest',flush=True)
    config=run/'firmware_project/src/Config.h'
    config.write_text(config.read_text().replace('WS2812_LED_COUNT 39','WS2812_LED_COUNT 58'))
    w.build();wait(lambda:not app.job['running'])
    assert app.job['phase']=='complete',list(app.logs)
    assert 'WS2812_LED_COUNT 39' in config.read_text()
    assert w.result['report']['model_sha256']==model_hash
    assert w.firmware_path().exists()
    print('PASS: cached project refreshed, unchanged model, no desktop function config required',flush=True)
    (w.root/'TEST-FIXTURE-DO-NOT-INSTALL.txt').write_text('Relabelled official sample fixture for software verification only. Not the user gesture model. No hardware update performed.')
    print('FIXTURE_RUN',run,flush=True)
finally:
    app.close()
