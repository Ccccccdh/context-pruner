"""Immutable development admission and held-out, preregistered small confirmation."""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
import itertools
import json
import math
import os
from pathlib import Path

import httpx
from agents import Agent,function_tool,MaxTurnsExceeded
from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle,atomic_spans,digest
from experiments.runners.agents_provider_audit_v2 import RequestLedger,RequestRejected,request_scope,make_sdk_model,sdk_settings,MODEL,PRICES,sha,SCOPE
from experiments.runners.run_agents_lifecycle_natural_v3 import BoundaryAdapter,BoundTransport,schema,environment,file_sha,write
from experiments.runners.agents_registry_tasks_v4 import make_task,RegistryWorkspace,admission

ROOT=Path(__file__).resolve().parents[2]
INSTRUCTIONS='''Complete a versioned registry workspace task using function tools, then return final JSON.
Investigate both north and south CURRENT documents and preserve your own useful summary with close_research.
Then obtain work_order and implement exactly its requested rows in artifact.json using current multiplier,bias,opaque label and receipt version.
Historical revisions are available for comparison but must not govern current work. Research history may be archived; your summary remains.
If specific registry evidence is absent from visible context, retrieve_evidence(entity) requests relevant archived original evidence when available.
You may instead reread read_document(entity,"current"), or use your retained summary. Choose the appropriate path; neither recovery nor rereading is required.
Unrelated retrieval is a permitted negative signal. Tools cannot return independent expected answers. check_artifact checks schema/provenance,
and final independent value/label evaluation happens after you stop. Write valid JSON as a string; finish when artifact is ready.'''
PROMPT='Investigate both current registries, preserve useful research, and implement the later work order.'
PROTOCOL={'formal_seeds':[1009,1013,1019],'formal_repeats':5,'development_repeats':3,
 'quality_noninferiority_margin':.10,'quality_interval':'four Bonferroni CP bounds alpha=.0125 on task ALL-five-pass indicators',
 'cost_test':'exact one-sided task-level sign flips of mean log(A2/A0) usage-priced cost;3 clusters,min p=.125',
 'no_tuning':True,'no_favorable_task_addition':True,'scope':'held-out synthetic registry instances; not unseen task families or Django repair'}
SOURCE_NAMES=['experiments/runners/agents_registry_tasks_v4.py','experiments/runners/run_agents_confirmation_v4.py',
 'experiments/runners/agents_provider_audit_v2.py','context_pruner/adapters/openai_agents_lifecycle_v1.py',
 'experiments/runners/run_agents_lifecycle_natural_v3.py','experiments/runners/agents_lifecycle_tasks_v3.py']


class StudyLedger(RequestLedger):
    def begin(self,sample_id,phase,body,**meta):
        if sum(r['sample_id'].split('/turn')[0]==sample_id.split('/turn')[0] for r in self.records)>=16:raise RequestRejected('sample16 ceiling')
        return super().begin(sample_id,phase,body,**meta)


class StudyAdapter(BoundaryAdapter):
    def __init__(self,w,arm,sample,out):
        OpenAIAgentsEvidenceLifecycle.__init__(self,namespace=sample,
            trusted_tools={'read_document','close_research','retrieve_evidence','check_artifact'},
            current_versions={f'docs/{e}.json':v for e,v in w.versions.items()},lifecycle_enabled=arm!='A0',recovery_enabled=arm=='A2',
            ttl_model_calls=2,hard_limit_tokens=24000)
        self.w=w;self.sample=sample;self.out=Path(out);self.boundaries=[]


class StudyTransport(BoundTransport):
    def __init__(self,ledger,tool_sha,initial_sha,inner=None):
        super().__init__(ledger,tool_sha,inner);self.initial_sha=initial_sha
    async def handle_async_request(self,request):
        scope=SCOPE.get()
        if scope and scope['sample_id'].endswith('/turn1') and sha(json.loads(request.content))!=self.initial_sha:
            raise RequestRejected('initial frozen converted body drift')
        return await super().handle_async_request(request)


def tools_for(w):
    def result(x):return json.dumps(x,ensure_ascii=False)
    @function_tool
    def read_document(entity:str,revision:str)->str:
        """Read north/south current or historical registry source with host provenance; natural reread is allowed."""
        return result(w.read_document(entity,revision))
    @function_tool
    def close_research(summary:str)->str:
        """Close research after both current reads; preserve arbitrary useful summary for later work."""
        return result(w.close_research(summary))
    @function_tool
    def work_order()->str:
        """Obtain late requested records and output schema, after research closes."""
        return result(w.work_order())
    @function_tool
    def retrieve_evidence(entity:str)->str:
        """Request archived original north/south evidence absent from visible context, or unrelated negative entity. Availability varies."""
        return result(w.retrieve_evidence(entity))
    @function_tool
    def write_artifact(content:str)->str:
        """Write a JSON string to artifact.json only; cannot alter source or verifier."""
        return result(w.write_artifact(content))
    @function_tool
    def check_artifact()->str:
        """Check exact row shape and current provenance, without revealing independent expected values or labels."""
        return result(w.check_artifact())
    return [read_document,close_research,work_order,retrieve_evidence,write_artifact,check_artifact]


def sync_run(a,agent,client):
    loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    try:return a.run_sync(agent,[{'role':'user','content':PROMPT}],max_turns=16)
    finally:
        try:loop.run_until_complete(client.close())
        finally:loop.close();asyncio.set_event_loop(None)


class ControlledMock:
    """Engineering only; reference outputs never used in live transport."""
    def __init__(self,w):self.w=w;self.step=0
    def __call__(self,request):
        seq=[('read_document',{'entity':'north','revision':'historical'}),('read_document',{'entity':'north','revision':'current'}),
            ('read_document',{'entity':'south','revision':'current'}),('close_research',{'summary':'controlled engineering summary'}),
            ('work_order',{}),('retrieve_evidence',{'entity':'unrelated'}),('retrieve_evidence',{'entity':'north'}),
            ('retrieve_evidence',{'entity':'south'}),('write_artifact',{'content':json.dumps(self.w.reference())}),('check_artifact',{})]
        i=self.step;self.step+=1
        message={'role':'assistant','content':'{"done":true}'}
        if i<len(seq):
            n,arg=seq[i];message={'role':'assistant','content':None,'tool_calls':[{'id':f'c-{i}','type':'function','function':{'name':n,'arguments':json.dumps(arg)}}]}
        return httpx.Response(200,json={'id':f'mock-{i}','object':'chat.completion','created':0,'model':MODEL,
            'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if i<len(seq) else 'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':100}})


def matrix(tasks,repeats):
    result=[];arms=['A0','A1','A2']
    for repeat in range(1,repeats+1):
        for ti,t in enumerate(tasks):
            shift=(repeat-1+ti)%3
            for a in arms[shift:]+arms[:shift]:result.append({'sample_id':t['name']+'-'+a+f'-r{repeat}','task':t['name'],'arm':a,'repeat':repeat})
    return result


def prepare(out,phase,tasks,admitted,dev_binding=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);repeats=3 if phase=='development' else 5
    write(out/'tasks.json',tasks);w=RegistryWorkspace(tasks[0],out/'schema-workspace');ts=schema(tools_for(w));write(out/'tools.json',ts)
    parity=[]
    for arm in ('A0','A2'):
        p=out/('parity-'+arm);p.mkdir();w=RegistryWorkspace(tasks[0],p/'workspace');a=StudyAdapter(w,arm,'parity',p)
        ledger=StudyLedger(p/'http',max_requests=1,cap_usd=1,max_body_bytes=24000)
        def stop(req):return httpx.Response(200,json={'id':'parity','object':'chat.completion','created':0,'model':MODEL,
            'choices':[{'index':0,'message':{'role':'assistant','content':'{"ready":true}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':1}})
        model,client=make_sdk_model('offline',BoundTransport(ledger,sha(ts),httpx.MockTransport(stop)))
        with request_scope('parity','initial'):sync_run(a,Agent(name='Registry implementation',instructions=INSTRUCTIONS,model=model,model_settings=sdk_settings(),tools=tools_for(w)),client)
        parity.append(ledger.records[0]['body_sha256'])
    if parity[0]!=parity[1]:raise ValueError('initial arm parity violated')
    plan=matrix(tasks,repeats)
    f={'schema':'agents_confirmation_v4','phase':phase,'protocol':PROTOCOL,'instructions':INSTRUCTIONS,'prompt':PROMPT,
       'tasks':[t['name'] for t in tasks],'plan':plan,'samples':len(plan),'max_requests':len(plan)*16,
       'cap_usd':3.25 if phase=='development' else 8.,'max_body_bytes':24000,'model':MODEL,'output':2048,'temperature':0,'thinking':'disabled',
       'prices':PRICES,'pricing_source':'https://api-docs.deepseek.com/quick_start/pricing/','environment':environment(),
       'sources_sha256':{n:file_sha(ROOT/n) for n in SOURCE_NAMES},'files_sha256':{n:file_sha(out/n) for n in ('tasks.json','tools.json')},
       'tool_schema_sha256':sha(ts),'initial_body_sha256':parity[0],'admission':admitted,'development_binding':dev_binding,
       'paid_run_allowed':all(x['passed'] for x in admitted)}
    write(out/'freeze.json',f);(out/'freeze.sha256').write_text(file_sha(out/'freeze.json'),encoding='utf-8');return f


def prepare_development(out):
    out=Path(out);controls_out=out.parent/(out.name+'-controls');controls_out.mkdir(exist_ok=False)
    tasks=[make_task(n,'dev'+str(n)) for n in (101,103,107)];controls=[admission(t,controls_out/t['name']) for t in tasks]
    selected=[t for t,c in zip(tasks,controls) if c['passed']][:2]
    if len(selected)!=2:raise ValueError('offline development task admission failed')
    return prepare(out,'development',selected,controls)


def verify(out):
    out=Path(out);f=json.loads((out/'freeze.json').read_text(encoding='utf-8'))
    if f['schema']!='agents_confirmation_v4' or file_sha(out/'freeze.json')!=(out/'freeze.sha256').read_text().strip():raise ValueError('freeze hash drift')
    if f['protocol']!=PROTOCOL or f['instructions']!=INSTRUCTIONS or f['prompt']!=PROMPT or f['environment']!=environment():raise ValueError('protocol/runtime drift')
    for n,h in f['sources_sha256'].items():
        if file_sha(ROOT/n)!=h:raise ValueError('candidate source drift')
    for n,h in f['files_sha256'].items():
        p=(out/n).resolve()
        if not p.is_relative_to(out.resolve()) or file_sha(p)!=h:raise ValueError('task/schema drift')
    if f['phase']=='formal':
        b=f['development_binding'];p=Path(b['summary'])
        if file_sha(p)!=b['summary_sha256']:raise ValueError('development result drift')
    return f


def development_gate(s):
    rows=s.get('samples',[]);tasks=sorted({x['task'] for x in rows});counts={a:sum(x['independent'].get('passed') is True for x in rows if x['arm']==a) for a in ('A0','A1','A2')}
    complete=s.get('samples_completed')==18 and len(rows)==18 and len(tasks)==2 and all(sorted(x['repeat'] for x in rows if x['task']==t and x['arm']==a)==[1,2,3] for t in tasks for a in ('A0','A1','A2'))
    baseline=counts['A0']>=5 and all(sum(x['independent'].get('passed') is True for x in rows if x['task']==t and x['arm']=='A0')>=2 for t in tasks)
    contributions=all(any(x['task']==t and x['arm']=='A2' and x.get('recovery_used') and x['independent'].get('passed') is True for x in rows) for t in tasks)
    integrity=not s.get('stopped') and not any(x.get('wrong_version_recovered') or x['status']=='infrastructure_error' for x in rows)
    passed=not s.get('mock',True) and s.get('phase')=='development' and complete and baseline and counts['A2']>=counts['A0'] and contributions and integrity
    return {'passed':bool(passed),'quality_counts':counts,'complete_matrix':complete,'baseline_stable':baseline,
            'current_recovery_contribution_each_task':contributions,'integrity':integrity,'mock_rejected':s.get('mock',True)}


def prepare_formal(out,dev_freeze,dev_summary):
    df=verify(dev_freeze);p=Path(dev_summary).resolve();s=json.loads(p.read_text(encoding='utf-8'));gate=development_gate(s)
    claim=Path(dev_freeze)/'paid.claim'
    if not gate['passed'] or df['phase']!='development' or not claim.exists() or Path(claim.read_text()).resolve()!=p.parent:
        raise ValueError('development admission not passed or paid evidence unbound')
    if not s.get('ledger') or any(r.get('transport_kind')!='live' or r['status']!='returned' or r.get('usage_errors') for r in s['ledger']):raise ValueError('nonlive or invalid development HTTP evidence')
    if s['freeze_sha256']!=file_sha(Path(dev_freeze)/'freeze.json'):raise ValueError('development freeze mismatch')
    expected=[{k:r[k] for k in ('sample_id','task','arm','repeat')} for r in df['plan']]
    actual=[{k:r[k] for k in ('sample_id','task','arm','repeat')} for r in s['samples']]
    if expected!=actual:raise ValueError('development exact matrix mismatch')
    binding={'summary':str(p),'summary_sha256':file_sha(p),'freeze_sha256':s['freeze_sha256'],'gate':gate}
    tasks=[make_task(n,'confirm'+str(n)) for n in PROTOCOL['formal_seeds']]
    controls_out=Path(out).parent/(Path(out).name+'-controls');controls_out.mkdir(exist_ok=False)
    controls=[admission(t,controls_out/t['name']) for t in tasks]
    if not all(c['passed'] for c in controls):raise ValueError('formal offline controls failed; no replacement')
    return prepare(out,'formal',tasks,controls,binding)


def sample_report(w,a,row,ledger,status,final):
    ls=[r for r in ledger.records if r['sample_id'].startswith(row['sample_id']+'/turn')];sent={r['sample_id'] for r in ls if r['status']=='returned'}
    independent=w.evaluate();writes=[i for i,t in enumerate(w.tools) if t['payload'].get('kind')=='written']
    first_write=writes[0] if writes else len(w.tools);final_write=writes[-1] if writes else len(w.tools)
    delivered=[];used=set();wrong=False;ttl_removed=[];current_ids={gid for gid,g in a.groups.items() if any(w.versions.get(s['entity'])==s['source_version'] for s in g['sources'])}
    opportunity=any(b['request_scope'] in sent and b['tool_count_before_model']<=first_write and any(b['group_statuses'].get(gid)=='archived' for gid in current_ids) for b in a.boundaries)
    for b in a.boundaries:
        if b['request_scope'] not in sent:continue
        visible={digest(s['items']) for s in atomic_spans(b['visible']) if s['complete']}
        for gid in b['recovered_groups']:
            g=a.groups[gid]
            if g['hash'] not in visible:continue
            delivered.append({'group':gid,'clock':b['clock'],'entities':[s['entity'] for s in g['sources']]})
            for source in g['sources']:
                e=source['entity'];wrong=wrong or source['source_version']!=w.versions.get(e)
                reread=any(t['name']=='read_document' and t.get('revision')=='current' and t['payload'].get('entity')==e for t in w.tools[b['tool_count_before_model']:final_write])
                if independent['passed'] and b['tool_count_before_model']<=final_write and not reread:used.add(e)
            if any(later['request_scope'] in sent and later['clock']>=b['clock']+2 and later['group_statuses'].get(gid)=='archived' and g['hash'] not in {digest(s['items']) for s in atomic_spans(later['visible']) if s['complete']} for later in a.boundaries):ttl_removed.append(gid)
    costs={}
    for period in ('offpeak','peak'):
        values=[r.get(period+'_usage_priced_usd') for r in ls];costs['cost_'+period+'_usd']=sum(values) if all(v is not None for v in values) else None
    return {**row,'status':status,'final_output':final,'independent':independent,'opportunity_formed':opportunity,
        'recovery_original_delivered':bool(delivered),'recovery_used':bool(used),'recovery_used_entities':sorted(used),
        'delivered_groups':delivered,'wrong_version_recovered':wrong,'ttl_removed_groups':list(set(ttl_removed)),
        'ttl_censored':bool(delivered) and len(set(ttl_removed))<len({x['group'] for x in delivered}),
        'natural_rereads':w.rereads,'model_signals':sum(t['payload'].get('origin')=='model' and t['payload'].get('entity')!='unrelated' for t in w.tools),
        'host_signals':sum(t['payload'].get('origin')=='host' for t in w.tools),'metrics':a.metrics,
        'http_requests':len(ls),'prompt_tokens':sum(r['usage']['prompt_tokens'] for r in ls) if all(r['usage']['prompt_tokens'] is not None for r in ls) else None,
        'completion_tokens':sum(r['usage']['completion_tokens'] for r in ls) if all(r['usage']['completion_tokens'] is not None for r in ls) else None,
        **costs,'all_tool_groups_paired':all(b['paired'] for b in a.boundaries)}


def run_batch(freeze_dir,out,key,mock=False):
    f=verify(freeze_dir);freeze_dir=Path(freeze_dir);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    if not mock and not f['paid_run_allowed']:raise ValueError('paid gate false')
    with (freeze_dir/('mock.claim' if mock else 'paid.claim')).open('x',encoding='utf-8') as c:c.write(str(out.resolve()))
    ledger=StudyLedger(out/'http',max_requests=f['max_requests'],cap_usd=f['cap_usd'],max_body_bytes=24000)
    tasks=json.loads((freeze_dir/'tasks.json').read_text(encoding='utf-8'));samples=[];stopped=None
    for row in f['plan']:
        trial=out/row['sample_id'];w=a=None;status='completed';final=None
        try:
            verify(freeze_dir);trial.mkdir();w=RegistryWorkspace(next(t for t in tasks if t['name']==row['task']),trial/'workspace')
            a=StudyAdapter(w,row['arm'],row['sample_id'],trial);tools=tools_for(w)
            if sha(schema(tools))!=f['tool_schema_sha256']:raise ValueError('tool schema drift')
            model,client=make_sdk_model(key,StudyTransport(ledger,f['tool_schema_sha256'],f['initial_body_sha256'],httpx.MockTransport(ControlledMock(w)) if mock else None))
            with request_scope(row['sample_id'],'initial'):result=sync_run(a,Agent(name='Registry implementation',instructions=INSTRUCTIONS,model=model,model_settings=sdk_settings(),tools=tools),client)
            final=result.final_output
        except MaxTurnsExceeded:status='max_turns'
        except Exception as exc:status='infrastructure_error';stopped={'sample_id':row['sample_id'],'reason':type(exc).__name__}
        finally:
            report={**row,'status':status,'independent':{'passed':None},'recovery_used':False,'wrong_version_recovered':False}
            try:
                if w is not None and a is not None:
                    write(trial/'tools.json',w.tools);write(trial/'lifecycle.json',a.events);write(trial/'checkpoint.json',a.export_state())
                    report=sample_report(w,a,row,ledger,status,final)
            except Exception as exc:report.update(status='infrastructure_error',finalization_error=type(exc).__name__);stopped={'sample_id':row['sample_id'],'reason':'finalize_'+type(exc).__name__}
            samples.append(report)
            try:
                if trial.exists():write(trial/'report.json',report)
            finally:
                summary={'phase':f['phase'],'mock':mock,'samples_planned':f['samples'],'samples_completed':sum(x['status'] in {'completed','max_turns'} for x in samples),
                    'samples':samples,'ledger':ledger.records,'provider_requests':0 if mock else len(ledger.records),'reserved_peak_allmiss_usd':ledger.reserved_usd,
                    'stopped':stopped,'freeze_sha256':file_sha(freeze_dir/'freeze.json')}
                write(out/'summary.json',summary)
        print(f"[{len(samples)}/{f['samples']}] {row['sample_id']} status={report['status']} pass={report['independent']['passed']} used={report['recovery_used']} requests={len(ledger.records)}",flush=True)
        if stopped:break
    return summary


def cp(k,n,alpha,lower):
    if lower and k==0:return 0.
    if not lower and k==n:return 1.
    lo,hi=0.,1.
    for _ in range(70):
        p=(lo+hi)/2
        probability=sum(math.comb(n,j)*p**j*(1-p)**(n-j) for j in (range(k,n+1) if lower else range(0,k+1)))
        if (probability<alpha)==lower:lo=p
        else:hi=p
    return (lo+hi)/2


def analyze(s):
    rows=s['samples'];tasks=sorted({x['task'] for x in rows});arms={a:{'passed':sum(x['independent'].get('passed') is True for x in rows if x['arm']==a),'n':sum(x['arm']==a for x in rows),
        'recovery_used':sum(bool(x.get('recovery_used')) for x in rows if x['arm']==a)} for a in ('A0','A1','A2')}
    result={'phase':s['phase'],'arms':arms,'scope':PROTOCOL['scope'],'quality_noninferiority_confirmed':False,'quality_difference_interval':None,'cost_task_signflip_p':None}
    valid_matrix=len(tasks)==3 and all(sorted(x['repeat'] for x in rows if x['task']==t and x['arm']==a)==[1,2,3,4,5] for t in tasks for a in ('A0','A1','A2'))
    if s['phase']=='formal' and len(rows)==45 and s.get('samples_completed')==45 and valid_matrix:
        wins={a:sum(all(x['independent'].get('passed') is True for x in rows if x['task']==t and x['arm']==a) for t in tasks) for a in ('A0','A2')}
        interval=[cp(wins['A2'],3,.0125,True)-cp(wins['A0'],3,.0125,False),cp(wins['A2'],3,.0125,False)-cp(wins['A0'],3,.0125,True)]
        result.update(quality_difference_interval=interval,quality_noninferiority_confirmed=interval[0]>=-.1)
        diffs=[]
        for t in tasks:
            costs={a:{x['repeat']:x.get('cost_offpeak_usd') for x in rows if x['task']==t and x['arm']==a} for a in ('A0','A2')}
            if all(v is not None and v>0 for a in costs for v in costs[a].values()):
                diffs.append(sum(math.log(costs['A2'][i]/costs['A0'][i]) for i in range(1,6))/5)
        if len(diffs)==3:
            observed=sum(diffs);result['cost_task_signflip_p']=sum(sum(d*sign for d,sign in zip(diffs,signs))<=observed+1e-15 for signs in itertools.product((-1,1),repeat=3))/8
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare-development','prepare-formal','mock','run'));p.add_argument('--out',required=True,type=Path);p.add_argument('--freeze',type=Path);p.add_argument('--development-summary',type=Path)
    args=p.parse_args()
    if args.mode=='prepare-development':f=prepare_development(args.out);print({k:f[k] for k in ('phase','samples','max_requests','cap_usd')});return
    if args.mode=='prepare-formal':f=prepare_formal(args.out,args.freeze,args.development_summary);print({k:f[k] for k in ('phase','samples','max_requests','cap_usd')});return
    key='offline' if args.mode=='mock' else os.environ.pop('DEEPSEEK_API_KEY','');os.environ.pop('OPENAI_API_KEY',None)
    if not key or args.freeze is None:raise ValueError('key/freeze required')
    s=run_batch(args.freeze,args.out,key,mock=args.mode=='mock');write(args.out/'analysis.json',analyze(s))
    if s['phase']=='development':write(args.out/'development_gate.json',development_gate(s))
    print({k:s[k] for k in ('phase','samples_completed','provider_requests','reserved_peak_allmiss_usd','stopped')})


if __name__=='__main__':main()
