"""Separate candidate trials and exploratory waveform comparison; no model install."""
from collections import deque
import io
import json
import math
from pathlib import Path
import secrets
import socket
import threading
import time
import zipfile
from core import backup
from alphabet import CANDIDATES, ROUTES, CATALOG_ID, CATALOG_NAME, CATALOG_REVISION


TRIAL_SECONDS = 6


class Screening:
    def __init__(self, workspace, lock):
        # New catalogs must never reinterpret the IDs of existing recordings.
        self.root = Path(workspace) / 'screening' / CATALOG_ID
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = lock
        self.stop_event = threading.Event()
        self.thread = None
        self.session = dict(running=False, candidate=None, phase='idle', message='选择一个候选，先录入 10 次。',
                            trial=0, total=10, remaining=0)
        self.trace = []
        self.report = None
        report = self.root / 'analysis.json'
        if report.is_file():
            try:
                self.report = json.loads(report.read_text(encoding='utf-8'))
                if len(self.report.get('candidates', [])) != len(CANDIDATES):
                    # Preserve the old report, but do not present it as covering
                    # the newly appended symbols. Raw recordings stay in place.
                    backup(report, self.root / 'backups')
                    self.report = None
            except (ValueError, OSError):
                pass

    def records(self, candidate):
        path = self.root / f'{candidate}.jsonl'
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                result.append(json.loads(line))
        return result

    def state(self, candidate=None):
        with self.lock:
            if candidate is not None and (type(candidate) is not int or not 0 <= candidate < len(CANDIDATES)):
                raise ValueError('请选择有效的候选。')
            candidate = self.session['candidate'] if candidate is None else candidate
            candidates = []
            trace = []
            for index, (name, symbol, instruction) in enumerate(CANDIDATES):
                records = self.records(index)
                if index == candidate:
                    trace = list(self.trace) if self.session['running'] and self.session['candidate'] == candidate else (records[-1]['frames'] if records else [])
                accepted = sum(bool(row['accepted']) for row in records)
                candidates.append(dict(id=index, name=name, symbol=symbol, instruction=instruction,
                                       route=ROUTES[index],
                                       trials=len(records), accepted=accepted,
                                       capture_rate=accepted / len(records) if records else None))
            return dict(catalog_id=CATALOG_ID, catalog_revision=CATALOG_REVISION, catalog_name=CATALOG_NAME, candidates=candidates, session=dict(self.session), trace=trace, trace_candidate=candidate, report=self.report)

    def invalidate(self):
        self.report = None
        (self.root / 'analysis.json').unlink(missing_ok=True)

    def start(self, candidate, host, port, total):
        if type(candidate) is not int or not 0 <= candidate < len(CANDIDATES):
            raise ValueError('请选择有效的候选手势。')
        if type(total) is not int or total not in (1, 10):
            raise ValueError('请选择录入一次或一组 10 次。')
        if not isinstance(host, str) or not host.strip() or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('请检查魔杖地址和端口。')
        self.stop_event.clear()
        self.session.update(running=True, candidate=candidate, phase='connecting', message='正在连接魔杖…',
                            trial=0, total=total, remaining=0)
        self.thread = threading.Thread(target=self._capture, args=(candidate, host, port, total), daemon=True)
        self.thread.start()
        return dict(message='正在连接，等页面提示“现在画”再挥动。')

    def _capture(self, candidate, host, port, total):
        connection = None
        ring = deque(maxlen=100)
        session_id = secrets.token_hex(8)
        completed = 0
        phase = 'preparing'
        deadline = time.monotonic() + 8
        trial_start = time.monotonic()
        last_state = None
        last_data = time.monotonic()
        final = '这组已完成，可以继续补录或换下一个候选。'
        try:
            connection = socket.create_connection((host, port), timeout=5)
            connection.settimeout(.2)
            connection.sendall(b'imu\n')
            last_data = time.monotonic()
            pending = b''
            deadline = time.monotonic() + 8
            while not self.stop_event.is_set() and completed < total:
                now = time.monotonic()
                if now - last_data > 5:
                    raise OSError('没有收到动作数据，请检查 Wi-Fi，已完成的记录会保留。')
                if phase == 'preparing' and now >= deadline:
                    raise ValueError('没有等到停稳状态。请先放稳魔杖，再重新开始这一组。')
                if phase == 'armed' and now >= deadline:
                    self._save(candidate, ring, False, 'timeout', session_id, now - trial_start)
                    completed += 1
                    phase = 'feedback'
                    deadline = now + 1.3
                    with self.lock:
                        self.session.update(message='这次未捕捉到完整动作，已记录。停稳后继续。', remaining=0)
                if phase == 'feedback' and now >= deadline:
                    phase = 'preparing'
                    deadline = now + 8
                    ring.clear()
                with self.lock:
                    self.session.update(phase=phase, trial=min(completed + 1, total),
                                        remaining=max(0, math.ceil(deadline - now)) if phase == 'armed' else 0)
                    if phase == 'preparing':
                        self.session['message'] = '先停稳，等“现在画”的提示。'
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise OSError('连接断开，已完成的记录已保存；本次未完成的尝试不计入结果。')
                pending += chunk
                if len(pending) > 65536:
                    raise ValueError('数据异常，请结束后重新连接。')
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        values = [float(v) for v in line.decode('utf-8').strip().split(',')]
                        if len(values) != 3 or not all(math.isfinite(v) for v in values) or values[2] not in (0, 1, 2, 3, 4):
                            continue
                    except (UnicodeDecodeError, ValueError):
                        continue
                    now = time.monotonic()
                    last_data = now
                    ring.append(values)
                    state = int(values[2])
                    with self.lock:
                        self.trace = list(ring)
                    if phase == 'preparing' and len(ring) == 100 and state == 1:
                        phase = 'armed'
                        trial_start = now
                        deadline = now + TRIAL_SECONDS
                        with self.lock:
                            self.session.update(phase='armed', message='现在画！约一秒内画完，然后停稳。', remaining=math.ceil(TRIAL_SECONDS))
                    elif phase == 'armed' and state == 4 and last_state != 4:
                        self._save(candidate, ring, True, 'complete', session_id, now - trial_start)
                        completed += 1
                        phase = 'feedback'
                        deadline = now + 1.3
                        with self.lock:
                            self.session.update(phase='feedback', message=f'已保存第 {completed} 次！停稳，准备下一次。', remaining=0)
                    last_state = state
        except Exception as exc:
            final = str(exc)
        finally:
            if connection:
                try:
                    connection.sendall(b'inference\n')
                except OSError:
                    pass
                connection.close()
            with self.lock:
                self.session.update(running=False, phase='idle', remaining=0,
                                    message='已停止，完成的记录已保留。' if self.stop_event.is_set() else final)

    def _save(self, candidate, ring, accepted, reason, session_id, elapsed):
        row = dict(time=time.time(), session=session_id, accepted=accepted, reason=reason,
                   elapsed=elapsed, frames=list(ring))
        with self.lock:
            with (self.root / f'{candidate}.jsonl').open('a', encoding='utf-8') as file:
                file.write(json.dumps(row, separators=(',', ':')) + '\n')
            self.invalidate()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=6)
        return dict(message='正在结束候选采集，已保存的记录会保留。')

    def reset(self, candidate):
        if type(candidate) is not int or not 0 <= candidate < len(CANDIDATES):
            raise ValueError('请选择有效的候选。')
        backup(self.root / f'{candidate}.jsonl', self.root / 'backups')
        (self.root / f'{candidate}.jsonl').unlink(missing_ok=True)
        self.invalidate()
        return dict(message='已备份并清空这个候选，其他数据已保留。')

    def analyze(self):
        # NumPy is already installed with the trainer's TensorFlow environment.
        import numpy as np
        rows, labels, counts, rates, trial_counts = [], [], {}, {}, {}
        for candidate in range(len(CANDIDATES)):
            records = self.records(candidate)
            trial_counts[candidate] = len(records)
            successes = [r for r in records if r['accepted']]
            rates[candidate] = len(successes) / len(records) if records else 0
            unique = {}
            for record in successes:
                frames = np.asarray(record['frames'], dtype=np.float32)
                if frames.shape != (100, 3) or not np.isfinite(frames).all():
                    continue
                unique[frames[:, :2].tobytes()] = frames[:, :2]
            counts[candidate] = len(unique)
            if counts[candidate] >= 10 and rates[candidate] >= .5:
                for frame in list(unique.values())[-20:]:
                    rows.append(frame)
                    labels.append(candidate)
        eligible = sorted(set(labels))
        if len(eligible) < 2:
            raise ValueError('至少两个候选各有 10 条不重复的有效记录，且捕捉率达到 50%，才能开始比较。')
        # Preserve rotation directions and axis relationships; trim resting padding,
        # then compare timing-normalized waveforms plus amplitude and duration.
        features = []
        for frame in rows:
            energy = np.linalg.norm(frame, axis=1)
            active = np.flatnonzero(energy > max(.3, float(energy.max()) * .12))
            start, end = (max(0, active[0] - 2), min(100, active[-1] + 3)) if len(active) else (0, 100)
            segment = frame[start:end]
            waveform = np.stack([np.interp(np.linspace(0, len(segment) - 1, 50), np.arange(len(segment)), segment[:, axis]) for axis in (0, 1)], axis=1)
            amplitude = max(float(np.sqrt(np.mean(waveform ** 2))), .05)
            features.append(np.r_[waveform.reshape(-1) / amplitude / 10,
                                  np.log1p(amplitude) * .2, (end - start) / 100 * .2])
        x = np.asarray(features, dtype=np.float64)
        distances = np.sqrt(np.maximum(0, (x*x).sum(1)[:, None] + (x*x).sum(1)[None, :] - 2 * x @ x.T))
        labels = np.asarray(labels)
        np.fill_diagonal(distances, np.inf)
        # Leave-one-out nearest-neighbour estimates are exploratory, not a CNN test.
        predictions = labels[distances.argmin(1)]
        recall = {i: float((predictions[labels == i] == i).mean()) for i in eligible}
        within = {i: float(np.median(distances[np.ix_(labels == i, labels == i)][np.isfinite(distances[np.ix_(labels == i, labels == i)])])) for i in eligible}
        pairs, separation = [], {}
        for pos, a in enumerate(eligible):
            for b in eligible[pos+1:]:
                between = float(np.median(distances[np.ix_(labels == a, labels == b)]))
                margin = between / max(.05, (within[a] + within[b]) / 2)
                confusion = float(((predictions[labels == a] == b).sum() + (predictions[labels == b] == a).sum()) / ((labels == a).sum() + (labels == b).sum()))
                pairs.append(dict(a=a, b=b, separation=round(margin, 3), confusion=confusion))
                separation[a, b] = separation[b, a] = margin
        target = min(10, len(eligible))
        best, best_score = [], None
        for seed in eligible:
            selected = [seed]
            while len(selected) < target:
                choice = max((i for i in eligible if i not in selected), key=lambda i:
                             (min(separation[i, j] for j in selected) * (.5 + .5 * recall[i]) * rates[i], -i))
                selected.append(choice)
            score = (min(separation[a, b] for p, a in enumerate(selected) for b in selected[p+1:]),
                     sum(recall[i] * rates[i] for i in selected))
            if best_score is None or score > best_score:
                best, best_score = selected, score
        selected_labels = np.isin(labels, best)
        restricted = distances.copy()
        restricted[:, ~selected_labels] = np.inf
        selected_predictions = labels[restricted.argmin(1)]
        selected_accuracy = float((selected_predictions[selected_labels] == labels[selected_labels]).mean())
        weakest_separation = min(separation[a, b] for p, a in enumerate(best) for b in best[p+1:])
        result = dict(catalog_id=CATALOG_ID, catalog_revision=CATALOG_REVISION, created=time.time(), selected=sorted(best), eligible=eligible, target=10,
                      provisional=len(eligible) < 10, recorded_candidates=sum(n > 0 for n in counts.values()),
                      evaluated_per_candidate={str(i): int((labels == i).sum()) for i in eligible},
                      selected_loo_accuracy=selected_accuracy,
                      needs_review=selected_accuracy < .8 or weakest_separation < 1.2,
                      weakest_separation=weakest_separation,
                      recording_groups={str(i): len({r['session'] for r in self.records(i) if r['accepted']}) for i in eligible},
                      candidates=[dict(id=i, unique=counts[i], trials=trial_counts[i], capture_rate=rates[i] if trial_counts[i] else None, within=within.get(i), recall=recall.get(i)) for i in range(len(CANDIDATES))],
                      confusing_pairs=sorted(pairs, key=lambda p: (-p['confusion'], p['separation']))[:12],
                      method='waveform-v1; last 20 unique accepted trials/class; trim, resample50, direction preserved, amplitude/duration weighted; leave-one-out nearest neighbour',
                      limitation='探索性筛选，不是训练模型准确率。同组录入可能偏乐观；请分组补录并用新动作复测。状态线仅检查捕捉，不参与相似度排序。')
        self.report = result
        (self.root / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return dict(message='筛选完成，请查看建议保留和容易混淆的组合。', report=result)

    def archive(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('candidates.json', json.dumps(dict(catalog_id=CATALOG_ID, candidates=[dict(id=i, name=c[0], letter=c[1], instruction=c[2], route=ROUTES[i]) for i, c in enumerate(CANDIDATES)]), ensure_ascii=False))
            for path in sorted(self.root.glob('*.jsonl')):
                archive.write(path, path.name)
            if (self.root / 'analysis.json').is_file():
                archive.write(self.root / 'analysis.json', 'analysis.json')
        return data.getvalue()
