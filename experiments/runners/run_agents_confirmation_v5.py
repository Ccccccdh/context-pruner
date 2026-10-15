"""Host-phase correction; preserves v4 evidence and shares remaining development cap."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import argparse
import json
import os
from pathlib import Path

from agents.run import ModelInputData
from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners.agents_registry_tasks_v4 import RegistryWorkspace

_ADAPTER=base.StudyAdapter
_PROTOCOL=deepcopy(base.PROTOCOL)
_SOURCE_NAMES=list(base.SOURCE_NAMES)
_VERIFY=base.verify
ORIGINAL_FREEZE=base.ROOT/'runs/agents-confirmation-v4-20261014/development-frozen'
ORIGINAL_SUMMARY=base.ROOT/'runs/agents-confirmation-v4-20261014/development-paid/summary.json'


class HostPhaseWorkspace(RegistryWorkspace):
    def close_research(self,summary):
        self.verify_sources()
        if self.closed:
            return self.record('close_research',{'kind':'stage_complete','stage':'research','already_completed':True,
                'summary':(self.out/'research.txt').read_text(encoding='utf-8'),'next_stage':'implementation','next_tool':'work_order'})
        result=super().close_research(summary)
        if result['kind']=='stage_complete':
            result.update(next_stage='implementation',next_tool='work_order')
            self.tools[-1]['payload']=deepcopy(result)
        return result


class HostPhaseAdapter(_ADAPTER):
    def __call__(self,data):
        if not self.w.closed:state='Research is open. Investigate both current documents and preserve your useful summary.'
        elif not self.w.ordered:state='Research has completed. The remaining task is implementation; obtain work_order. Existing source receipts may be archived.'
        else:state='Research has completed and work_order has been delivered. Implement or correct its artifact using available evidence. Research need not be closed again.'
        # Same source-free trusted phase information to every arm; no facts/answers.
        instructions=data.model_data.instructions+'\nTrusted host lifecycle state: '+state
        updated=replace(data,model_data=ModelInputData(input=data.model_data.input,instructions=instructions))
        return super().__call__(updated)


@contextmanager
def configured():
    saved={n:getattr(base,n) for n in ('RegistryWorkspace','StudyAdapter','PROTOCOL','SOURCE_NAMES','verify')}
    base.RegistryWorkspace=HostPhaseWorkspace;base.StudyAdapter=HostPhaseAdapter
    base.PROTOCOL={**deepcopy(_PROTOCOL),'candidate_version':'v5-host-phase-only'}
    base.SOURCE_NAMES=[*_SOURCE_NAMES,'experiments/runners/run_agents_confirmation_v5.py']
    def checked(path):
        f=_VERIFY(path)
        if f['phase']=='development':check_shared(f)
        return f
    base.verify=checked
    try:yield
    finally:
        for n,value in saved.items():setattr(base,n,value)


def predecessor(path):
    path=Path(path).resolve();s=json.loads(path.read_text(encoding='utf-8'))
    if path!=ORIGINAL_SUMMARY.resolve():raise ValueError('unbound predecessor identity')
    frozen=ORIGINAL_FREEZE;f=json.loads((frozen/'freeze.json').read_text(encoding='utf-8'))
    if base.file_sha(frozen/'freeze.json')!=(frozen/'freeze.sha256').read_text().strip() or s.get('freeze_sha256')!=base.file_sha(frozen/'freeze.json'):
        raise ValueError('predecessor frozen identity drift')
    if f['phase']!='development' or f['protocol']!=_PROTOCOL or f['max_requests']!=288 or f['cap_usd']!=3.25 or f['environment']!=base.environment():raise ValueError('predecessor protocol/runtime drift')
    if Path((frozen/'paid.claim').read_text()).resolve()!=path.parent:raise ValueError('predecessor paid claim drift')
    for n,h in f['sources_sha256'].items():
        if base.file_sha(base.ROOT/n)!=h:raise ValueError('predecessor source drift')
    for n,h in f['files_sha256'].items():
        p=(frozen/n).resolve()
        if not p.is_relative_to(frozen.resolve()) or base.file_sha(p)!=h:raise ValueError('predecessor materials drift')
    if s.get('phase')!='development' or s.get('mock',True) or not isinstance(s.get('stopped'),dict) or s['stopped'].get('reason')!='RequestRejected':
        raise ValueError('not the preserved stopped development predecessor')
    journal=path.parent/'http/requests.jsonl';events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    dispatch=[e for e in events if e['event']=='dispatch'];complete=[{k:v for k,v in e.items() if k!='event'} for e in events if e['event']=='complete']
    if not dispatch or [r['index'] for r in dispatch]!=list(range(1,len(dispatch)+1)) or complete!=s['ledger']:
        raise ValueError('predecessor durable ledger mismatch')
    if len(complete)!=len(dispatch) or any(r.get('transport_kind')!='live' or r['status']!='returned' or r.get('usage_errors') for r in complete):raise ValueError('predecessor HTTP accounting invalid')
    for r in complete:
        body=json.loads((path.parent/'http'/r['request_file']).read_text(encoding='utf-8'))
        response=json.loads((path.parent/'http'/r['response_file']).read_text(encoding='utf-8'))
        if base.sha(body)!=r['body_sha256'] or response.get('usage')!=r['raw_usage']:raise ValueError('predecessor HTTP evidence drift')
    attempts=len(dispatch);reserved=sum(e['reserved_peak_allmiss_usd'] for e in dispatch)
    if abs(reserved-s['reserved_peak_allmiss_usd'])>1e-12:raise ValueError('predecessor reserve mismatch')
    if not 0<=attempts<288 or not 0<=reserved<3.25:raise ValueError('development budget exhausted')
    return {'summary':str(path),'summary_sha256':base.file_sha(path),'journal_sha256':base.file_sha(journal),
        'freeze_sha256':base.file_sha(frozen/'freeze.json'),'requests':attempts,'reserved_usd':reserved}


def check_shared(f):
    b=f['predecessor'];actual=predecessor(b['summary'])
    if b!=actual or f['max_requests']!=288-b['requests'] or abs(f['cap_usd']-(3.25-b['reserved_usd']))>1e-12:raise ValueError('shared budget/predecessor drift')
    if f['operation_claim']!=str((ORIGINAL_FREEZE/'v5-successor.claim').resolve()):raise ValueError('operation reservation path drift')


def seal(out,f):
    base.write(Path(out)/'freeze.json',f);(Path(out)/'freeze.sha256').write_text(base.file_sha(Path(out)/'freeze.json'),encoding='utf-8');return f


def reserve_successor(f,freeze_dir,out):
    p=Path(f['operation_claim'])
    with p.open('x',encoding='utf-8') as claim:
        json.dump({'freeze':str(Path(freeze_dir).resolve()),'out':str(Path(out).resolve()),
            'freeze_sha256':base.file_sha(Path(freeze_dir)/'freeze.json') if (Path(freeze_dir)/'freeze.json').exists() else None,
            'predecessor':f.get('predecessor'), 'request_cap':f.get('max_requests'),'cap_usd':f.get('cap_usd')},claim)


def prepare_development(out,prior):
    b=predecessor(prior)
    with configured():f=base.prepare_development(out)
    f['max_requests']=288-b['requests'];f['cap_usd']=3.25-b['reserved_usd'];f['predecessor']=b
    f['operation_claim']=str((ORIGINAL_FREEZE/'v5-successor.claim').resolve())
    return seal(out,f)


def verify(out):
    with configured():f=base.verify(out)
    if f['phase']=='formal':
        df=Path(f['development_binding']['freeze_path']);verify(df)
        if f['operation_claim']!=str((df/'formal-confirmation.claim').resolve()):raise ValueError('formal operation reservation path drift')
    return f


def prepare_formal(out,dev_freeze,dev_summary):
    verify(dev_freeze)
    reserved=json.loads((ORIGINAL_FREEZE/'v5-successor.claim').read_text(encoding='utf-8'))
    if reserved['freeze']!=str(Path(dev_freeze).resolve()) or reserved['out']!=str(Path(dev_summary).resolve().parent) or reserved['freeze_sha256']!=base.file_sha(Path(dev_freeze)/'freeze.json'):
        raise ValueError('development operation claim mismatch')
    with configured():f=base.prepare_formal(out,dev_freeze,dev_summary)
    f['development_binding']['freeze_path']=str(Path(dev_freeze).resolve());f['operation_claim']=str((Path(dev_freeze)/'formal-confirmation.claim').resolve())
    return seal(out,f)


def run_batch(freeze_dir,out,key,mock=False):
    f=verify(freeze_dir)
    if not mock:reserve_successor(f,freeze_dir,out)
    with configured():s=base.run_batch(freeze_dir,out,key,mock=mock)
    s['candidate_version']='v5-host-phase-only'
    if f['phase']=='development':
        b=f['predecessor'];s['operation_total_requests']=b['requests']+len(s['ledger']);s['operation_reserved_peak_allmiss_usd']=b['reserved_usd']+s['reserved_peak_allmiss_usd']
        if s['operation_total_requests']>288 or s['operation_reserved_peak_allmiss_usd']>3.25:raise ValueError('shared development ceiling exceeded')
    base.write(Path(out)/'summary.json',s)
    with configured():base.write(Path(out)/'analysis.json',base.analyze(s))
    if f['phase']=='development':base.write(Path(out)/'development_gate.json',base.development_gate(s))
    return s


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare-development','prepare-formal','mock','run'));p.add_argument('--out',required=True,type=Path)
    p.add_argument('--freeze',type=Path);p.add_argument('--predecessor',type=Path);p.add_argument('--development-summary',type=Path);a=p.parse_args()
    if a.mode=='prepare-development':f=prepare_development(a.out,a.predecessor);print({k:f[k] for k in ('samples','max_requests','cap_usd')});return
    if a.mode=='prepare-formal':f=prepare_formal(a.out,a.freeze,a.development_summary);print({k:f[k] for k in ('samples','max_requests','cap_usd')});return
    key='offline' if a.mode=='mock' else os.environ.pop('DEEPSEEK_API_KEY','');os.environ.pop('OPENAI_API_KEY',None)
    if not key or a.freeze is None:raise ValueError('key/freeze absent')
    s=run_batch(a.freeze,a.out,key,mock=a.mode=='mock');print({k:s[k] for k in ('phase','samples_completed','provider_requests','stopped')})


if __name__=='__main__':main()
