"""Read-only, isolated waveform review. Suggestions are not accuracy measurements."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from core import read_samples
from quality_review import sample_hash


def emit(kind, **data):
    print(json.dumps(dict(type=kind, **data), ensure_ascii=False), flush=True)


def dtw(a,b):
    m=a.shape[1]
    prev=np.full((m+1,len(a),len(b)),np.inf,dtype=np.float32);prev[0]=0
    for i in range(m):
        cur=np.full_like(prev,np.inf)
        for j in range(max(0,i-5),min(m,i+6)):
            cost=np.mean((a[:,None,i]-b[None,:,j])**2,axis=-1)
            cur[j+1]=cost+np.minimum(np.minimum(prev[j],prev[j+1]),cur[j])
        prev=cur
    return np.sqrt(prev[m]/m)


def robust(values):
    center=np.median(values)
    scale=max(1.4826*float(np.median(abs(values-center))),float(np.subtract(*np.quantile(values,[.75,.25])))/1.349,.01)
    return (values-center)/scale


def analyze(data,names,progress=lambda **kw:None):
    _,_,counts=read_samples(data,minimum=0,names=names)
    rows=[];hashes={};seen=set()
    for label in range(len(names)):
        path=data/f'{label}.csv';hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        for line,row in enumerate(csv.reader(path.read_text(encoding='utf-8-sig').splitlines()),1):
            if not row:continue
            values=list(map(float,row[1:]));key=tuple(values)
            if key in seen:continue
            seen.add(key);rows.append(dict(label=label,line=line,values=values))
    eligible=[i for i,row in enumerate(rows) if counts[row['label']]>=10]
    skipped=[names[i] for i,n in enumerate(counts) if n<10]
    if not eligible:raise ValueError('至少一个手势录满10条后才能检查。')
    frames=np.asarray([rows[i]['values'] for i in eligible],dtype=np.float32).reshape(-1,100,2)
    records=[rows[i] for i in eligible];labels=np.array([r['label'] for r in records]);shapes=[]
    for frame in frames:
        energy=np.linalg.norm(frame,axis=1);active=np.flatnonzero(energy>max(.3,float(energy.max())*.12))
        lo,hi=(max(0,int(active[0])-2),min(100,int(active[-1])+3)) if len(active) else (0,100)
        segment=frame[lo:hi]
        wave=np.stack([np.interp(np.linspace(0,len(segment)-1,50),np.arange(len(segment)),segment[:,a]) for a in range(2)],axis=1)
        shapes.append(wave/max(float(np.sqrt(np.mean(wave**2))),.05))
    shapes=np.asarray(shapes,dtype=np.float32);n=len(shapes)
    # Limit temporary allocations even when many recordings have accumulated.
    D=np.empty((n,n),dtype=np.float32)
    for i in range(0,n,64):
        for j in range(i,n,64):
            block=dtw(shapes[i:i+64],shapes[j:j+64]);D[i:i+64,j:j+64]=block;D[j:j+64,i:i+64]=block.T
        progress(epoch=min(i+64,n),total=n,message='正在比较动作曲线…')
    np.fill_diagonal(D,np.inf)
    full=frames/np.maximum(np.sqrt(np.mean(frames**2,axis=(1,2),keepdims=True)),.05)
    full=full.reshape(n,-1)
    F=np.sqrt(np.maximum(0,(full*full).sum(1)[:,None]+(full*full).sum(1)[None,:]-2*full@full.T)/200)
    np.fill_diagonal(F,np.inf);candidates=[]
    for label in sorted(set(labels)):
        ids=np.flatnonzero(labels==label);own=D[np.ix_(ids,ids)];fown=F[np.ix_(ids,ids)]
        near=np.median(np.sort(own,axis=1)[:,:7],axis=1);fn=np.median(np.sort(fown,axis=1)[:,:7],axis=1)
        sz,fz=robust(near),robust(fn)
        ref=int(ids[np.argmin(np.nanmedian(np.where(np.isfinite(own),own,np.nan),axis=1))])
        for local,index in enumerate(ids):
            if sz[local]<4.5 or fz[local]<3:continue
            other=[float(np.median(np.sort(D[index,labels==g])[:7])) for g in set(labels) if g!=label]
            foreign=int((labels[np.argsort(D[index])[:7]]!=label).sum())
            priority=bool(other and min(other)<near[local] and foreign>=4)
            row=records[index];reference=records[ref];values=row['values'];rv=reference['values']
            candidates.append(dict(id=f"{names[label]} #{row['line']:03d}",label=int(label),gesture=names[label],line=row['line'],file=f'{label}.csv',sha256=sample_hash(values),category='priority' if priority else 'review',
                reason='与同类动作差异较大，且更接近其他手势。请对照曲线确认。' if priority else '与同类动作差异较大，可能是正常变化，建议人工复核。',
                shape_z=float(sz[local]),full_z=float(fz[local]),sample=[values[i:i+2] for i in range(0,200,2)],reference=[rv[i:i+2] for i in range(0,200,2)],reference_id=f"{names[label]} #{reference['line']:03d}"))
    return dict(format=1,gestures=names,source_hashes=hashes,original_counts=counts,candidates=candidates,skipped=skipped,method='trim50-joint-norm-band5-DTW-7NN; robust shape>=4.5 and full>=3; heuristic-v1')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);args=parser.parse_args()
    try:
        names=json.loads((args.run/'gestures.json').read_text(encoding='utf-8'))['names']
        report=analyze(args.run/'data',names,lambda **kw:emit('epoch',phase='checking',**kw))
        report.update(id=args.run.name,date=args.run.name[:8])
        (args.run/'review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        emit('done',message='数据检查完成。',result=dict(complete=True))
    except Exception as exc:
        emit('error',message=str(exc));raise SystemExit(1)
