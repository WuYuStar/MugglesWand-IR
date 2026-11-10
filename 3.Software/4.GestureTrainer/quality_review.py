"""Snapshot-bound waveform reviews, reversible isolation and retained decisions."""
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
from core import read_samples, stamp
from workflow_core import GESTURES, MINIMUM, valid_catalog


def sample_hash(values):
    return hashlib.sha256(struct.pack('<200d', *values)).hexdigest()


def write_json(path, value):
    pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(pending,path)


class QualityReview:
    def __init__(self, workflow):
        self.workflow=workflow
        self.root=workflow.root/'quality'
        self._cached=None

    def _report(self):
        path=self.root/'review.json'
        if not path.exists():return None
        signature=(path.stat().st_mtime_ns,path.stat().st_size)
        if self._cached is None or self._cached[0]!=signature:
            report=json.loads(path.read_text(encoding='utf-8'))
            if report['format']!=1 or not valid_catalog(report['gestures']):
                raise ValueError('筛查记录格式不匹配，请重新导入。')
            self._cached=(signature,report)
        return self._cached[1]

    def _applied(self, report):
        path=self.root/'applied.json'
        if not path.exists():return None
        record=json.loads(path.read_text(encoding='utf-8'))
        return record if record['report_id']==report['id'] else None

    def import_review(self, report_file, snapshot):
        """Local administrative import; deliberately not an arbitrary-path HTTP API."""
        with self.workflow.owner.lock:
            self.workflow.owner.idle()
            blob=Path(report_file).read_bytes();audit=json.loads(blob)
            if audit['counts']!=[MINIMUM]*8:
                raise ValueError('这份筛查不是针对每类120条的原始记录。')
            report_id=hashlib.sha256(blob).hexdigest()[:16]
            current=self._report()
            if current:
                if current['id']==report_id:return dict(message='这份筛查已导入。')
                raise ValueError('已有一份筛查任务，请先完成或归档，未覆盖原记录。')
            snapshot=Path(snapshot)
            if read_samples(snapshot,minimum=MINIMUM,names=GESTURES)[2]!=audit['counts']:
                raise ValueError('快照的实际记录数与报告不同。')
            all_rows=[];original={}
            for label in range(8):
                name=f'{label}.csv';data=(snapshot/name).read_bytes()
                if hashlib.sha256(data).hexdigest()!=audit['original_sha256'][name]:
                    raise ValueError('筛查快照校验失败，没有导入。')
                if (self.workflow.data/name).read_bytes()!=data:
                    raise ValueError('当前数据已变化，不能套用旧筛查。请先重新检查。')
                original[name]=data
                for line,row in enumerate(csv.reader(io.StringIO(data.decode('utf-8-sig'))),1):
                    if row:all_rows.append(dict(label=label,line=line,values=list(map(float,row[1:]))))
            lookup={(r['label'],r['line']):r for r in all_rows}
            candidates=[];seen=set()
            for category in ('priority','review'):
                for record in audit[category]:
                    label=record['label'];line=record['line']
                    if type(label) is not int or not 0<=label<8 or type(line) is not int:
                        raise ValueError('筛查记录编号无效。')
                    row=lookup.get((label,line))
                    if not row or sample_hash(row['values'])!=record['sha256'] or (label,line) in seen:
                        raise ValueError('筛查条目与原始动作不一致，没有导入。')
                    seen.add((label,line))
                    ref_index=record['medoid_index']
                    if type(ref_index) is not int or not 0<=ref_index<len(all_rows):
                        raise ValueError('筛查参考动作无效。')
                    reference=all_rows[ref_index]
                    if reference['label']!=label:raise ValueError('参考动作不是同类手势。')
                    values=row['values'];ref=reference['values']
                    candidates.append(dict(id=record['id'],label=label,gesture=GESTURES[label],line=line,
                        file=f'{label}.csv',sha256=record['sha256'],category=category,reason=str(record['reason']),
                        sample=[values[i:i+2] for i in range(0,200,2)],reference=[ref[i:i+2] for i in range(0,200,2)],
                        reference_id=f"{GESTURES[label]} #{reference['line']:03d}"))
            if not any(r['category']=='priority' for r in candidates):raise ValueError('没有需要补录的筛查条目。')
            self.root.mkdir(parents=True,exist_ok=True)
            evidence=self.root/('source-'+report_id);evidence.mkdir(exist_ok=True)
            for name,data in original.items():(evidence/name).write_bytes(data)
            report=dict(format=1,id=report_id,date=str(audit['date']),gestures=GESTURES,source_hashes=audit['original_sha256'],
                original_counts=audit['counts'],candidates=candidates)
            write_json(self.root/'review.json',report)
            self._cached=None
            return dict(message='筛查结论和原始曲线已导入。')

    def state(self, counts):
        report=self._report()
        if not report:return dict(available=False)
        applied=self._applied(report)
        priority=[r for r in report['candidates'] if r['category']=='priority']
        planned=[sum(r['label']==label for r in priority) if not applied or applied.get('decision')!='keep' else 0 for label in range(len(self.workflow.gestures))]
        missing=[max(0,MINIMUM-count) if planned[i] else 0 for i,count in enumerate(counts)]
        matches=report['gestures']==self.workflow.gestures
        if not applied:
            matches=matches and all(hashlib.sha256((self.workflow.data/name).read_bytes()).hexdigest()==digest for name,digest in report['source_hashes'].items())
        return dict(available=True,report_id=report['id'],date=report['date'],applied=bool(applied),can_apply=not applied and matches,
            source_changed=not applied and not matches,priority_count=len(priority),review_count=len(report['candidates'])-len(priority),
            planned=planned,missing=missing,missing_total=sum(missing),complete=bool(applied) and not any(missing),
            original_counts=report['original_counts'],
            candidates=[{k:v for k,v in r.items() if k not in ('sample','reference')} for r in report['candidates']],
            decision=applied.get('decision','isolate') if applied else None,skipped=report.get('skipped',[]),
            backup=applied['backup'] if applied else None)

    def details(self):
        report=self._report()
        if not report:raise ValueError('尚未导入筛查结论。')
        return dict(report_id=report['id'],candidates=report['candidates'])

    def excluded_samples(self):
        reports=[]
        current=self._report()
        if current and (applied:=self._applied(current)) and applied.get('decision')!='keep':reports.append(current)
        for path in (self.root/'archive').glob('*/review.json'):
            applied=path.with_name('applied.json')
            if applied.exists() and json.loads(applied.read_text(encoding='utf-8')).get('decision')!='keep':
                reports.append(json.loads(path.read_text(encoding='utf-8')))
        return {tuple(v for frame in r['sample'] for v in frame):r['label'] for report in reports for r in report['candidates'] if r['category']=='priority'}

    def scan(self):
        with self.workflow.owner.lock:
            self.workflow.owner.idle()
            stats=self.workflow._stats()
            if max(stats['counts'])<10:raise ValueError('先为一个手势录满10次，再检查数据。')
            run=self.root/'scans'/stamp();run.mkdir(parents=True)
            shutil.copytree(self.workflow.data,run/'data')
            write_json(run/'gestures.json',dict(names=self.workflow.gestures))
            self.workflow.owner._start_job([sys.executable,str(Path(__file__).with_name('quality_worker.py')),'--run',str(run)],kind='gesture-quality',run=run,total=sum(stats['counts']))
        return dict(message='正在检查数据，原记录不会改动。')

    def accept_scan(self,run):
        report=json.loads((run/'review.json').read_text(encoding='utf-8'))
        if not valid_catalog(report.get('gestures')) or report['gestures']!=self.workflow.gestures:raise ValueError('手势目录已变化，请重新检查。')
        if not all(hashlib.sha256((self.workflow.data/name).read_bytes()).hexdigest()==digest for name,digest in report['source_hashes'].items()):raise ValueError('检查期间数据已变化，请重新检查。')
        if self._report():
            archive=self.root/'archive'/stamp();archive.mkdir(parents=True)
            for name in ('review.json','applied.json'):
                if (self.root/name).exists():shutil.copy2(self.root/name,archive/name)
        # The current marker is matched by report id; archived decisions retain replay protection.
        write_json(self.root/'review.json',report);self._cached=None
        if not any(r['category']=='priority' for r in report['candidates']):
            write_json(self.root/'applied.json',dict(report_id=report['id'],backup=None,decision='keep'))

    def keep(self):
        with self.workflow.owner.lock:
            self.workflow.owner.idle()
            report=self._report()
            if not report:raise ValueError('请先检查数据。')
            if self._applied(report):return dict(message='这份检查已处理。')
            write_json(self.root/'applied.json',dict(report_id=report['id'],backup=None,decision='keep'))
        return dict(message='已保留原记录。')

    def require_resolved(self):
        report=self._report()
        if report and not self._applied(report):
            raise ValueError('请先处理数据检查结果：隔离建议记录，或选择保留原记录。')
        if report:
            excluded=self.excluded_samples()
            if any(tuple(row) in excluded for row in self.workflow._stats()['samples']):
                raise ValueError('训练数据中又出现已隔离的异常原记录，请先复核。')

    def apply(self):
        with self.workflow.owner.lock:
            self.workflow.owner.idle()
            report=self._report()
            if not report:raise ValueError('尚未导入筛查结论。')
            if self._applied(report):return dict(message='已采用这份筛查，不会重复隔离或覆盖补录记录。')
            state=self.state(self.workflow._stats()['counts'])
            if not state['can_apply']:raise ValueError('当前数据与筛查原件不同，没有改动。请重新检查。')
            transaction=self.root/'history'/stamp();original=transaction/'original';stage=transaction/'prepared';isolated=transaction/'isolated'
            for folder in [original,stage,isolated]:folder.mkdir(parents=True,exist_ok=True)
            changed=[]
            for label in range(len(report['gestures'])):
                name=f'{label}.csv';path=self.workflow.data/name
                shutil.copy2(path,original/name)
                excluded={tuple(v for frame in r['sample'] for v in frame) for r in report['candidates'] if r['category']=='priority' and r['label']==label}
                with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
                keep=[];remove=[]
                for row in rows:
                    (remove if row and tuple(map(float,row[1:])) in excluded else keep).append(row)
                with (stage/name).open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows(keep)
                with (isolated/name).open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows(remove)
                if excluded:changed.append(name)
            _,_,counts=read_samples(stage,minimum=0,names=report['gestures'])
            expected=[n-p for n,p in zip(report['original_counts'],state['planned'])]
            if counts!=expected:raise ValueError('隔离后的记录数校验失败，当前数据未修改。')
            committed=[]
            try:
                for name in changed:
                    os.replace(stage/name,self.workflow.data/name)
                    committed.append(name)
                # This marker is committed only after all affected classes succeed.
                write_json(self.root/'applied.json',dict(report_id=report['id'],counts=counts,backup=str(original),isolated=str(isolated)))
            except Exception:
                for name in committed:
                    pending=self.workflow.data/(name+'.restore')
                    shutil.copy2(original/name,pending)
                    os.replace(pending,self.workflow.data/name)
                raise
            self.workflow._stats_cache=None
            return dict(message=f"已备份原记录并单独留存{state['priority_count']}条，按页面提示补录即可。",counts=counts)
