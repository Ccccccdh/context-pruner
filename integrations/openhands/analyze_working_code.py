"""Separate compressed and identity-run pairs; no tools or model calls."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    audit = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
    assert audit['complete'], 'Full audit required; no partial efficacy estimate'
    method = next(k for k in audit['paired'] if k.startswith('pruner'))
    pairs = audit['paired'][method]['pairs']
    bykey = {(r['task'], r['repeat'], r['arm']): r for r in audit['samples']}
    rows = []
    for pair in pairs:
        sample = out / f"r{pair['repeat']}-{pair['task']}-{method}"
        state = json.loads((sample / 'pruner-state.json').read_text(encoding='utf-8'))
        accepted = [r for r in state['audit'] if 'after' in r and 'skipped' not in r]
        base = bykey[pair['task'], pair['repeat'], 'none']
        other = bykey[pair['task'], pair['repeat'], method]
        rows.append({**pair,
                     'baseline_normal_completion': base['normal_completion_success'],
                     'method_normal_completion': other['normal_completion_success'],
                     'accepted_compressions': len(accepted),
                     'protected_tokens': [r['protected_tokens'] for r in accepted],
                     'selected_units': [r['selected_units'] for r in accepted],
                     'omitted_units': [r['omitted_units'] for r in accepted],
                     'available_code_tokens': [r.get('available_code_tokens') for r in accepted]})
    subsets = {}
    def both_normal(row):
        return row['api_ok'] and row['baseline_normal_completion'] and row['method_normal_completion']
    for label, predicate in [('compressed', lambda r: r['accepted_compressions'] > 0),
                             ('not_compressed', lambda r: r['accepted_compressions'] == 0),
                             ('both_normal_api_ok', both_normal),
                             ('compressed_both_normal_api_ok', lambda r: both_normal(r) and r['accepted_compressions'] > 0)]:
        values = [r['input_savings'] for r in rows if predicate(r) and r['input_savings'] is not None]
        subsets[label] = {'n': len(values), 'mean': statistics.mean(values) if values else None,
                          'median': statistics.median(values) if values else None,
                          'wins': sum(v > 0 for v in values)}
    result = {'method': method, 'pairs': rows, 'subsets': subsets, 'api_requests': 0,
              'limitation': 'Post-run trigger subsets are descriptive, not randomized causal estimates. Identity runs cannot demonstrate a compression benefit; different trajectories can occur even at temperature zero.'}
    (out / 'working-code-diagnostics.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(subsets, indent=2))


if __name__ == '__main__':
    main()
