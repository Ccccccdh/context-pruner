"""Descriptive V8 development screen; no provider requests or formal inference."""
import argparse
import json
import math
from experiments.audits.agents_v8_confirmation_statistics import confirm
from pathlib import Path


def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def analyze(run):
    run=Path(run);summary=load(run/'paid/summary.json');audit=load(run/'paid/audit.json');rows=[]
    for sample in summary['samples']:
        trial=run/'paid'/sample['sample_id'];tools=load(trial/'tools.json');reads=load(trial/'host_reads.json')
        metrics=load(trial/'host_read_metrics.json');records=[r for r in summary['ledger'] if r['sample_id'].split('/turn')[0]==sample['sample_id']]
        known=all(r['status']=='returned' and not r.get('usage_errors') and all(v is not None for v in r['usage'].values()) for r in records)
        usage={k:sum(r['usage'][k] for r in records) if known else None for k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')}
        good_scopes={r['sample_id'] for r in records if r['status']=='returned' and not r.get('usage_errors') and all(v is not None for v in r['usage'].values())}
        boundaries=load(trial/'model_boundaries.json') if (trial/'model_boundaries.json').exists() else []
        hits=[e for e in reads if e['origin']=='archive']
        delivered=sum(any(b['request_scope'] in good_scopes and any(i.get('type')=='function_call_output' and i.get('call_id')==e['call_id'] and json.loads(i['output'])==tools[e['tool_index']]['payload'] for i in b['visible']) for b in boundaries) for e in hits)
        row=dict(sample_id=sample['sample_id'],task=sample['task'],arm=sample['arm'],status=sample['status'],
                 quality_passed=sample['independent']['passed'] if sample['status']!='infrastructure_error' else None,
                 requests=len(records),usage=usage,total_tokens=usage['total_tokens'],cost_low=sample.get('cost_offpeak_usd'),cost_high=sample.get('cost_peak_usd'),
                 postresearch_reads=sample.get('natural_rereads',0),record_requests=sum(t['name']=='read_records' for t in tools),
                 record_successes=sum(t['name']=='read_records' and t['payload'].get('kind')=='registry_record_evidence' for t in tools),
                 full_document_requests=sum(t['name']=='read_document' for t in tools),archive_prepared=len(hits),archive_delivered=delivered,
                 model_missing_signals=sample.get('model_signals',0),lifecycle_recovery_used=sample['recovery_used'],**metrics)
        rows.append(row)
    arms={}
    for arm in ('B0','B1','B2'):
        subset=[r for r in rows if r['arm']==arm]
        arms[arm]=dict(planned=48,observed=len(subset),quality_passes=sum(r['quality_passed'] is True for r in subset),
                       **{k:sum(r[k] for r in subset) for k in ('requests','postresearch_reads','record_requests','record_successes','full_document_requests','archive_prepared','archive_delivered','source_payload_constructs','source_validation_calls','model_missing_signals','lifecycle_recovery_used')},
                       **{k:sum(r[k] for r in subset) if len(subset)==48 and all(r[k] is not None for r in subset) else None for k in ('total_tokens','cost_low','cost_high')},
                       usage={k:sum(r['usage'][k] for r in subset) if len(subset)==48 and all(r['usage'][k] is not None for r in subset) else None for k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')})
    known=[r for r in summary['ledger'] if r['status']=='returned' and not r.get('usage_errors') and r.get('offpeak_usage_priced_usd') is not None]
    result=dict(scope='48 unseen registry instances; preregistered confirmation within synthetic family',audit=audit,arms=arms,rows=rows,
                confirmation=confirm(rows,audit['complete']),known_cost_low=sum(r['offpeak_usage_priced_usd'] for r in known),
                known_cost_high=sum(r['peak_usage_priced_usd'] for r in known),reserved_usd=summary['reserved_peak_allmiss_usd'],
                analysis_provider_calls=0,host_cache_token_savings_attributable=False)
    (run/'final-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path);analyze(p.parse_args().run)
