"""Estimate view-token effect of v30 pruning along the exact saved action trajectory.

No model calls are made. The fixed future actions come from the pruner run, so this
is a mechanical prompt comparison, not a counterfactual task success estimate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from replay_v33_checkpoints import ContextPrunerCondenserV11, EVENT_TYPES
from context_pruner.adapters.openhands_v36 import ContextPrunerCondenserV36

from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM

def inspect(sample: Path, llm: LLM, trigger: int, target: int, hard: int, candidate: str):
    report = json.loads((sample / 'report.json').read_text(encoding='utf-8'))
    expected_calls = len(report['agent_metrics']['token_usages'])
    condenser = (ContextPrunerCondenserV36() if candidate == 'v36' else
                 ContextPrunerCondenserV11(trigger_tokens=trigger, target_tokens=target, hard_tokens=hard))
    raw_view, compressed_view, rows = [], [], []
    previous = None
    for path in sorted(sample.glob('conversation/**/events/event-*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))
        kind = raw['kind']
        cls = EVENT_TYPES.get(kind)
        if cls is None:
            continue
        event = cls.model_validate(raw)
        if kind == 'ActionEvent' and previous != 'ActionEvent':
            original = list(compressed_view)
            candidate = condenser.condense(View(events=original), llm)
            if isinstance(candidate, Condensation):
                compressed_view = list(candidate.apply(original))
                View(events=compressed_view).enforce_properties(original)
            raw_tokens = get_total_token_count(raw_view, llm)
            pruned_tokens = get_total_token_count(compressed_view, llm)
            rows.append({'call': len(rows) + 1, 'raw_view_tokens': raw_tokens,
                         'pruned_view_tokens': pruned_tokens, 'saved_view_tokens': raw_tokens - pruned_tokens})
        raw_view.append(event)
        compressed_view.append(event)
        previous = kind
    assert len(rows) == expected_calls, (sample, len(rows), expected_calls)
    audit = condenser.export_state()['audit']
    return {'sample': sample.name, 'task': report['task'], 'calls': len(rows),
            'accepted_compressions': sum('forgotten_ids' in a for a in audit),
            'safe_skips': [a['skipped'] for a in audit if 'skipped' in a],
            'omitted_units_total': sum(a.get('omitted_units', 0) for a in audit),
            'raw_view_total': sum(r['raw_view_tokens'] for r in rows),
            'pruned_view_total': sum(r['pruned_view_tokens'] for r in rows),
            'rows': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--trigger', type=int, default=24000)
    parser.add_argument('--target', type=int, default=20000)
    parser.add_argument('--hard', type=int, default=32000)
    parser.add_argument('--candidate', choices=['v11', 'v36'], default='v11')
    args = parser.parse_args()
    source, out = Path(args.source).resolve(), Path(args.out).resolve()
    assert source.is_dir() and not out.exists()
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    rows = [inspect(sample, llm, args.trigger, args.target, args.hard, args.candidate)
            for sample in sorted(source.glob('r*-pruner_v11'))]
    out.mkdir(parents=True)
    thresholds = ({'trigger': 20000, 'target': 16000, 'hard': 28000} if args.candidate == 'v36' else
                  {'trigger': args.trigger, 'target': args.target, 'hard': args.hard})
    result = {'source': str(source), 'api_requests': 0, 'candidate': args.candidate,
              'thresholds': thresholds, 'samples': rows,
              'note': 'Fixed pruner actions and SDK views; excludes transient runtime notices and cannot infer changed future actions or quality.'}
    (out / 'same-trajectory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    raw = sum(r['raw_view_total'] for r in rows)
    pruned = sum(r['pruned_view_total'] for r in rows)
    print(json.dumps({'samples': len(rows), 'calls': sum(r['calls'] for r in rows),
                      'accepted': sum(r['accepted_compressions'] for r in rows),
                      'estimated_view_reduction': 1 - pruned / raw, 'api_requests': 0}))


if __name__ == '__main__':
    main()
