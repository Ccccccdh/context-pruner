"""Controlled evidence granularity and archive backend development screen."""
from contextlib import contextmanager
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import httpx
from agents import function_tool
from agents.tool_context import ToolContext
from context_pruner.evidence_projection_v8 import project_records,validate_request
from context_pruner.evidence_reader_v7 import resolve_current
from experiments.runners import run_agents_archive_first_v7 as v7
from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners.agents_registry_tasks_v4 import make_task,admission

ROOT=base.ROOT
SEEDS=(401,409,419,421)
OPERATION_CLAIM=ROOT/'runs/agents-minimal-evidence-v8-20261015/paid-operation.claim'
INSTRUCTIONS=base.INSTRUCTIONS+'\nAfter research closes, read_records(entity,codes,"current") can provide source evidence for explicit record codes. Full read_document remains available. Use current source_version in the artifact; payload_sha256 describes only returned text. Choose the evidence you need.'
PROTOCOL={**deepcopy(base.PROTOCOL),'candidate_version':'v8-record-projection',
          'scope':'four-instance development; B0/B1 granularity and B1/B2 backend, common A1 lifecycle',
          'seeds':list(SEEDS),'samples':12,'repeats':1,'cap_usd':2.,
          'response_text_reduction_min':.5,'aggregate_wire_reduction_min':.15,
          'screen_max_geomean_ratio':.90,'screen_min_consistent_instances':3}
SOURCES=[*v7.SOURCES,'context_pruner/evidence_projection_v8.py',
         'experiments/runners/run_agents_minimal_evidence_v8.py','experiments/audits/agents_minimal_evidence_v8_audit.py',
         'experiments/audits/analyze_agents_minimal_evidence_v8.py',
         'docs/AGENTS_V8_PROTOCOL_20261015.md','docs/superpowers/specs/2026-10-15-agents-minimal-evidence-v8-design.md']
_VERIFY=base.verify
_ACTIVE=False


def task_set():return [make_task(s,f'minimalevidence{s}') for s in SEEDS]


def matrix(tasks,repeats=1):
    rows=[];arms=['B0','B1','B2']
    for index,t in enumerate(tasks):
        shift=index%3
        for arm in arms[shift:]+arms[:shift]:rows.append(dict(sample_id=f"{t['name']}-{arm}-r1",task=t['name'],arm=arm,repeat=1))
    return rows


class MinimalWorkspace(v7.ArchiveFirstWorkspace):
    def read_records(self,entity,codes,revision,*,call_id=None):
        self.verify_sources();proof=gid=None;origin='source';reason='ordinary_read'
        try:
            validate_request(entity,codes,revision,self.closed)
            known={r['code'] for r in self.task['documents'][entity]['records']}
            if any(c not in known for c in codes):raise ValueError('unknown code')
        except ValueError as exc:
            payload=self.record('read_records',dict(kind='error',error=str(exc)))
        else:
            snapshot=self.snapshot_provider() if self.arm=='B2' and self.snapshot_provider else None
            resolved=resolve_current(entity,namespace=self.namespace,
                current_versions={f'docs/{e}.json':v for e,v in self.versions.items()},
                archive_snapshot=snapshot,source_loader=self._load_current)
            origin,reason,gid,proof=resolved.origin,resolved.reason,resolved.archive_group_id,resolved.proof
            try:payload=project_records(resolved.payload,entity,codes,revision,self.closed,full=self.arm=='B0')
            except ValueError as exc:payload=dict(kind='error',error=str(exc))
            if payload['kind']=='registry_record_evidence':self.rereads+=1
            payload=self.record('read_records',payload)
        self.host_reads.append(dict(tool_name='read_records',call_id=call_id,tool_index=len(self.tools)-1,
            entity=entity,codes=deepcopy(codes),revision=revision,after_research=self.closed,
            origin=origin,reason=reason,archive_group_id=gid,proof=proof,payload_sha256=base.sha(payload)))
        self.persist_reads();return payload


def tools_for(w):
    tools=v7.tools_for(w)
    @function_tool
    def read_records(ctx:ToolContext,entity:str,codes:list[str],revision:str)->str:
        """After research closes, read explicit current registry codes with source provenance. Full document reads remain available."""
        return json.dumps(w.read_records(entity,codes,revision,call_id=ctx.tool_call_id),ensure_ascii=False)
    return [*tools,read_records]


class MinimalAdapter(v7.ArchiveFirstAdapter):
    def __init__(self,w,arm,sample,out):
        super().__init__(w,'A1',sample,out)
        w.arm=arm if arm in {'B0','B1','B2'} else 'B0'
        w.archive_enabled=False


class ControlledMock:
    """Reference answer only in mock engineering probe; never live transport."""
    def __init__(self,w):self.w=w;self.step=0
    def __call__(self,request):
        sequence=[('read_document',dict(entity='north',revision='current')),('read_document',dict(entity='south',revision='current')),
                  ('close_research',dict(summary='controlled short summary')),('work_order',{})]
        for e in ('north','south'):
            sequence.append(('read_records',dict(entity=e,codes=[q['code'] for q in self.w.task['queries'] if q['entity']==e],revision='current')))
        sequence.extend([('write_artifact',dict(content=json.dumps(self.w.reference()))),('check_artifact',{})])
        i=self.step;self.step+=1;message=dict(role='assistant',content='{"done":true}')
        if i<len(sequence):
            name,args=sequence[i];message=dict(role='assistant',content=None,tool_calls=[dict(id=f'c-{i}',type='function',function=dict(name=name,arguments=json.dumps(args)))])
        return httpx.Response(200,json=dict(id=f'mock-{i}',object='chat.completion',created=0,model=base.MODEL,
            choices=[dict(index=0,message=message,finish_reason='tool_calls' if i<len(sequence) else 'stop')],
            usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120,prompt_cache_hit_tokens=0,prompt_cache_miss_tokens=100)))


@contextmanager
def configured():
    global _ACTIVE
    if _ACTIVE:raise RuntimeError('serial nonnested V8 operations required')
    saved={k:getattr(base,k) for k in ('RegistryWorkspace','StudyAdapter','PROTOCOL','SOURCE_NAMES','verify','matrix','tools_for','ControlledMock','INSTRUCTIONS')}
    _ACTIVE=True
    try:
        base.RegistryWorkspace=MinimalWorkspace;base.StudyAdapter=MinimalAdapter;base.PROTOCOL=PROTOCOL
        base.SOURCE_NAMES=SOURCES;base.verify=verify;base.matrix=matrix;base.tools_for=tools_for
        base.ControlledMock=ControlledMock;base.INSTRUCTIONS=INSTRUCTIONS
        yield
    finally:
        for k,v in saved.items():setattr(base,k,v)
        _ACTIVE=False


def prepare(out):
    out=Path(out);controls=out.parent/(out.name+'-controls');controls.mkdir(parents=True,exist_ok=False)
    tasks=task_set();admitted=[admission(t,controls/t['name']) for t in tasks]
    if not all(a['passed'] for a in admitted):raise ValueError('offline admission failed')
    with configured():f=base.prepare(out,'development',tasks,admitted)
    f['cap_usd']=2.;base.write(out/'freeze.json',f);(out/'freeze.sha256').write_text(base.file_sha(out/'freeze.json'))
    return verify(out)


def verify(out):
    out=Path(out);saved=(base.PROTOCOL,base.INSTRUCTIONS)
    base.PROTOCOL=PROTOCOL;base.INSTRUCTIONS=INSTRUCTIONS
    try:f=_VERIFY(out)
    finally:base.PROTOCOL,base.INSTRUCTIONS=saved
    expected=dict(phase='development',samples=12,max_requests=192,cap_usd=2.,max_body_bytes=24000,
                  model=base.MODEL,output=2048,temperature=0,thinking='disabled',prices=base.PRICES,plan=matrix(task_set()))
    if any(f.get(k)!=v for k,v in expected.items()) or json.loads((out/'tasks.json').read_text())!=task_set():raise ValueError('V8 design/cap drift')
    if set(f['sources_sha256'])!=set(SOURCES) or set(f['files_sha256'])!={'tasks.json','tools.json'}:raise ValueError('manifest omission')
    if base.sha(json.loads((out/'tools.json').read_text()))!=f['tool_schema_sha256']:raise ValueError('schema drift')
    if f['admission']!=[dict(task=t['name'],passed=True,reference_passed=True,empty_passed=False,historical_passed=False) for t in task_set()]:raise ValueError('admission drift')
    return f


def reserve_operation(freeze,out):
    verify(freeze);OPERATION_CLAIM.parent.mkdir(parents=True,exist_ok=True)
    with OPERATION_CLAIM.open('x',encoding='utf-8') as stream:
        json.dump(dict(freeze=str(Path(freeze).resolve()),out=str(Path(out).resolve()),freeze_sha256=base.file_sha(Path(freeze)/'freeze.json'),max_requests=192,cap_usd=2.),stream)


def audit_output(freeze,out,*,mock):
    from experiments.audits.agents_minimal_evidence_v8_audit import audit
    return audit(Path(freeze),Path(out),verify(freeze),OPERATION_CLAIM,task_set(),mock=mock)


def run_batch(freeze,out,key,*,mock=False):
    verify(freeze)
    if not mock:
        gate=audit_output(freeze,Path((Path(freeze)/'mock.claim').read_text()),mock=True)
        if not gate['offline_gate_passed']:raise ValueError('V8 offline gate failed')
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
