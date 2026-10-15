"""Descriptive reconciliation of the V7 development cohort; no provider calls."""
import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def analyze(run):
    run = Path(run)
    summary = load(run/'paid/summary.json')
    audit = load(run/'paid/audit.json')
    rows = []
    for sample in summary['samples']:
        trial = run/'paid'/sample['sample_id']
        reads = load(trial/'host_reads.json')
        counters = load(trial/'host_read_metrics.json')
        tools = load(trial/'tools.json')
        boundaries = load(trial/'model_boundaries.json')
        expected_checks = (len(boundaries) + sum(t['name'] in ('read_document','check_artifact') for t in tools)
                           + sum(1 if t['payload'].get('already_completed') else 2 for t in tools if t['name']=='close_research') + 1)
        if sample['status'] == 'completed':
            assert counters['source_validation_calls'] == expected_checks, 'source validation counter reconciliation failed'
        records = [r for r in summary['ledger'] if r['sample_id'].split('/turn')[0] == sample['sample_id']]
        known = all(r['status'] == 'returned' and not r.get('usage_errors') and all(v is not None for v in r['usage'].values()) for r in records)
        usage = {k: sum(r['usage'][k] for r in records) if known else None for k in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')}
        rows.append(dict(sample_id=sample['sample_id'], task=sample['task'], arm=sample['arm'],
                         status=sample['status'], quality_passed=sample['independent']['passed'] if sample['status'] != 'infrastructure_error' else None,
                         requests=len(records), usage=usage,
                         cost_low=sample.get('cost_offpeak_usd'), cost_high=sample.get('cost_peak_usd'),
                         postresearch_reads=sample.get('natural_rereads'),
                         archive_hits_prepared=sum(e['origin']=='archive' for e in reads),
                         model_missing_signals=sample.get('model_signals'), lifecycle_recovery_used=sample['recovery_used'],
                         expected_source_validation_calls=expected_checks,
                         source_validation_counter_reconciled=counters['source_validation_calls']==expected_checks,
                         **counters))
    arms = {}
    for arm in ('A0', 'A1', 'A2'):
        subset = [r for r in rows if r['arm'] == arm]
        arms[arm] = dict(planned=4, observed=len(subset), passed=sum(r['quality_passed'] is True for r in subset),
                         **{k:sum(r[k] for r in subset) for k in ('requests','postresearch_reads','archive_hits_prepared','source_payload_constructs','source_validation_calls','model_missing_signals','lifecycle_recovery_used')},
                         **{k:sum(r[k] for r in subset) if len(subset)==4 and all(r[k] is not None for r in subset) else None for k in ('cost_low','cost_high')},
                         usage={k:sum(r['usage'][k] for r in subset) if len(subset)==4 and all(r['usage'][k] is not None for r in subset) else None for k in ('prompt_tokens','completion_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')})
    known = [r for r in summary['ledger'] if r['status']=='returned' and not r.get('usage_errors') and r.get('offpeak_usage_priced_usd') is not None]
    pairs = []
    for task in load(run/'frozen/freeze.json')['tasks']:
        group = {r['arm']:r for r in rows if r['task']==task}
        if len(group)==3 and all(r['status']=='completed' and r['cost_low'] is not None for r in group.values()):
            pairs.append(dict(task=task, A2_vs_A0_cost_change=group['A2']['cost_low']/group['A0']['cost_low']-1,
                              A2_vs_A1_cost_change=group['A2']['cost_low']/group['A1']['cost_low']-1))
    result = dict(scope='four-instance development; descriptive, not formal confirmation', audit=audit, arms=arms, rows=rows,
                  known_cost_low=sum(r['offpeak_usage_priced_usd'] for r in known),
                  known_cost_high=sum(r['peak_usage_priced_usd'] for r in known), complete_task_pairs=pairs,
                  model_cost_saving_attributable_to_host_cache=False,
                  lifecycle_recovery_used_observed=any(r['lifecycle_recovery_used'] for r in rows),
                  formal_experiment_admitted=False, analysis_provider_calls=0)
    (run/'final-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path)
    analyze(parser.parse_args().run)
