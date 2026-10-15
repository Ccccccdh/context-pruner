"""Archive-first host read facade; independent expanded development, no hidden retries."""
from contextlib import contextmanager
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path

import httpx
from agents import function_tool
from agents.tool_context import ToolContext
from context_pruner.evidence_reader_v7 import resolve_current
from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners import run_agents_confirmation_v5 as v5
from experiments.runners import run_agents_receipt_manifest_v6 as v6
from experiments.runners.agents_registry_tasks_v4 import make_task, admission, dump

ROOT=base.ROOT
SEEDS=(307,311,313,317)
OPERATION_CLAIM=ROOT/'runs/agents-archive-first-v7-20261015/paid-operation.claim'
OLD_TOOLS=base.tools_for
_VERIFY,_MATRIX=base.verify,base.matrix
_ACTIVE=False
PROTOCOL={**deepcopy(base.PROTOCOL),'candidate_version':'v7-host-archive-first',
          'scope':'four synthetic development instances; host reuse, not autonomous recovery or formal confirmation',
          'seeds':list(SEEDS),'repeats':1,'samples':12,'cap_usd':2.}
SOURCES=[*v6.SOURCES,'context_pruner/evidence_reader_v7.py',
         'experiments/runners/run_agents_archive_first_v7.py','experiments/audits/agents_archive_first_v7_audit.py',
         'docs/AGENTS_V7_PROTOCOL_20261015.md','docs/superpowers/specs/2026-10-15-agents-unified-evidence-read-design.md']


def snapshot_from_adapter(adapter):
    return dict(namespace=adapter.namespace,groups=deepcopy(adapter.groups),receipts=deepcopy(adapter.receipts),
                trusted_tools=sorted(adapter.trusted_tools),entries={entry.archive_id:
                dict(text=entry.item.text,metadata=deepcopy(entry.item.metadata),source_event_ids=list(entry.item.source_event_ids))
                for entry in adapter.archive_store.entries()})


class ArchiveFirstWorkspace(v5.HostPhaseWorkspace):
    def __init__(self,task,out):
        self.validation_calls=0;self.payload_constructs=0;self.host_reads=[]
        self.archive_enabled=False;self.snapshot_provider=None;self.namespace='unbound'
        super().__init__(task,out)
        self.persist_reads()

    def verify_sources(self):
        self.validation_calls+=1
        try:
            return super().verify_sources()
        finally:
            self.persist_reads()

    def persist_reads(self):
        base.write(self.out.parent/'host_reads.json',self.host_reads)
        base.write(self.out.parent/'host_read_metrics.json',dict(source_validation_calls=self.validation_calls,source_payload_constructs=self.payload_constructs))

    def _load_current(self,entity):
        self.payload_constructs+=1
        return dict(kind='evidence',source_path=f'docs/{entity}.json',source_version=self.versions[entity],
                    entity=entity,stage='research',text=dump(self.task['documents'][entity]))

    def read_document(self,entity,revision,*,call_id=None):
        closed=self.closed
        if closed and revision=='current' and entity in self.entities:
            self.verify_sources();self.reads.add(entity);self.rereads+=1
            snapshot=self.snapshot_provider() if self.archive_enabled and self.snapshot_provider else None
            resolved=resolve_current(entity,namespace=self.namespace,current_versions={f'docs/{e}.json':v for e,v in self.versions.items()},
                                     archive_snapshot=snapshot,source_loader=self._load_current)
            payload=self.record('read_document',resolved.payload,revision=revision,reread=True)
            origin,reason,gid,proof=resolved.origin,resolved.reason,resolved.archive_group_id,resolved.proof
        else:
            payload=super().read_document(entity,revision)
            if payload.get('kind')=='evidence':self.payload_constructs+=1
            origin,reason,gid,proof='source','ordinary_read',None,None
        self.host_reads.append(dict(call_id=call_id,tool_index=len(self.tools)-1,entity=entity,revision=revision,
                                    after_research=closed,origin=origin,reason=reason,archive_group_id=gid,
                                    payload_sha256=base.sha(payload),proof=proof))
        self.persist_reads()
        return payload


def tools_for(workspace):
    tools=OLD_TOOLS(workspace)
    @function_tool(description_override=tools[0].description)
    def read_document(ctx:ToolContext,entity:str,revision:str)->str:
        return json.dumps(workspace.read_document(entity,revision,call_id=ctx.tool_call_id),ensure_ascii=False)
    return [read_document,*tools[1:]]


class ArchiveFirstAdapter(v6.ReceiptStudyAdapter):
    def __init__(self,w,arm,sample,out):
        super().__init__(w,arm,sample,out)
        w.namespace=self.namespace;w.arm=arm;w.archive_enabled=arm=='A2';w.snapshot_provider=lambda:snapshot_from_adapter(self)

    def __call__(self,data):
        result=super().__call__(data)
        self.boundaries[-1]['host_read_events_before_model']=len(self.w.host_reads)
        self.w.persist_reads();base.write(self.out/'model_boundaries.json',self.boundaries)
        return result


class ControlledMock:
    """Fixed read schedule and reference answer are engineering-only, never live."""
    def __init__(self,w):self.w=w;self.step=0
    def __call__(self,request):
        sequence=[('read_document',{'entity':'north','revision':'historical'}),
                  ('read_document',{'entity':'north','revision':'current'}),('read_document',{'entity':'south','revision':'current'}),
                  ('close_research',{'summary':'controlled short summary'}),('work_order',{}),
                  ('read_document',{'entity':'north','revision':'current'}),('read_document',{'entity':'south','revision':'current'}),
                  ('write_artifact',{'content':json.dumps(self.w.reference())}),('check_artifact',{})]
        if self.w.arm=='A0':
            # Retained full sources suffice. Artificially duplicating them would
            # exceed the unchanged wire guard, rather than exercise host reuse.
            sequence=[step for index,step in enumerate(sequence) if index not in {5,6}]
        i=self.step;self.step+=1
        message={'role':'assistant','content':'{"done":true}'}
        if i<len(sequence):
            name,args=sequence[i];message={'role':'assistant','content':None,'tool_calls':[
                {'id':f'c-{i}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        return httpx.Response(200,json={'id':f'mock-{i}','object':'chat.completion','created':0,'model':base.MODEL,
            'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if i<len(sequence) else 'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':100}})


@contextmanager
def configured():
    global _ACTIVE
    if _ACTIVE:raise RuntimeError('serial nonnested V7 operation required')
    saved={key:getattr(base,key) for key in ('RegistryWorkspace','StudyAdapter','PROTOCOL','SOURCE_NAMES','verify','matrix','tools_for','ControlledMock')}
    _ACTIVE=True
    try:
        base.RegistryWorkspace=ArchiveFirstWorkspace;base.StudyAdapter=ArchiveFirstAdapter;base.PROTOCOL=PROTOCOL
        base.SOURCE_NAMES=SOURCES;base.verify=verify;base.matrix=lambda tasks,repeats:_MATRIX(tasks,1)
        base.tools_for=tools_for;base.ControlledMock=ControlledMock
        yield
    finally:
        for key,value in saved.items():setattr(base,key,value)
        _ACTIVE=False


def task_set():return [make_task(seed,f'archivefirst{seed}') for seed in SEEDS]


def prepare(out):
    out=Path(out);controls=out.parent/(out.name+'-controls');controls.mkdir(parents=True,exist_ok=False)
    tasks=task_set();admitted=[admission(t,controls/t['name']) for t in tasks]
    if not all(a['passed'] for a in admitted):raise ValueError('offline admission failed')
    with configured():f=base.prepare(out,'development',tasks,admitted)
    f['cap_usd']=2.;base.write(out/'freeze.json',f);(out/'freeze.sha256').write_text(base.file_sha(out/'freeze.json'))
    return verify(out)


def verify(out):
    out=Path(out);saved=base.PROTOCOL;base.PROTOCOL=PROTOCOL
    try:f=_VERIFY(out)
    finally:base.PROTOCOL=saved
    expected=dict(phase='development',samples=12,max_requests=192,cap_usd=2.,max_body_bytes=24000,
                  model=base.MODEL,output=2048,temperature=0,thinking='disabled',prices=base.PRICES,plan=_MATRIX(task_set(),1))
    if any(f.get(k)!=v for k,v in expected.items()) or json.loads((out/'tasks.json').read_text())!=task_set():raise ValueError('V7 design/cap drift')
    if set(f['sources_sha256'])!=set(SOURCES) or set(f['files_sha256'])!={'tasks.json','tools.json'}:raise ValueError('source/material manifest omission')
    if base.sha(json.loads((out/'tools.json').read_text()))!=f['tool_schema_sha256']:raise ValueError('tool schema drift')
    return f


def reserve_operation(freeze,out):
    verify(freeze);OPERATION_CLAIM.parent.mkdir(parents=True,exist_ok=True)
    with OPERATION_CLAIM.open('x',encoding='utf-8') as stream:
        json.dump(dict(freeze=str(Path(freeze).resolve()),out=str(Path(out).resolve()),
                       freeze_sha256=base.file_sha(Path(freeze)/'freeze.json'),max_requests=192,cap_usd=2.),stream)


def audit_output(freeze,out,*,mock):
    from experiments.audits.agents_archive_first_v7_audit import audit
    return audit(Path(freeze),Path(out),verify(freeze),OPERATION_CLAIM,task_set(),mock=mock)


def run_batch(freeze,out,key,*,mock=False):
    verify(freeze)
    if not mock:
        proof=audit_output(freeze,Path((Path(freeze)/'mock.claim').read_text()),mock=True)
        if not proof['complete'] or proof['passed']!=12 or proof['host_archive_hits_delivered']!=8 or proof['mock_A1_A2_equal_body_pairs']!=40:
            raise ValueError('V7 offline mechanism/equivalence gate failed')
        reserve_operation(freeze,out)
    with configured():summary=base.run_batch(freeze,out,key,mock=mock)
    try:result=audit_output(freeze,out,mock=mock)
    except Exception as exc:
        base.write(Path(out)/'audit.json',dict(complete=False,error=type(exc).__name__,reason=str(exc),planned_samples=12,
                    observed_samples=len(summary['samples']),provider_requests=summary['provider_requests'],reserved_usd=summary['reserved_peak_allmiss_usd']))
        raise
    base.write(Path(out)/'audit.json',result)
    return summary


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
