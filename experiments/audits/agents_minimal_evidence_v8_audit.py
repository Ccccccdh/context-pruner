"""V8 wire, record projection, archive proof, usage and quality reconciliation."""
import hashlib
import json
import math

from agents.models.chatcmpl_converter import Converter
from agents.models.openai_chatcompletions import _to_dump_compatible
from context_pruner.evidence_reader_v7 import resolve_current
from context_pruner.evidence_projection_v8 import project_records
from experiments.runners.agents_provider_audit_v2 import sha,PRICES,MODEL
from experiments.runners.agents_registry_tasks_v4 import dump,bytes_sha


def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def audit(freeze,out,f,operation_claim,tasks,*,mock):
    summary=load(out/'summary.json')
    if summary['mock'] is not mock or summary['freeze_sha256']!=hashlib.sha256((freeze/'freeze.json').read_bytes()).hexdigest():
        raise ValueError('output identity mismatch')
    if (freeze/('mock.claim' if mock else 'paid.claim')).read_text()!=str(out.resolve()):raise ValueError('output claim mismatch')
    if not mock:
        expected=dict(freeze=str(freeze.resolve()),out=str(out.resolve()),freeze_sha256=summary['freeze_sha256'],max_requests=192,cap_usd=2.)
        if load(operation_claim)!=expected:raise ValueError('operation claim mismatch')
    events=[json.loads(line) for line in (out/'http/requests.jsonl').read_text().splitlines()]
    sent=[e for e in events if e['event']=='dispatch'];done=[e for e in events if e['event']=='complete']
    if len(sent)!=len(done) or [{k:v for k,v in e.items() if k!='event'} for e in done]!=summary['ledger']:raise ValueError('durable journal mismatch')
    if len(done)>192 or summary['reserved_peak_allmiss_usd']>2. or not math.isclose(sum(r['reserved_peak_allmiss_usd'] for r in done),summary['reserved_peak_allmiss_usd'],abs_tol=1e-12):
        raise ValueError('budget mismatch')
    unknown=[];bodies={}
    for index,(dispatch,record) in enumerate(zip(sent,done),1):
        if dispatch['index']!=index or record['index']!=index or any(dispatch[k]!=record[k] for k in dispatch if k not in {'event','status','usage'}):raise ValueError('dispatch identity mismatch')
        body=load(out/'http'/record['request_file']);bodies[record['sample_id']]=body
        wire=json.dumps(body,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode()
        reserve=(len(wire)+4096)*PRICES['peak']['miss']/1e6+2048*PRICES['peak']['output']/1e6
        if len(wire)!=record['body_bytes'] or len(wire)>24000 or hashlib.sha256(wire).hexdigest()!=record['wire_body_sha256'] or sha(body)!=record['body_sha256'] or not math.isclose(reserve,record['reserved_peak_allmiss_usd'],abs_tol=1e-12):raise ValueError('wire/reservation mismatch')
        if (sha(body['tools'])!=f['tool_schema_sha256'] or body['model']!=MODEL or body['max_tokens']!=2048
                or body['temperature']!=0 or body['thinking']!={'type':'disabled'}):raise ValueError('body settings/schema mismatch')
        if record['transport_kind']!=('mock' if mock else 'live'):raise ValueError('transport kind mismatch')
        u=record['usage']
        if record['status']!='returned' or record.get('usage_errors') or any(v is None for v in u.values()):unknown.append(record['sample_id']);continue
        response=load(out/'http'/record['response_file'])
        if any(response['usage'].get(k)!=v for k,v in u.items()) or u['prompt_tokens']!=u['prompt_cache_hit_tokens']+u['prompt_cache_miss_tokens'] or u['total_tokens']!=u['prompt_tokens']+u['completion_tokens']:raise ValueError('raw usage mismatch')
        for period,p in PRICES.items():
            cost=(u['prompt_cache_hit_tokens']*p['hit']+u['prompt_cache_miss_tokens']*p['miss']+u['completion_tokens']*p['output'])/1e6
            if not math.isclose(cost,record[period+'_usage_priced_usd'],abs_tol=1e-12):raise ValueError('price mismatch')
    prepared=delivered=manifest_boundaries=0;unsent=[];all_rows=summary['samples'];pairs=0
    if len(all_rows)>12:raise ValueError('extra samples')
    for position,row in enumerate(all_rows):
        expected=f['plan'][position]
        if any(row[k]!=v for k,v in expected.items()):raise ValueError('matrix mismatch')
        trial=out/row['sample_id'];task=next(t for t in tasks if t['name']==row['task'])
        if load(trial/'report.json')!=row:raise ValueError('report mismatch')
        tools=load(trial/'tools.json');reads=load(trial/'host_reads.json');metrics=load(trial/'host_read_metrics.json')
        boundaries=load(trial/'model_boundaries.json') if (trial/'model_boundaries.json').exists() else []
        records=[r for r in done if r['sample_id'].split('/turn')[0]==row['sample_id']]
        byscope={r['sample_id']:r for r in records}
        if len(records)>16 or len(records)!=row['http_requests'] or len(byscope)!=len(records):raise ValueError('sample count mismatch')
        if not set(byscope).issubset({b['request_scope'] for b in boundaries}):raise ValueError('unbound dispatched request')
        versions={f'docs/{e}.json':bytes_sha(dump(doc).encode()) for e,doc in task['documents'].items()}
        for key,suffix in (('documents',''),('old_documents','.old')):
            for entity,doc in task[key].items():
                if bytes_sha((trial/f'workspace/docs/{entity}{suffix}.json').read_bytes())!=bytes_sha(dump(doc).encode()):raise ValueError('source changed')
        valid_boundaries=[]
        for b in boundaries:
            r=byscope.get(b['request_scope'])
            if r is None:unsent.append(b['request_scope']);continue
            if not b['paired'] or r['sdk_input_sha256']!=sha(b['visible']):raise ValueError('final SDK input mismatch')
            messages=Converter.items_to_messages(b['visible'],model=MODEL,base_url='https://api.deepseek.com',strict_feature_validation=True)
            messages.insert(0,{'role':'system','content':b['instructions']})
            if bodies[r['sample_id']]['messages']!=_to_dump_compatible(messages):raise ValueError('converted messages mismatch')
            prior=tools[:b['tool_count_before_model']]
            if any(t['payload'].get('kind')=='stage_complete' for t in prior):
                projection=b['receipt_projection'];observed={t['payload']['entity'] for t in prior if t['name']=='read_document' and t.get('revision')=='current'}
                expected_manifest=dict(kind='host_source_receipts',schema='agents_receipts_v6',sources=[dict(entity=e,source_path=f'docs/{e}.json',source_version=versions[f'docs/{e}.json']) for e in sorted(observed)])
                if not projection or projection['manifest']!=expected_manifest or projection['bytes']>2048:raise ValueError('receipt manifest mismatch')
                count=sum(i.get('role')=='user' and i.get('content')==json.dumps(expected_manifest,ensure_ascii=False,sort_keys=True,separators=(',', ':')) for i in b['visible'])
                if count!=1:raise ValueError('manifest duplication/omission')
                if r['sample_id'] not in unknown:manifest_boundaries+=1
            if r['sample_id'] not in unknown:valid_boundaries.append(b)
        read_tools=[i for i,t in enumerate(tools) if t['name'] in {'read_document','read_records'}]
        if [e['tool_index'] for e in reads]!=read_tools or len({e['call_id'] for e in reads})!=len(reads) or any(not e['call_id'] for e in reads):raise ValueError('read invocation identity mismatch')
        source_constructs=0
        for event in reads:
            payload=tools[event['tool_index']]['payload']
            if event['payload_sha256']!=sha(payload):raise ValueError('read payload hash mismatch')
            if event.get('tool_name')=='read_records' and payload.get('kind')=='registry_record_evidence':
                text=dump(task['documents'][event['entity']])
                whole=dict(kind='evidence',entity=event['entity'],stage='research',source_path=f"docs/{event['entity']}.json",source_version=versions[f"docs/{event['entity']}.json"],text=text)
                expected_payload=project_records(whole,event['entity'],event['codes'],event['revision'],event['after_research'],full=row['arm']=='B0')
                if payload!=expected_payload:raise ValueError('record projection mismatch')
            if event['origin']=='source':
                source_constructs+=payload.get('kind') in {'evidence','registry_record_evidence'}
                if event['proof'] is not None:raise ValueError('source mislabelled proof')
                continue
            if event['origin']!='archive' or row['arm']!='B2' or not event['after_research'] or event['revision']!='current':raise ValueError('invalid archive origin')
            def unavailable(entity):raise ValueError('host archive proof rejected')
            proof=resolve_current(event['entity'],namespace=row['sample_id'],current_versions=versions,archive_snapshot=event['proof'],source_loader=unavailable)
            projected=project_records(proof.payload,event['entity'],event['codes'],event['revision'],event['after_research'])
            if proof.origin!='archive' or projected!=payload or proof.archive_group_id!=event['archive_group_id'] or event.get('tool_name')!='read_records':raise ValueError('host archive proof mismatch')
            prepared+=1
            if any(any(i.get('type')=='function_call_output' and i.get('call_id')==event['call_id'] and json.loads(i['output'])==payload for i in b['visible']) for b in valid_boundaries):delivered+=1
        if source_constructs!=metrics['source_payload_constructs']:raise ValueError('source construction counter mismatch')
        expected_checks=(len(boundaries)+sum(t['name'] in {'read_document','read_records','check_artifact'} for t in tools)
                         +sum(1 if t['payload'].get('already_completed') else 2 for t in tools if t['name']=='close_research')+1)
        if row['status']=='completed' and metrics['source_validation_calls']!=expected_checks:raise ValueError('source validation counter mismatch')
        for field in ('prompt_tokens','completion_tokens'):
            values=[r['usage'][field] for r in records];total=sum(values) if all(v is not None for v in values) else None
            if row[field]!=total:raise ValueError('sample token mismatch')
        for period in ('offpeak','peak'):
            values=[r.get(period+'_usage_priced_usd') for r in records];total=sum(values) if all(v is not None for v in values) else None
            if (total is None)!=(row['cost_'+period+'_usd'] is None) or (total is not None and not math.isclose(total,row['cost_'+period+'_usd'],abs_tol=1e-12)):raise ValueError('sample price mismatch')
        artifact=load(trial/'workspace/artifact.json') if (trial/'workspace/artifact.json').exists() else None
        expected_rows=[]
        for q in task['queries']:
            rec=next(rec for rec in task['documents'][q['entity']]['records'] if rec['code']==q['code'])
            expected_rows.append(dict(id=q['id'],value=q['reading']*rec['multiplier']+rec['bias'],label=rec['label'],source_version=versions[f"docs/{q['entity']}.json"],evidence_code=q['code']))
        if (artifact=={'rows':expected_rows})!=row['independent']['passed']:raise ValueError('independent artifact mismatch')
    if sum(r['http_requests'] for r in all_rows)!=len(done):raise ValueError('unattributed HTTP')
    complete=len(all_rows)==12 and summary['samples_completed']==12 and summary['stopped'] is None and not unknown and not unsent
    if summary['provider_requests']!=(0 if mock else len(done)):raise ValueError('provider count mismatch')
    response_reduction=wire_reduction=None
    if mock and complete:
        sums={a:dict(text=0,wire=0) for a in ('B0','B1','B2')}
        for task in tasks:
            perarm={arm:[bodies[r['sample_id']] for r in done if r['sample_id'].startswith(task['name']+'-'+arm+'-')] for arm in sums}
            if perarm['B1']!=perarm['B2']:raise ValueError('controlled B1/B2 final-input equivalence failed')
            if not (len(perarm['B0'])==len(perarm['B1'])==9):raise ValueError('controlled sequence count mismatch')
            if not (perarm['B0'][0]==perarm['B1'][0]):raise ValueError('initial input parity failed')
            pairs+=len(perarm['B1'])
        for row in all_rows:
            trial=out/row['sample_id'];tools=load(trial/'tools.json')
            sums[row['arm']]['text']+=sum(len(t['payload']['text'].encode()) for t in tools if t['name']=='read_records' and t['payload'].get('kind')=='registry_record_evidence')
            sums[row['arm']]['wire']+=sum(r['body_bytes'] for r in done if r['sample_id'].split('/turn')[0]==row['sample_id'])
        response_reduction=1-sums['B1']['text']/sums['B0']['text']
        wire_reduction=1-sums['B1']['wire']/sums['B0']['wire']
    offline_gate=bool(mock and complete and sum(r['independent']['passed'] is True for r in all_rows)==12 and prepared==delivered==8
                      and pairs==36 and response_reduction>=.5 and wire_reduction>=.15)
    return dict(complete=complete,requests=len(done),passed=sum(r['status']!='infrastructure_error' and r['independent']['passed'] is True for r in all_rows),
                planned_samples=12,observed_samples=len(all_rows),completed_samples=summary['samples_completed'],unattempted_samples=12-len(all_rows),
                infrastructure_failed_samples=sum(r['status']=='infrastructure_error' for r in all_rows),unknown_usage_requests=len(unknown),
                unknown_usage=unknown,unsent_boundaries=unsent,manifest_boundaries=manifest_boundaries,
                host_archive_hits_prepared=prepared,host_archive_hits_delivered=delivered,mock_B1_B2_equal_body_pairs=pairs,
                response_text_reduction=response_reduction,aggregate_wire_reduction=wire_reduction,offline_gate_passed=offline_gate,
                reserved_usd=summary['reserved_peak_allmiss_usd'],provider_requests=summary['provider_requests'])
