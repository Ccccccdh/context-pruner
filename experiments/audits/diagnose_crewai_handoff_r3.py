"""Post-run descriptive diagnosis; never substitutes the frozen quality gate."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from experiments.audits.audit_crewai_handoff import HANDOFF_FACT, ROOT


def diagnose(batch: Path) -> dict:
    tasks = {row['task_id']: row for row in json.loads(
        (ROOT / 'tasks/stage5_autogen/natural_tasks.json').read_text(encoding='utf-8'))}
    rows = [json.loads(line) for line in (batch / 'results.jsonl').read_text(encoding='utf-8').splitlines() if line]
    by_key = {(row['task_id'], row['repeat'], row['method']): row for row in rows}
    quality = {}
    for task in tasks:
        subset = [row for row in rows if row['task_id'] == task]
        if not subset:
            continue
        quality[task] = {}
        for method in ('none', 'native_summary', 'pruner_v1'):
            arm = [row for row in subset if row['method'] == method]
            exact = sum(bool(row['success']) for row in arm)
            marker = sum(bool(row['role_outputs']) and row['role_outputs'][0].startswith('HANDOFF ') for row in arm)
            handoff_facts = sum(bool(row['role_outputs']) and all(
                fact.lower() in row['role_outputs'][0].lower() for fact in HANDOFF_FACT[task])
                for row in arm)
            tool_pair = sum(len(row['role_tool_traces']) == 2 and all(
                Counter(row['role_tool_traces'][i]) == Counter([tasks[task]['tools'][i]])
                for i in (0, 1)) for row in arm)
            quality[task][method] = {'n': len(arm), 'exact_success': exact,
                                     'strict_handoff_marker': marker,
                                     'handoff_facts': handoff_facts,
                                     'tool_pair_correct': tool_pair}
    savings = {}
    for method in ('native_summary', 'pruner_v1'):
        values = []
        for task, repeat, _ in by_key:
            baseline = by_key.get((task, repeat, 'none'))
            candidate = by_key.get((task, repeat, method))
            if baseline and candidate and baseline['all_arm_total_tokens']:
                value = (baseline['all_arm_total_tokens'] - candidate['all_arm_total_tokens']) / baseline['all_arm_total_tokens']
                if (task, repeat) not in {(item['task'], item['repeat']) for item in values}:
                    values.append({'task': task, 'repeat': repeat, 'savings': value})
        savings[method] = {'n': len(values), 'mean': sum(v['savings'] for v in values) / len(values),
                           'positive': sum(v['savings'] > 0 for v in values), 'pairs': values}
    return {'batch': batch.name, 'quality': quality, 'paired_total_token_savings': savings,
            'interpretation': 'Post-run mechanism diagnosis only; frozen exact_success remains primary quality gate.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    output = diagnose(args.batch)
    destination = args.batch / 'diagnostic.json'
    destination.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(output, indent=2, ensure_ascii=False))
