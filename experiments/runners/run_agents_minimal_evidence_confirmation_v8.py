"""48 unseen registry instances; frozen V8 behavior, fixed confirmation protocol."""
from contextlib import contextmanager
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
from experiments.runners import run_agents_minimal_evidence_v8 as v8
from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners.agents_registry_tasks_v4 import make_task,admission

ROOT=base.ROOT
RUN=ROOT/'runs/agents-minimal-evidence-confirmation-v8-20261015'
PREDECESSOR=ROOT/'runs/agents-minimal-evidence-v8-20261015/completion_manifest.json'
PREDECESSOR_SHA='33c510d420a83b6ade1c5dfcec9acc55f48db7e21773fdd66af85bc62c59beb8'
HISTORY_LIST=RUN/'registry-history-files.txt'
OPERATION_CLAIM=RUN/'paid-operation.claim'
SEEDS=tuple(range(20001,20049))
ControlledMock=v8.ControlledMock
_OBSOLETE={'quality_interval','cost_test','formal_seeds','formal_repeats','development_repeats',
           'screen_max_geomean_ratio','screen_min_consistent_instances'}
PROTOCOL={**{k:deepcopy(v) for k,v in v8.PROTOCOL.items() if k not in _OBSOLETE},'candidate_version':'frozen-v8-confirmation',
          'scope':'48 unseen synthetic registry instances; preregistered confirmation; not new task families',
          'seeds':list(SEEDS),'samples':144,'cap_usd':30.,'statistics':'CPalpha.025perarm;pairedlogStudentt95%upper;allgatesjointlyrequired'}
SOURCES=[*v8.SOURCES,'experiments/runners/run_agents_minimal_evidence_confirmation_v8.py',
         'experiments/audits/agents_v8_confirmation_audit.py','experiments/audits/analyze_agents_v8_confirmation.py',
         'experiments/audits/agents_v8_confirmation_statistics.py','docs/AGENTS_V8_CONFIRMATION_PROTOCOL_20261015.md',
         str(HISTORY_LIST.relative_to(ROOT).as_posix())]
_VERIFY=base.verify
_ACTIVE=False


def task_set():return [make_task(s,f'confirmv8{s}') for s in SEEDS]


def predecessor(full=False):
    if base.file_sha(PREDECESSOR)!=PREDECESSOR_SHA:raise ValueError('unbound V8 predecessor')
    manifest=json.loads(PREDECESSOR.read_text())
    if len(manifest)!=814:raise ValueError('predecessor manifest omission')
    if full and any(base.file_sha(ROOT/n)!=h for n,h in manifest.items()):raise ValueError('predecessor artifact changed')
    return manifest


def history_snapshot():
    result={};seen=set()
    for raw in HISTORY_LIST.read_text(encoding='utf-8-sig').splitlines():
        p=Path(raw).resolve()
        if not p.is_relative_to((ROOT/'runs').resolve()):raise ValueError('history path outside runs')
        values=json.loads(p.read_text(encoding='utf-8-sig'))
        if isinstance(values,list):seen.update(t['seed'] for t in values if isinstance(t,dict) and 'seed' in t)
        result[p.relative_to(ROOT).as_posix()]=base.file_sha(p)
    if not result or seen.intersection(SEEDS):raise ValueError('confirmation seeds overlap prior history or history missing')
    return dict(files_sha256=result,observed_seeds=sorted(seen),proposed_seeds=list(SEEDS),overlap=[])


@contextmanager
def configured():
    global _ACTIVE
    if _ACTIVE:raise RuntimeError('serial confirmation operations required')
    saved={k:getattr(base,k) for k in ('RegistryWorkspace','StudyAdapter','PROTOCOL','SOURCE_NAMES','verify','matrix','tools_for','ControlledMock','INSTRUCTIONS')}
    _ACTIVE=True
    try:
        base.RegistryWorkspace=v8.MinimalWorkspace;base.StudyAdapter=v8.MinimalAdapter;base.PROTOCOL=PROTOCOL
        base.SOURCE_NAMES=SOURCES;base.verify=verify;base.matrix=v8.matrix;base.tools_for=v8.tools_for
        base.ControlledMock=ControlledMock;base.INSTRUCTIONS=v8.INSTRUCTIONS
        yield
    finally:
        for k,v in saved.items():setattr(base,k,v)
        _ACTIVE=False


def prepare(out):
    predecessor(full=True);history=history_snapshot();out=Path(out)
    controls=out.parent/(out.name+'-controls');controls.mkdir(parents=True,exist_ok=False)
    tasks=task_set();admitted=[admission(t,controls/t['name']) for t in tasks]
    if not all(a['passed'] for a in admitted):raise ValueError('offline admission failed')
    with configured():f=base.prepare(out,'confirmation',tasks,admitted)
    base.write(out/'history.json',history)
    f.update(cap_usd=30.,predecessor_sha256=PREDECESSOR_SHA)
    f['files_sha256']['history.json']=base.file_sha(out/'history.json')
    base.write(out/'freeze.json',f);(out/'freeze.sha256').write_text(base.file_sha(out/'freeze.json'))
    return verify(out)


def verify(out):
    out=Path(out);old=predecessor();saved=base.PROTOCOL,base.INSTRUCTIONS
    base.PROTOCOL=PROTOCOL;base.INSTRUCTIONS=v8.INSTRUCTIONS
    try:f=_VERIFY(out)
    finally:base.PROTOCOL,base.INSTRUCTIONS=saved
    expected=dict(phase='confirmation',samples=144,max_requests=2304,cap_usd=30.,max_body_bytes=24000,
                  model=base.MODEL,output=2048,temperature=0,thinking='disabled',prices=base.PRICES,
                  plan=v8.matrix(task_set()),predecessor_sha256=PREDECESSOR_SHA)
    if any(f.get(k)!=v for k,v in expected.items()) or json.loads((out/'tasks.json').read_text())!=task_set():raise ValueError('confirmation cohort/cap drift')
    if set(f['sources_sha256'])!=set(SOURCES) or set(f['files_sha256'])!={'tasks.json','tools.json','history.json'}:raise ValueError('confirmation manifest omission')
    if any(f['sources_sha256'][n]!=old[n] for n in v8.SOURCES):raise ValueError('frozen V8 candidate changed')
    if base.sha(json.loads((out/'tools.json').read_text()))!=f['tool_schema_sha256']:raise ValueError('schema drift')
    if f['admission']!=[dict(task=t['name'],passed=True,reference_passed=True,empty_passed=False,historical_passed=False) for t in task_set()]:raise ValueError('admission drift')
    history=json.loads((out/'history.json').read_text())
    if history['proposed_seeds']!=list(SEEDS) or history['overlap'] or set(history['observed_seeds']).intersection(SEEDS):raise ValueError('heldout identity drift')
    if history!=history_snapshot():raise ValueError('historical material changed')
    return f


def reserve_operation(freeze,out):
    verify(freeze);OPERATION_CLAIM.parent.mkdir(parents=True,exist_ok=True)
    with OPERATION_CLAIM.open('x',encoding='utf-8') as stream:
        json.dump(dict(freeze=str(Path(freeze).resolve()),out=str(Path(out).resolve()),freeze_sha256=base.file_sha(Path(freeze)/'freeze.json'),max_requests=2304,cap_usd=30.),stream)


def audit_output(freeze,out,*,mock):
    from experiments.audits.agents_v8_confirmation_audit import audit
    return audit(Path(freeze),Path(out),verify(freeze),OPERATION_CLAIM,task_set(),mock=mock)


def run_batch(freeze,out,key,*,mock=False):
    verify(freeze)
    if not mock:
        predecessor(full=True)
        gate=audit_output(freeze,Path((Path(freeze)/'mock.claim').read_text()),mock=True)
        if not gate['offline_gate_passed']:raise ValueError('confirmation offline gate failed')
        reserve_operation(freeze,out)
    with configured():summary=base.run_batch(freeze,out,key,mock=mock)
    try:result=audit_output(freeze,out,mock=mock)
    except Exception as exc:
        base.write(Path(out)/'audit.json',dict(complete=False,error=type(exc).__name__,reason=str(exc)));raise
    base.write(Path(out)/'audit.json',result);return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('prepare','mock','run','audit'))
    p.add_argument('--freeze',required=True,type=Path);p.add_argument('--out',type=Path);args=p.parse_args()
    if args.mode=='prepare':result=prepare(args.freeze)
    elif args.mode=='audit':result=audit_output(args.freeze,args.out,mock=False)
    else:
        key='offline' if args.mode=='mock' else os.environ.get('DEEPSEEK_API_KEY')
        if not key:raise RuntimeError('provider key unavailable')
        s=run_batch(args.freeze,args.out,key,mock=args.mode=='mock')
        result={k:s[k] for k in ('samples_completed','provider_requests','reserved_peak_allmiss_usd','stopped')}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
