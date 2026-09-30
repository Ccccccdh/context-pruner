"""No-provider replay of recorded OpenHands events at safe tool boundaries."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
os.environ['OH_PERSISTENCE_DIR'] = str(ROOT / '.tooling/openhands-replay-state')

from openhands.sdk.context.view import View
from openhands.sdk.event import ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.tools.file_editor import FileEditorTool
from prototype_v11 import ContextPrunerCondenserV11
from context_pruner.adapters.openhands_v4 import semantic_text
from symbol_tool_v31 import ScopedSymbolsTool as BaseScopedSymbolsTool
from validation_tasks_v32 import TASKS
from validation_tasks_v34 import TASKS as V34_TASKS
from validation_tasks_v29 import TASKS as V29_TASKS

TASKS = {**V29_TASKS, **TASKS, **V34_TASKS}
from verification_tool_v32 import ScopedTestsTool


class ScopedEditorTool(FileEditorTool):
    pass


class ScopedSymbolsTool(BaseScopedSymbolsTool):
    pass

EVENT_TYPES = {c.__name__: c for c in (ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent)}


def replay(sample: Path, llm: LLM, trigger: int, target: int, hard: int):
    report = json.loads((sample / 'report.json').read_text(encoding='utf-8'))
    task = report['task']
    adapter = ContextPrunerCondenserV11(trigger_tokens=trigger, target_tokens=target, hard_tokens=hard)
    original_ids, derived_ids, events, checkpoints = set(), set(), [], []
    max_event_tokens = 0
    for path in sorted(sample.glob('conversation/**/events/event-*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))
        kind = raw['kind']
        cls = EVENT_TYPES.get(kind)
        if cls is None:
            continue
        event = cls.model_validate(raw)
        original_ids.add(event.id)
        events.append(event)
        if kind != 'ObservationEvent':
            continue
        view = View(events=list(events))
        max_event_tokens = max(max_event_tokens, get_total_token_count(view.events, llm))
        candidate = adapter.condense(view, llm)
        if not isinstance(candidate, Condensation):
            continue
        new_events = list(candidate.apply(events))
        View(events=new_events).enforce_properties(events)
        state = adapter.export_state()
        entry = state['audit'][-1]
        assert entry['after'] <= hard and entry['after'] < entry['before']
        assert set(entry['forgotten_ids']) <= original_ids | derived_ids
        # Both the initial contract and later host feedback must survive verbatim.
        surviving = '\n'.join(semantic_text(e.to_llm_message()) for e in new_events if isinstance(e, MessageEvent))
        assert TASKS[task]['problem'] in surviving, (sample.name, 'contract lost')
        checkpoints.append({k: entry[k] for k in
                            ('before', 'after', 'protected_tokens', 'selected_units', 'omitted_units', 'task_contract_chars')})
        derived_ids.add(candidate.summary_event.id)
        events = new_events
    state = adapter.export_state()
    return {'sample': sample.name, 'task': task, 'checkpoints': checkpoints,
            'max_event_tokens': max_event_tokens,
            'skips': [row.get('skipped') for row in state['audit'] if 'skipped' in row],
            'source_references_valid': set(state['archived_sdk_source_ids']) <= {str(x) for x in original_ids}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--trigger', type=int, default=16000)
    parser.add_argument('--target', type=int, default=13000)
    parser.add_argument('--hard', type=int, default=24000)
    parser.add_argument('--arm', default='none')
    args = parser.parse_args()
    source, out = Path(args.source).resolve(), Path(args.out).resolve()
    assert source.is_dir() and not out.exists()
    out.mkdir(parents=True)
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    rows = [replay(sample, llm, args.trigger, args.target, args.hard)
            for sample in sorted(source.glob(f'r*-{args.arm}'))]
    result = {'source': str(source), 'api_requests': 0, 'thresholds':
              {'trigger': args.trigger, 'target': args.target, 'hard': args.hard}, 'samples': rows,
              'note': 'Recorded future actions do not react to derived views; mechanics only, not efficacy.'}
    (out / 'replay.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'samples': len(rows), 'accepted': sum(len(r['checkpoints']) for r in rows),
                      'skips': sum(len(r['skips']) for r in rows), 'api_requests': 0}))


if __name__ == '__main__':
    main()
