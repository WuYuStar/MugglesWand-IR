"""Versioned model-only contract. Contains no actions, light effects or firmware."""
import hashlib
import json
import math
from pathlib import Path
import struct

MAGIC=b'MWMODEL1'
VERSION=CONTRACT=1
MAX_CLASSES=16
MAX_MODEL_BYTES=48*1024
HEADER=struct.Struct('<8sIIII4f1024s24s')
assert HEADER.size==1088


def encode(blob,report):
    from workflow_core import check_report
    check_report(report)
    names=report['gestures'];mean=report.get('mean',[]);std=report.get('std',[])
    if len(mean)!=2 or len(std)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in [*mean,*std]) or any(v<=0 for v in std):raise ValueError('模型的归一化参数无效。')
    if not 8<=len(blob)<=MAX_MODEL_BYTES or blob[4:8]!=b'TFL3':raise ValueError('模型不是支持的TFLite文件，或超过48KB。')
    if hashlib.sha256(blob).hexdigest()!=report.get('model_sha256'):raise ValueError('模型校验失败，请恢复原文件或重新训练。')
    labels=bytearray(1024)
    for i,name in enumerate(names):
        raw=name.encode('utf-8')
        if not 1<=len(raw)<64 or any(ord(c)<32 for c in name):raise ValueError('手势名称无效或过长。')
        labels[i*64:i*64+len(raw)]=raw
    try:header=HEADER.pack(MAGIC,VERSION,len(names),len(blob),CONTRACT,*mean,*std,bytes(labels),bytes(24))
    except (struct.error,OverflowError):raise ValueError('模型参数超出设备支持的数值范围。') from None
    data=header+blob
    return data+hashlib.sha256(data).digest()


def decode(data):
    if len(data)<HEADER.size+32:raise ValueError('模型包不完整。')
    magic,version,count,length,contract,*tail=HEADER.unpack_from(data)
    if magic!=MAGIC or version!=VERSION or contract!=CONTRACT or not 2<=count<=16 or not 8<=length<=MAX_MODEL_BYTES or len(data)!=HEADER.size+length+32:raise ValueError('模型包格式或识别协议不兼容。')
    if hashlib.sha256(data[:-32]).digest()!=data[-32:]:raise ValueError('模型包校验失败。')
    mean,std=tail[:2],tail[2:4];labels=tail[4]
    if not all(math.isfinite(v) for v in [*mean,*std]) or min(std)<=0:raise ValueError('模型参数无效。')
    names=[]
    for i in range(count):
        raw=labels[i*64:(i+1)*64]
        if b'\0' not in raw:raise ValueError('手势名称缺少结束标记。')
        name=raw.split(b'\0')[0].decode('utf-8')
        if not name or any(ord(c)<32 for c in name) or name in names:raise ValueError('手势名称无效或重复。')
        names.append(name)
    return dict(protocol=VERSION,contract=CONTRACT,gestures=names,mean=mean,std=std,model_bytes=length,sha256=data[-32:].hex())


def prepare(run,report):
    data=encode((run/'gesture_model.tflite').read_bytes(),report)
    path=run/'gesture-model.wandmodel';pending=path.with_suffix('.pending');pending.write_bytes(data);pending.replace(path)
    return path,decode(data)


def export_runtime_model(directory,blob,mean,std,report):
    directory.mkdir(parents=True,exist_ok=True)
    report.update(mean=list(map(float,mean)),std=list(map(float,std)),model_sha256=hashlib.sha256(blob).hexdigest(),model_bytes=len(blob),model_protocol=VERSION)
    (directory/'gesture_model.tflite').write_bytes(blob)
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    prepare(directory,report)
    (directory/'使用说明.txt').write_text('这是独立手势模型包，不是固件。\n在训练工具中上传模型，或在魔杖网页的模型更新入口上传 .wandmodel 文件。\n功能和灯效由魔杖独立保存，模型包不包含功能配置。\n',encoding='utf-8')
    return report
