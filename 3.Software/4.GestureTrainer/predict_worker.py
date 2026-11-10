"""Isolated INT8 inference for live desktop validation; JSON lines in and out."""
import os
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '2')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '2')
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import tensorflow as tf
    from workflow_core import check_report, GESTURES
    report = json.loads((args.run/'report.json').read_text(encoding='utf-8'))
    check_report(report)
    GESTURES=report['gestures']
    blob = (args.run/'gesture_model.tflite').read_bytes()
    if hashlib.sha256(blob).hexdigest() != report['model_sha256']:
        raise ValueError('模型文件校验失败，请重新训练。')
    interpreter = tf.lite.Interpreter(model_content=blob, num_threads=2)
    interpreter.allocate_tensors()
    inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    if list(inp['shape']) != [1,100,2] or list(out['shape']) != [1,len(GESTURES)] or inp['dtype'] != np.int8 or out['dtype'] != np.int8:
        raise ValueError('模型形状与魔杖不匹配。')
    mean, std = np.asarray(report['mean'],np.float32), np.asarray(report['std'],np.float32)
    if mean.shape != (2,) or std.shape != (2,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError('模型归一化参数无效。')
    print(json.dumps(dict(type='ready', model_sha256=report['model_sha256'])),flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            x = np.asarray(request['features'],np.float32)
            if x.size != 200 or not np.isfinite(x).all():
                raise ValueError('需要100帧有效动作数据。')
            raw = ((x.reshape(1,100,2)-mean)/std)/inp['quantization'][0]+inp['quantization'][1]
            quantized = np.clip(np.where(raw>=0,np.floor(raw+.5),np.ceil(raw-.5)),-128,127).astype(np.int8)
            interpreter.set_tensor(inp['index'],quantized)
            interpreter.invoke()
            probabilities = (interpreter.get_tensor(out['index'])[0].astype(np.float32)-out['quantization'][1])*out['quantization'][0]
            winner = int(probabilities.argmax())
            confidence = float(probabilities[winner])
            print(json.dumps(dict(type='prediction',id=winner,gesture=GESTURES[winner],confidence=confidence,
                                  accepted=confidence>=.6, probabilities=probabilities.tolist())),flush=True)
        except Exception as exc:
            print(json.dumps(dict(type='error',message=str(exc)),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(type='error',message=str(exc)),ensure_ascii=False),flush=True)
        raise SystemExit(1)
