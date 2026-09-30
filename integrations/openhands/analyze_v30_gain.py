"""Break down the saved v30 input result without making provider requests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def sample(root: Path, task: str, repeat: int, arm: str) -> dict:
    folder = root / f'r{repeat}-{task}-{arm}'
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    usage = report['agent_metrics']['token_usages']
    inputs = [entry['prompt_tokens'] for entry in usage]
    assert sum(inputs) == report['agent_metrics']['accumulated_token_usage']['prompt_tokens']
    state_path = folder / 'pruner-state.json'
    compressions = json.loads(state_path.read_text(encoding='utf-8'))['audit'] if state_path.exists() else []
    return {'calls': len(inputs), 'input': sum(inputs), 'mean_per_call': sum(inputs) / len(inputs),
            'normal_completion': report['success'], 'compressions': len(compressions),
            'compression_views': [{'before': row['before'], 'after': row['after']} for row in compressions]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root, out = Path(args.source).resolve(), Path(args.out).resolve()
    assert root.is_dir() and not out.exists()
    rows = []
    for task in ('click_catalog', 'packaging_wheel_ranking'):
        for repeat in (1, 2, 3):
            baseline = sample(root, task, repeat, 'none')
            plugin = sample(root, task, repeat, 'pruner_v11')
            nb, np = baseline['calls'], plugin['calls']
            mb, mp = baseline['mean_per_call'], plugin['mean_per_call']
            count_part = (nb - np) * (mb + mp) / 2
            size_part = (mb - mp) * (nb + np) / 2
            assert abs(count_part + size_part - (baseline['input'] - plugin['input'])) < 1e-6
            rows.append({'task': task, 'repeat': repeat, 'baseline': baseline, 'plugin': plugin,
                         'input_savings': 1 - plugin['input'] / baseline['input'],
                         'call_count_component_tokens': count_part,
                         'mean_context_component_tokens': size_part})
    out.mkdir(parents=True)
    result = {'source': str(root), 'api_requests': 0, 'rows': rows,
              'unweighted_mean_pair_savings': sum(r['input_savings'] for r in rows) / len(rows),
              'weighted_total_savings': 1 - sum(r['plugin']['input'] for r in rows) / sum(r['baseline']['input'] for r in rows),
              'decomposition_note': 'Symmetric N times mean decomposition is arithmetic, not causal; trajectories and quality differ.'}
    (out / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pairs': len(rows), 'unweighted_mean_pair_savings': result['unweighted_mean_pair_savings'],
                      'weighted_total_savings': result['weighted_total_savings'], 'api_requests': 0}))


if __name__ == '__main__':
    main()
