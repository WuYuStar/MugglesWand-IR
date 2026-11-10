"""Isolated training process: emits newline JSON progress to the desktop tool."""
import os
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '-1')
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '2')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '2')
import argparse
import json
from pathlib import Path
import traceback
from core import ACTIONS, CLASSES, read_samples, export_model

def emit(kind, **data):
    print(json.dumps(dict(type=kind, **data), ensure_ascii=False), flush=True)

def train(args):
    emit('progress', phase='preparing', message='正在载入训练环境，首次运行可能稍慢…')
    import numpy as np
    import tensorflow as tf
    tf.keras.utils.set_random_seed(42)
    from workflow_core import PROFILE, GESTURES, MINIMUM, dataset_digest
    gesture_profile = getattr(args, 'profile', None) == PROFILE
    if gesture_profile and getattr(args,'catalog',None):
        GESTURES = json.loads(args.catalog.read_text(encoding='utf-8'))['names']
        from workflow_core import valid_catalog
        if not valid_catalog(GESTURES):raise ValueError('手势目录无效。')
    classes = len(GESTURES) if gesture_profile else CLASSES
    values, labels, counts = read_samples(args.data, minimum=MINIMUM if gesture_profile else 10,
                                         names=GESTURES if gesture_profile else None)
    fingerprint = dataset_digest(values, labels)
    if gesture_profile and args.dataset_sha and fingerprint != args.dataset_sha:
        raise ValueError('训练数据快照与采集记录不一致，请重新开始训练。')
    x = np.asarray(values, dtype=np.float32).reshape(-1, 100, 2)
    y = np.asarray(labels, dtype=np.int32)
    # Split independently per class, never allowing duplicate samples into different sets.
    rng = np.random.default_rng(42)
    groups = [[], [], []]
    for label in range(classes):
        indices = rng.permutation(np.flatnonzero(y == label))
        n_test = max(2, int(len(indices) * .2))
        n_val = max(2, int(len(indices) * .2))
        for bucket, part in zip(groups, [indices[n_test + n_val:], indices[n_test:n_test + n_val], indices[:n_test]]):
            bucket.extend(map(int, part))
    train_ids, val_ids, test_ids = [rng.permutation(ids) for ids in groups]
    mean = x[train_ids].mean(axis=(0, 1))
    std = x[train_ids].std(axis=(0, 1)) + 1e-6
    normalized = (x - mean) / std
    layers = tf.keras.layers
    model = tf.keras.Sequential([
        layers.Input(shape=(100, 2)),
        layers.Conv1D(8, 5, activation='relu'), layers.BatchNormalization(), layers.MaxPooling1D(2),
        layers.Conv1D(16, 3, activation='relu'), layers.BatchNormalization(), layers.MaxPooling1D(2),
        layers.Flatten(), layers.Dense(16, activation='relu'), layers.Dense(classes, activation='softmax'),
    ])
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    class Progress(tf.keras.callbacks.Callback):
        def on_epoch_end(self, epoch, logs=None):
            logs = logs or {}
            emit('epoch', phase='training', epoch=epoch + 1, total=args.epochs,
                 accuracy=float(logs.get('accuracy', 0)), val_accuracy=float(logs.get('val_accuracy', 0)),
                 message=f'第 {epoch + 1} / {args.epochs} 轮训练完成')
    # tf.data bounds the private thread pools on desktops with many CPU cores.
    def data(ids, shuffle=False):
        ds = tf.data.Dataset.from_tensor_slices((normalized[ids], y[ids]))
        if shuffle:
            ds = ds.shuffle(len(ids), seed=42)
        options = tf.data.Options()
        options.threading.private_threadpool_size = 2
        return ds.batch(16).with_options(options)
    emit('progress', phase='training', message='开始学习你的手势…', counts=counts)
    model.fit(data(train_ids, True), validation_data=data(val_ids), epochs=args.epochs, verbose=0,
              callbacks=[Progress(), tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=8, restore_best_weights=True)])
    float_probs = model(normalized[test_ids], training=False).numpy()
    float_accuracy = float((float_probs.argmax(axis=1) == y[test_ids]).mean())
    emit('progress', phase='converting', message='正在生成魔杖使用的轻量模型…')
    # Freeze weights explicitly; Keras 3 resource-variable exports are unsuitable for MCU inference.
    from tensorflow.python.framework.convert_to_constants import convert_variables_to_constants_v2
    concrete = tf.function(lambda value: model(value, training=False)).get_concrete_function(
        tf.TensorSpec([1, 100, 2], tf.float32))
    frozen = convert_variables_to_constants_v2(concrete)
    converter = tf.lite.TFLiteConverter.from_concrete_functions([frozen])
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    def representative():
        # Cover every class in the calibration data.
        for index in train_ids[:500]:
            yield [normalized[index:index + 1].astype(np.float32)]
    converter.representative_dataset = representative
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    blob = converter.convert()
    interpreter = tf.lite.Interpreter(model_content=blob, num_threads=2)
    interpreter.allocate_tensors()
    inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    if inp['dtype'] != np.int8 or out['dtype'] != np.int8 or list(inp['shape']) != [1, 100, 2] or list(out['shape']) != [1, classes]:
        raise ValueError('模型的形状或量化格式与魔杖不匹配，没有导出。')
    scale, zero = inp['quantization']
    predictions, accepted, clipped = [], [], 0
    out_scale, out_zero = out['quantization']
    for index in test_ids:
        value = normalized[index:index + 1] / scale + zero
        raw = np.where(value >= 0, np.floor(value + .5), np.ceil(value - .5))
        clipped += int(np.count_nonzero((raw < -128) | (raw > 127)))
        quantized = np.clip(raw, -128, 127).astype(np.int8)
        interpreter.set_tensor(inp['index'], quantized)
        interpreter.invoke()
        probs = (interpreter.get_tensor(out['index'])[0].astype(np.float32) - out_zero) * out_scale
        prediction = int(probs.argmax())
        predictions.append(prediction)
        accepted.append(float(probs[prediction]) >= .6)
    truth = y[test_ids]
    predictions = np.asarray(predictions)
    accepted = np.asarray(accepted)
    confusion = np.zeros((classes, classes), dtype=int)
    for a, b in zip(truth, predictions):
        confusion[a, b] += 1
    report = dict(float_accuracy=float_accuracy, accuracy=float((predictions == truth).mean()),
                  trigger_accuracy=float(((predictions == truth) & accepted).mean()),
                  accepted_fraction=float(accepted.mean()), counts=counts, confusion=confusion.tolist(),
                  per_class_accuracy=[float((predictions[truth == c] == c).mean()) for c in range(classes)],
                  train_samples=len(train_ids), validation_samples=len(val_ids), test_samples=len(test_ids),
                  split_seed=42, epochs_requested=args.epochs, input_scale=scale, input_zero_point=zero,
                  clipped_test_values=clipped, tensorflow_version=tf.__version__,
                  evaluation_note='按类别随机留出不重复样本；同一次采集的动作仍可能相似，需要另做实物测试。')
    if gesture_profile:
        report.update(profile=PROFILE, gestures=GESTURES, data_fingerprint=fingerprint)
    if gesture_profile:
        from model_package import export_runtime_model
        report=export_runtime_model(args.output,blob,mean,std,report)
    else:
        if args.template is None:raise ValueError('旧版导出需要开发固件模板。')
        report=export_model(args.output,blob,mean,std,args.template,report)
    emit('done', phase='complete', message='模型已生成，验证后可以独立上传到魔杖。', result=report)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--template', type=Path)
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--profile', default='legacy')
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--dataset-sha', default='')
    args = parser.parse_args()
    try:
        train(args)
    except Exception as exc:
        traceback.print_exc()
        emit('error', message=f'训练未完成：{exc}')
        raise SystemExit(1)
