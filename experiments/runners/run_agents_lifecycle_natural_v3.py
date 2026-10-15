"""Natural SDK function-tool lifecycle pilot; scripted HTTP mock is engineering only."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import httpx
from agents import Agent,function_tool,MaxTurnsExceeded
from agents.models.chatcmpl_converter import Converter
from agents.models.openai_chatcompletions import _to_dump_compatible

from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle,atomic_spans,digest
from experiments.runners.agents_provider_audit_v2 import (RequestLedger,RequestRejected,AuditedTransport,
    SCOPE,request_scope,make_sdk_model,sdk_settings,MODEL,PRICES,sha)
from experiments.runners.agents_lifecycle_tasks_v3 import CANDIDATES,TaskWorkspace,admit,text,sha_bytes,PATH

ROOT=Path(__file__).resolve().parents[2]
INSTRUCTIONS='''Complete a versioned workspace implementation task using function tools. Return final JSON.
First investigate the current policy (historical revision is available for comparison), then close_research with your own useful summary.
After research obtain work_order, derive every requested output using CURRENT policy, write_artifact as a JSON string, and check_artifact.
Current source provenance must include source_path, exact source_version from read receipt, and exact rule quote.
Research tool history may be archived after completion; your summary remains. If relevant policy evidence is missing from visible context,
you may call report_missing("policy") or naturally reread read_policy("current"). Neither route is required.
report_missing("unrelated") is allowed when no relevant evidence is needed. Do not confuse obsolete revision with current policy.
You choose tools and number of steps; finish when your artifact is ready. Public checker checks provenance/schema only; independent evaluator checks correctness after you stop.'''
PROMPT='Investigate the current workspace policy, summarize research, then implement the later work order into artifact.json.'
SOURCES=['experiments/runners/agents_lifecycle_tasks_v3.py','experiments/runners/run_agents_lifecycle_natural_v3.py',
    'experiments/runners/agents_provider_audit_v2.py','context_pruner/adapters/openai_agents_lifecycle_v1.py']


def write(path,obj):Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def file_sha(path):return sha_bytes(Path(path).read_bytes())
def environment():return {**{p:importlib.metadata.version(p) for p in ('openai-agents','openai','httpx')},
    'python_version':sys.version,'python_executable':str(Path(sys.executable).resolve()),'python_sha256':file_sha(sys.executable)}


def run_sdk_and_close(adapter,agent,client):
    # SDK sync and AsyncClient pool must live and close on one explicit loop.
    loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    try:return adapter.run_sync(agent,[{'role':'user','content':PROMPT}],max_turns=24)
    finally:
        try:loop.run_until_complete(client.close())
        finally:loop.close();asyncio.set_event_loop(None)


class SampleLedger(RequestLedger):
    def begin(self,sample_id,phase,body,**meta):
        base=sample_id.split('/turn')[0]
        if sum(r['sample_id'].split('/turn')[0]==base for r in self.records)>=24:
            raise RequestRejected('sample24 request ceiling')
        return super().begin(sample_id,phase,body,**meta)


def build_tools(w):
    def result(payload):return json.dumps(payload,ensure_ascii=False)
    @function_tool
    def read_policy(revision:str)->str:
        """Read policy current or historical revision, returning trusted source metadata."""
        return result(w.read_policy(revision))
    @function_tool
    def close_research(summary:str)->str:
        """Close research after reading current policy; preserve your useful summary."""
        return result(w.close_research(summary))
    @function_tool
    def work_order()->str:
        """Obtain the later implementation work order after research."""
        return result(w.work_order())
    @function_tool
    def write_artifact(content:str)->str:
        """Write JSON string content to artifact.json; never changes source or verifier."""
        return result(w.write_artifact(content))
    @function_tool
    def check_artifact()->str:
        """Check public artifact schema and source provenance, without hidden answer feedback."""
        return result(w.check_artifact())
    @function_tool
    def report_missing(entity:str)->str:
        """Report absent policy evidence or unrelated negative entity; restoration availability varies."""
        return result(w.report_missing(entity))
    return [read_policy,close_research,work_order,write_artifact,check_artifact,report_missing]


class BoundaryAdapter(OpenAIAgentsEvidenceLifecycle):
    def __init__(self,w,arm,sample,out):
        super().__init__(namespace=sample,trusted_tools={'read_policy','close_research','check_artifact','report_missing'},
            current_versions={PATH:w.version},lifecycle_enabled=arm!='A0',recovery_enabled=arm=='A2',
            ttl_model_calls=2,hard_limit_tokens=14000)
        self.w=w;self.sample=sample;self.out=Path(out);self.boundaries=[]

    def __call__(self,data):
        self.w.verify_sources()
        filtered=super().__call__(data)
        messages=Converter.items_to_messages(filtered.input,model=MODEL,base_url='https://api.deepseek.com',strict_feature_validation=True)
        messages.insert(0,{'role':'system','content':filtered.instructions})
        messages=_to_dump_compatible(messages)
        scope={'sample_id':self.sample+'/turn'+str(self.clock),'phase':'initial',
            'sdk_input_sha256':digest(filtered.input),'expected_messages':messages}
        SCOPE.set(scope)
        paired=all(s['complete'] for s in atomic_spans(filtered.input) if s['calls'])
        if not paired:raise ValueError('unpaired visible tools')
        self.boundaries.append({'clock':self.clock,'request_scope':scope['sample_id'],
            'tool_count_before_model':len(self.w.tools),
            'authoritative':deepcopy(data.model_data.input),'visible':deepcopy(filtered.input),
            'input_sha256':scope['sdk_input_sha256'],'paired':paired,
            'group_statuses':{gid:g['status'] for gid,g in self.groups.items()},
            'recovered_groups':self.inputs[-1]['recovered_groups']})
        write(self.out/'model_boundaries.json',self.boundaries)
        return filtered


class BoundTransport(AuditedTransport):
    def __init__(self,ledger,tool_schema_sha,inner=None):
        super().__init__(ledger,inner);self.tool_schema_sha=tool_schema_sha

    async def handle_async_request(self,request):
        body=json.loads(request.content)
        if sha(body.get('tools',[]))!=self.tool_schema_sha:raise RequestRejected('frozen tool schema drift')
        response=await super().handle_async_request(request)
        r=self.ledger.records[-1]
        if r['status']!='returned' or any(r['usage'][k] is None for k in ('prompt_tokens','completion_tokens')):
            raise RequestRejected('accounting response invalid or missing usage')
        return response


class ScriptedMock:
    """Fixed controlled probe ONLY. Paid transport does not instantiate this object."""
    def __init__(self,w):self.w=w;self.step=0
    def __call__(self,request):
        sequence=[('read_policy',{'revision':'historical'}),('read_policy',{'revision':'current'}),
            ('close_research',{'summary':'controlled mock summary'}),('work_order',{}),
            ('report_missing',{'entity':'unrelated'}),('report_missing',{'entity':'policy'}),
            ('write_artifact',{'content':json.dumps(self.w.reference())}),('check_artifact',{})]
        i=self.step;self.step+=1
        if i<len(sequence):
            name,args=sequence[i];message={'role':'assistant','content':None,
                'tool_calls':[{'id':f'call-{i}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
            reason='tool_calls'
        else:message={'role':'assistant','content':'{"done":true}'};reason='stop'
        return httpx.Response(200,json={'id':f'mock-{i}','object':'chat.completion','created':0,'model':MODEL,
            'choices':[{'index':0,'message':message,'finish_reason':reason}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':100}})


def schema(tools):return _to_dump_compatible([Converter.tool_to_openai(t) for t in tools])


def prepare(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    controls=[admit(t,out/'admission'/t['name']) for t in CANDIDATES];chosen=[t for t,c in zip(CANDIDATES,controls) if c['passed']][:2]
    if len(chosen)!=2:raise ValueError('need two offline admitted tasks')
    plan=[{'sample_id':t['name']+'-'+a,'task':t['name'],'arm':a} for i,t in enumerate(chosen) for a in (('A0','A1','A2') if i==0 else ('A2','A0','A1'))]
    write(out/'tasks.json',chosen)
    w=TaskWorkspace(chosen[0],out/'schema-workspace');ts=schema(build_tools(w))
    write(out/'tool_schemas.json',ts)
    initial_bodies={}
    for arm in ('A0','A2'):
        tmp=out/('initial-parity-'+arm);tmp.mkdir();probe=TaskWorkspace(chosen[0],tmp/'workspace')
        ledger=SampleLedger(tmp/'http',max_requests=1,cap_usd=2,max_body_bytes=32000)
        adapter=BoundaryAdapter(probe,arm,'parity-'+arm,tmp)
        model,client=make_sdk_model('offline',BoundTransport(ledger,sha(ts),httpx.MockTransport(lambda req:httpx.Response(200,json={
            'id':'parity','object':'chat.completion','created':0,'model':MODEL,'choices':[{'index':0,'message':{'role':'assistant','content':'{"probe":true}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':1}}))))
        with request_scope('parity','initial'):run_sdk_and_close(adapter,Agent(name='Workspace implementation',instructions=INSTRUCTIONS,model=model,model_settings=sdk_settings(),tools=build_tools(probe)),client)
        initial_bodies[arm]=ledger.records[0]['body_sha256']
    if initial_bodies['A0']!=initial_bodies['A2']:raise ValueError('initial A0/A2 body mismatch')
    f={'schema':'agents_natural_lifecycle_v3','experiment_id':'agents-lifecycle-natural-v3-20261014','tasks':[t['name'] for t in chosen],
        'plan':plan,'admission':controls,'max_requests':144,'sample_max_requests':24,'cap_usd':2.,'max_body_bytes':32000,
        'model':MODEL,'output_max':2048,'temperature':0,'thinking':'disabled','ttl':2,'prices':PRICES,
        'pricing_source':'https://api-docs.deepseek.com/quick_start/pricing/','instructions':INSTRUCTIONS,'prompt':PROMPT,
        'environment':environment(),'sources_sha256':{n:file_sha(ROOT/n) for n in SOURCES},
        'files_sha256':{n:file_sha(out/n) for n in ('tasks.json','tool_schemas.json')},
        'tool_schema_sha256':sha(ts),'initial_body_sha256':initial_bodies['A0'],'initial_body_parity':True,
        'paid_run_allowed':all(c['passed'] for c in controls),'scope':'synthetic natural tool task development; not Django repair or formal confirmation'}
    write(out/'freeze.json',f);(out/'freeze.sha256').write_text(file_sha(out/'freeze.json'),encoding='utf-8');return f


def verify_freeze(out):
    out=Path(out);f=json.loads((out/'freeze.json').read_text(encoding='utf-8'))
    if f['schema']!='agents_natural_lifecycle_v3' or (out/'freeze.sha256').read_text().strip()!=file_sha(out/'freeze.json'):raise ValueError('freeze drift')
    for n,h in f['sources_sha256'].items():
        if file_sha(ROOT/n)!=h:raise ValueError('source drift')
    for n,h in f['files_sha256'].items():
        p=(out/n).resolve()
        if not p.is_relative_to(out.resolve()) or file_sha(p)!=h:raise ValueError('task/schema drift')
    if f['environment']!=environment() or f['instructions']!=INSTRUCTIONS or f['prompt']!=PROMPT:raise ValueError('runtime/prompt drift')
    return f


def trial_report(w,a,arm,sample,ledger,status,final):
    records=[r for r in ledger.records if r['sample_id'].startswith(sample+'/turn')]
    returned={r['sample_id'] for r in records if r['status']=='returned'}
    delivered=[];wrong=False;ttl_removed=False
    for b in a.boundaries:
        if b['request_scope'] not in returned:continue
        vis=[digest(s['items']) for s in atomic_spans(b['visible']) if s['complete']]
        for gid in b['recovered_groups']:
            g=a.groups[gid]
            if g['hash'] in vis:
                delivered.append(gid);wrong=wrong or any(s['source_version']!=w.version for s in g['sources'])
    for gid in set(delivered):
        recovery_clock=next(b['clock'] for b in a.boundaries if gid in b['recovered_groups'])
        ttl_removed=ttl_removed or any(b['request_scope'] in returned and b['clock']>=recovery_clock+2 and b['group_statuses'].get(gid)=='archived'
            and a.groups[gid]['hash'] not in [digest(s['items']) for s in atomic_spans(b['visible']) if s['complete']] for b in a.boundaries)
    first_write=next((i for i,t in enumerate(w.tools) if t['name']=='write_artifact' and t['payload'].get('kind')=='written'),len(w.tools))
    current_gids={gid for gid,g in a.groups.items() if any(s['source_version']==w.version for s in g['sources'])}
    archived_before_write=any(b['request_scope'] in returned and b['tool_count_before_model']<=first_write
        and any(b['group_statuses'].get(gid)=='archived' for gid in current_gids) for b in a.boundaries)
    return {'sample_id':sample,'arm':arm,'task':w.task['name'],'status':status,'final_output':final,
        'independent':w.evaluate(),'sdk_model_calls':a.clock,'http_requests':len(records),'tool_calls':len(w.tools),
        'opportunity_formed':archived_before_write and any(t['name']=='work_order' and t['payload']['kind']=='work_order' for t in w.tools[:first_write]),
        'host_missing_signals':sum(t['payload'].get('origin')=='host' for t in w.tools),
        'autonomous_missing_signals':sum(t['payload'].get('origin')=='model' and t['payload'].get('entity')=='policy' for t in w.tools),
        'negative_signals':sum(t['payload'].get('entity')=='unrelated' for t in w.tools),
        'natural_rereads':w.rereads,'recovery_original_delivered':bool(delivered),'recovered_groups_delivered':list(dict.fromkeys(delivered)),
        'wrong_version_recovered':wrong,'ttl_original_removed':ttl_removed,'ttl_censored':bool(delivered) and not ttl_removed,
        'all_tool_groups_paired':all(b['paired'] for b in a.boundaries),'metrics':a.metrics}


def run_batch(freeze_dir,out,key,*,mock=False):
    f=verify_freeze(freeze_dir);freeze_dir=Path(freeze_dir);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    if not mock and not f['paid_run_allowed']:raise ValueError('paid gate false')
    with (freeze_dir/('mock.claim' if mock else 'paid.claim')).open('x',encoding='utf-8') as c:c.write(str(out.resolve()))
    ledger=SampleLedger(out/'http',max_requests=f['max_requests'],cap_usd=f['cap_usd'],max_body_bytes=f['max_body_bytes'])
    tasks=json.loads((freeze_dir/'tasks.json').read_text(encoding='utf-8'));samples=[];stopped=None
    def save_summary():
        summary={'experiment_id':f['experiment_id'],'mock':mock,'samples_planned':len(f['plan']),
            'samples_completed':sum(x['status'] in {'completed','max_turns'} for x in samples),'samples':samples,
            'provider_requests':0 if mock else len(ledger.records),'http_requests':len(ledger.records),
            'reserved_peak_allmiss_usd':ledger.reserved_usd,'ledger':ledger.records,'stopped':stopped,
            'initial_body_parity':f['initial_body_parity'],'freeze_sha256':file_sha(freeze_dir/'freeze.json')}
        write(out/'summary.json',summary);return summary
    for row in f['plan']:
        trial=out/row['sample_id'];w=a=None;status='completed';final=None
        try:
            verify_freeze(freeze_dir);trial.mkdir();w=TaskWorkspace(next(t for t in tasks if t['name']==row['task']),trial/'workspace')
            a=BoundaryAdapter(w,row['arm'],row['sample_id'],trial);tools=build_tools(w)
            if sha(schema(tools))!=f['tool_schema_sha256']:raise ValueError('constructed tools drift')
            inner=httpx.MockTransport(ScriptedMock(w)) if mock else None
            model,client=make_sdk_model(key,BoundTransport(ledger,f['tool_schema_sha256'],inner))
            with request_scope(row['sample_id'],'initial'):
                result=run_sdk_and_close(a,Agent(name='Workspace implementation',instructions=INSTRUCTIONS,model=model,
                    model_settings=sdk_settings(),tools=tools),client)
            final=result.final_output
        except MaxTurnsExceeded:status='max_turns'
        except Exception as exc:
            status='infrastructure_error';stopped={'sample_id':row['sample_id'],'reason':type(exc).__name__}
        finally:
            report={**row,'status':status,'independent':{'passed':None,'reason':'setup_incomplete'}}
            try:
                if w is not None and a is not None:
                    write(trial/'tool_events.json',w.tools);write(trial/'lifecycle_events.json',a.events);write(trial/'checkpoint.json',a.export_state())
                    report=trial_report(w,a,row['arm'],row['sample_id'],ledger,status,final)
            except Exception as exc:
                report.update(status='infrastructure_error',finalization_error=type(exc).__name__)
                stopped={'sample_id':row['sample_id'],'reason':'finalize_'+type(exc).__name__}
            samples.append(report)
            try:
                if trial.exists():write(trial/'report.json',report)
            finally:summary=save_summary()
        print(f"[{len(samples)}/{len(f['plan'])}] {row['sample_id']} status={report['status']} passed={report['independent']['passed']} requests={len(ledger.records)}",flush=True)
        if stopped:break
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','mock','run'));p.add_argument('--out',type=Path,required=True);p.add_argument('--freeze',type=Path)
    args=p.parse_args()
    if args.mode=='prepare':f=prepare(args.out);print({k:f[k] for k in ('tasks','max_requests','cap_usd','paid_run_allowed')});return
    if args.freeze is None:raise ValueError('--freeze required')
    key='offline' if args.mode=='mock' else os.environ.pop('DEEPSEEK_API_KEY','');os.environ.pop('OPENAI_API_KEY',None)
    if not key:raise ValueError('provider key absent')
    s=run_batch(args.freeze,args.out,key,mock=args.mode=='mock');print({k:s[k] for k in ('samples_completed','provider_requests','reserved_peak_allmiss_usd','stopped')})


if __name__=='__main__':main()
