"""Compare frozen v36 and v41 condensers on identical saved v40 tool actions.

No model requests are made. Fixed future actions cannot establish a task-success
effect; this replay checks only structural safety, edit-scope retention and view size.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
os.environ['OH_PERSISTENCE_DIR'] = str(ROOT / '.tooling/openhands-replay-state')

from context_pruner.adapters.openhands_v36 import ContextPrunerCondenserV36
from context_pruner.adapters.openhands_v41 import ContextPrunerCondenserV41
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM

from replay_v33_checkpoints import EVENT_TYPES
from validation_tasks_v40 import TASKS


SOURCE = ROOT / 'runs/stage5-openhands/django-16315-v40-pilot-retry-02'
OUTPUT = ROOT / 'runs/stage5-openhands/django-16315-v41-contract-replay'


def replay(sample: Path, adapter, llm: LLM, contract: str | None):
    raw_view, compressed_view, rows = [], [], []
    previous_kind = None
    for path in sorted(sample.glob('conversation/**/events/event-*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        kind = record['kind']
        event_type = EVENT_TYPES.get(kind)
        if event_type is None:
            continue
        event = event_type.model_validate(record)
        if kind == 'ActionEvent' and previous_kind != 'ActionEvent':
            original = list(compressed_view)
            candidate = adapter.condense(View(events=original), llm)
            if isinstance(candidate, Condensation):
                compressed_view = list(candidate.apply(original))
                View(events=compressed_view).enforce_properties(original)
                if contract is not None:
                    assert contract in candidate.summary
            raw_tokens = get_total_token_count(raw_view, llm)
            compressed_tokens = get_total_token_count(compressed_view, llm)
            rows.append({'call': len(rows) + 1, 'raw': raw_tokens,
                         'compressed': compressed_tokens})
        raw_view.append(event)
        compressed_view.append(event)
        previous_kind = kind
    report = json.loads((sample / 'report.json').read_text(encoding='utf-8'))
    assert len(rows) == len(report['agent_metrics']['token_usages'])
    audit = adapter.export_state()['audit']
    return {'calls': len(rows), 'accepted': sum('forgotten_ids' in row for row in audit),
            'contract_chars': [row.get('task_contract_chars') for row in audit
                               if 'forgotten_ids' in row],
            'raw_view_total': sum(row['raw'] for row in rows),
            'compressed_view_total': sum(row['compressed'] for row in rows),
            'safe': all(row.get('after', 0) <= adapter.hard_tokens and
                        row.get('after', 0) < row['before'] for row in audit
                        if 'forgotten_ids' in row),
            'rows': rows}


def main():
    assert SOURCE.is_dir() and not OUTPUT.exists()
    sample = next(SOURCE.glob('r*-pruner_v40'))
    task = TASKS['django_bulk_create_db_column']
    contract = 'Edit only: ' + ', '.join(task['allowed']) + '. All other source files are read-only.'
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    old = replay(sample, ContextPrunerCondenserV36(), llm, None)
    new = replay(sample, ContextPrunerCondenserV41(protected_contract=contract), llm, contract)
    assert old['safe'] and new['safe']
    assert old['calls'] == new['calls']
    assert new['accepted'] > 0 and all(n >= len(contract) for n in new['contract_chars'])
    result = {'source': str(SOURCE), 'api_requests': 0,
              'contract': contract, 'v36': old, 'v41': new,
              'note': 'Identical recorded tool actions. View-size mechanics only; no success or causal inference.'}
    OUTPUT.mkdir(parents=True)
    (OUTPUT / 'replay.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({name: {key: value for key, value in data.items() if key != 'rows'}
                      for name, data in [('v36', old), ('v41', new)]}, indent=2))


if __name__ == '__main__':
    main()
