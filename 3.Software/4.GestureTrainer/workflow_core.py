"""Stable gesture labels, independent action bindings and generated firmware."""
import hashlib
import json
import re
from pathlib import Path
from core import ACTIONS

PROFILE = 'gesture-workflow-v1'
GESTURES = ['a', 'e', 'n', 'o', 'p', 's', '8', '∞']
MINIMUM = 120
SCREEN_IDS = [0, 4, 9, 10, 11, 14, 20, 21]


def dataset_digest(samples, labels):
    return hashlib.sha256(json.dumps([labels, samples], separators=(',', ':')).encode()).hexdigest()


def validate_bindings(values):
    if not isinstance(values, list) or len(values) != len(GESTURES) or any(type(v) is not int or not -1 <= v < len(ACTIONS) for v in values):
        raise ValueError('请为每个手势选择有效功能，或选择“不执行功能”。')
    return values


def bindings_digest(values):
    return hashlib.sha256(json.dumps(validate_bindings(values)).encode()).hexdigest()


RUNTIME_MAPPING = 'device-nvs-v2-fx'


def prepare_runtime_firmware(project: Path, template: Path, report):
    """Rebase old exported models onto the current runtime-mapping template."""
    from core import patched_inference
    check_report(report)
    source=(template/'src/Model/gesture_inference.cpp').read_text(encoding='utf-8')
    if 'ModelStore::model_data()' not in source:raise ValueError('此开发模板不支持独立模型。')
    (project/'src/Model/gesture_inference.cpp').write_text(source,encoding='utf-8')
    normalization='#pragma once\n'
    for name,values in [('kBuiltinMean',report['mean']),('kBuiltinStd',report['std'])]:
        normalization+='constexpr float '+name+'[2] = {'+', '.join(f'{float(v):.9e}f' for v in values)+'};\n'
    (project/'src/Model/BuiltinModelProfile.h').write_text(normalization,encoding='utf-8')
    names = report['gestures']; count = len(names)
    key = profile_key(names)
    fallbacks = [(profile_key(names[:n]), n) for n in range(count-1, 7, -1)]
    header = ('#pragma once\n#include <stdint.h>\n'
              f'constexpr int kGestureCount = {count};\n'
              f'constexpr const char *kGestureProfileKey = "{key}";\n'
              f'constexpr const char *kGestureProfileTitle = "自定义{count}手势模型";\n'
              'constexpr const char *kGestureLabels[kGestureCount] = {' + ','.join(json.dumps(g,ensure_ascii=False) for g in names) + '};\n'
              'constexpr int8_t kDefaultGestureActions[kGestureCount] = {' + ','.join(['-1']*count) + '};\n'
              'struct GestureProfileFallback { const char *key; int count; };\n'
              f'constexpr int kGestureFallbackCount = {len(fallbacks)};\n'
              'constexpr GestureProfileFallback kGestureFallbacks[] = {' + (','.join('{"'+k+'",'+str(n)+'}' for k,n in fallbacks) or '{"",0}') + '};\n')
    (project/'src/Model/GestureProfile.h').write_text(header,encoding='utf-8')


def profile_key(names):
    return 'letters-v1' if names == GESTURES else 'g'+hashlib.sha256(json.dumps(names,ensure_ascii=False).encode()).hexdigest()[:13]


def valid_catalog(names):
    return (isinstance(names,list) and 8 <= len(names) <= 16 and names[:8] == GESTURES
            and all(isinstance(n,str) and 1 <= len(n) <= 16 and n == n.strip() and not any(ord(c)<32 for c in n) for n in names)
            and len({n.casefold() for n in names}) == len(names))


def check_report(report):
    counts = report.get('counts', [])
    if report.get('profile') != PROFILE or not valid_catalog(report.get('gestures')) or len(counts) != len(report['gestures']) or any(type(n) is not int or n < MINIMUM for n in counts):
        raise ValueError('当前模型不是这套120条手势流程训练的模型，请先完成采集与训练。')
