"""Descriptive, zero-provider analysis of the independent V6 follow-up cohort."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def analyze(run):
    run=Path(run);summary=load(run/'paid/summary.json');audit=load(run/'paid/audit.json');frozen=load(run/'frozen/freeze.json')
    assert frozen['tasks']==['receiptdev227','receiptdev229'] and frozen['samples']==6
    assert len(summary['samples'])<=6 and audit['requests']==len(summary['ledger'])
    rows=[]
    for sample in summary['samples']:
        trial=run/'paid'/sample['sample_id'];tools=load(trial/'tools.json')
        records=[r for r in summary['ledger'] if r['sample_id'].split('/turn')[0]==sample['sample_id']]
        unknown=any(r['status']!='returned' or any(v is None for v in r['usage'].values()) for r in records)
        counts={field:None if unknown else sum(r['usage'][field] for r in records) for field in ('prompt_tokens','completion_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')}
        costs={}
        for period,prices in frozen['prices'].items():
            cost=None if unknown else (counts['prompt_cache_hit_tokens']*prices['hit']+counts['prompt_cache_miss_tokens']*prices['miss']+counts['completion_tokens']*prices['output'])/1e6
            declared=sample['cost_'+period+'_usd']
            assert (cost is None)==(declared is None)
            if cost is not None:assert math.isclose(cost,declared,abs_tol=1e-12)
            costs[period]=cost
        known_quality=sample['status']!='infrastructure_error'
        summary_text=next((t['payload']['summary'] for t in tools if t['payload'].get('kind')=='stage_complete'),None)
        order=next((t['payload'] for t in tools if t['name']=='work_order'),None)
        labels=0 if order is not None and summary_text is not None else None
        if labels is not None:
            for query in order['queries']:
                doc=load(trial/f"workspace/docs/{query['entity']}.json")
                record=next(r for r in doc['records'] if r['code']==query['code'])
                labels+=record['label'] in summary_text
        checkpoint=load(trial/'checkpoint.json');archived=[]
        for group in checkpoint['groups'].values():
            if group['status']=='archived':
                for item in group['items']:
                    if item.get('type')=='function_call_output':
                        try:payload=json.loads(item['output'])
                        except (ValueError,TypeError):continue
                        if isinstance(payload,dict) and payload.get('kind')=='evidence':archived.append(payload)
        rereads=[t for t in tools if t['name']=='read_document' and t.get('reread') and t.get('revision')=='current']
        rows.append(dict(sample_id=sample['sample_id'],task=sample['task'],arm=sample['arm'],status=sample['status'],
                         quality_passed=sample['independent']['passed'] if known_quality else None,requests=len(records),
                         usage=counts,cost=costs,rereads=sample['natural_rereads'],model_missing_signals=sample['model_signals'],
                         lifecycle_recoveries_used=sample['recovery_used'],summary_selected_label_coverage=labels,
                         reread_payloads_equal_existing_archived_payload=sum(t['payload'] in archived for t in rereads)))
    arms={}
    for arm in ('A0','A1','A2'):
        subset=[r for r in rows if r['arm']==arm]
        arms[arm]=dict(planned=2,observed=len(subset),completed=sum(r['status'] in {'completed','max_turns'} for r in subset),
                       passed=sum(r['quality_passed'] is True for r in subset),
                       infrastructure_failed=sum(r['status']=='infrastructure_error' for r in subset),
                       requests=sum(r['requests'] for r in subset),rereads=sum(r['rereads'] for r in subset),
                       recoveries_used=sum(r['lifecycle_recoveries_used'] for r in subset),
                       cost_low=sum(r['cost']['offpeak'] for r in subset) if subset and all(r['cost']['offpeak'] is not None for r in subset) else None,
                       cost_high=sum(r['cost']['peak'] for r in subset) if subset and all(r['cost']['peak'] is not None for r in subset) else None)
    pairs=[]
    for task in frozen['tasks']:
        task_rows={r['arm']:r for r in rows if r['task']==task}
        if len(task_rows)==3 and all(r['status']=='completed' and r['cost']['offpeak'] is not None for r in task_rows.values()):
            baseline=task_rows['A0']
            pairs.append(dict(task=task,
                              A1_cost_change=task_rows['A1']['cost']['offpeak']/baseline['cost']['offpeak']-1,
                              A2_cost_change=task_rows['A2']['cost']['offpeak']/baseline['cost']['offpeak']-1,
                              A2_input_change=task_rows['A2']['usage']['prompt_tokens']/baseline['usage']['prompt_tokens']-1))
    valid=[r for r in summary['ledger'] if r['status']=='returned' and r.get('offpeak_usage_priced_usd') is not None]
    result=dict(scope='independent two-instance development; descriptive, not formal confirmation',
                complete=audit['complete'],planned_samples=6,completed_samples=summary['samples_completed'],
                real_http_requests=len(summary['ledger']),valid_usage_requests=len(valid),unknown_usage_requests=len(summary['ledger'])-len(valid),
                reserved_usd=summary['reserved_peak_allmiss_usd'],known_cost_low=sum(r['offpeak_usage_priced_usd'] for r in valid),
                known_cost_high=sum(r['peak_usage_priced_usd'] for r in valid),arms=arms,rows=rows,complete_task_pairs=pairs,
                source_manifest_boundaries=audit['manifest_boundaries'],
                assessment=dict(retain_as_provenance_capability=audit['manifest_boundaries']>0,
                                A2_lower_cost_in_both_instances=all(p['A2_cost_change']<0 for p in pairs) if len(pairs)==2 else None,
                                rereads_persist=any(r['rereads']>0 for r in rows if r['arm'] in {'A1','A2'}),
                                stable_cost_savings_confirmed=False,quality_noninferiority_confirmed=False,
                                formal_experiment_admitted=False),
                prior_cohort=dict(http_requests=35,completed=5,infrastructure_failed=1,unknown_usage_requests=1,
                                  complete=False,pooled_as_new_cohort=False),provider_calls_made_by_analysis=0)
    (run/'final-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True,type=Path)
    analyze(parser.parse_args().run)
