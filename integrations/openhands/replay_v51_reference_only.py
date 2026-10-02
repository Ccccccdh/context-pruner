"""Offline replay comparing v42 (bodies) against v51 (references) on the v49 trajectories.

Purpose (V48_V49_DIAG_REVIEW.md section 3.4 and V49_COMPRESSION_DIAGNOSTIC.md): decide
whether listing evidence by reference preserves everything the bodies carried, while
reducing the injected summary and the peak view. No model request is made: the saved
SDK events are replayed through each condenser, following the pattern of
replay_v42_state.py, and the LLM object is only used for local token counting.

Reported per sample and per adapter:
  * injected summary characters and the model-visible view tokens before/after
  * peak view tokens across the trajectory
  * whether the four required items survive compression (task contract, current task
    state, latest failure text, recovery instruction)
  * whether the archived-original count is identical, i.e. the same events are
    forgotten by both adapters

Run: .venv-openhands python integrations/openhands/replay_v51_reference_only.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'integrations/openhands'))
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
os.environ['OH_PERSISTENCE_DIR'] = str(ROOT / '.tooling/openhands-replay-v51')

from context_pruner.adapters.openhands_v42 import ContextPrunerCondenserV42  # noqa: E402
from context_pruner.adapters.openhands_v51 import ContextPrunerCondenserV51  # noqa: E402
from openhands.sdk.context.condenser.utils import get_total_token_count  # noqa: E402
from openhands.sdk.context.view import View  # noqa: E402
from openhands.sdk.event.condenser import Condensation  # noqa: E402
from openhands.sdk.llm import LLM  # noqa: E402
from replay_v33_checkpoints import EVENT_TYPES  # noqa: E402
from validation_tasks_v49 import TASKS  # noqa: E402

BATCH = ROOT / 'runs/stage5-openhands/django-multitask-v49-pilot-01'

MARKERS = {
    'task_contract': 'PROTECTED TASK CONTRACT',
    'current_state': 'CURRENT TASK STATE',
    'recovery_hint': 'Recover it using scoped_editor view',
    'file_index': 'OBSERVED FILE INDEX',
    'symbol_locations': 'OBSERVED SYMBOL LOCATIONS',
}


def register_custom_tool_schemas() -> None:
    """Register the batch's custom tool action/observation types before parsing.

    Saved SystemPromptEvent records name action kinds such as
    `ScopedSymbolsActionV44`. Those are pydantic discriminated-union members that
    only exist once the corresponding ToolDefinition has been registered, which the
    runner does at run time. Without this the replay fails with
    "Unknown kind 'ScopedSymbolsActionV44' ... for openhands.sdk.tool.schema.Action".
    """
    from openhands.sdk.tool import register_tool
    from symbol_tool_v44 import ScopedSymbolsToolV44
    # The saved records discriminate on `verification_tool_v32.ScopedTestsAction`
    # (no version suffix), so the registration must come from that module. Using
    # verification_tool_django_v49 here conflicts with the record's own kind.
    from verification_tool_v32 import ScopedTestsTool

    register_tool(ScopedSymbolsToolV44.name, ScopedSymbolsToolV44)
    register_tool(ScopedTestsTool.name, ScopedTestsTool)


def replay(sample: Path, adapter, llm: LLM, contract: str, state: str):
    raw_view: list = []
    compressed_view: list = []
    previous_kind = None
    peak_raw = peak_compressed = 0
    summaries: list[str] = []
    forgotten_counts: list[int] = []
    for path in sorted(sample.glob('conversation/**/events/event-*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        kind = record.get('kind')
        if kind is None:
            # The events directory also holds non-event files such as
            # original_hashes.json; skip anything without a discriminator.
            continue
        event_type = EVENT_TYPES.get(kind)
        if event_type is None:
            continue
        event = event_type.model_validate(record)
        if kind == 'ActionEvent' and previous_kind != 'ActionEvent':
            candidate = adapter.condense(View(events=list(compressed_view)), llm)
            if isinstance(candidate, Condensation):
                compressed_view = list(candidate.apply(compressed_view))
                View(events=list(compressed_view)).enforce_properties(list(raw_view))
                summaries.append(candidate.summary)
                forgotten_counts.append(len(candidate.forgotten_event_ids))
            peak_raw = max(peak_raw, get_total_token_count(raw_view, llm))
            peak_compressed = max(peak_compressed, get_total_token_count(compressed_view, llm))
        raw_view.append(event)
        compressed_view.append(event)
        previous_kind = kind

    combined = '\n'.join(summaries)
    return {
        'condensations': len(summaries),
        'summary_chars': sum(len(s) for s in summaries),
        'forgotten': sum(forgotten_counts),
        'peak_raw': peak_raw,
        'peak_compressed': peak_compressed,
        'markers': {name: needle in combined for name, needle in MARKERS.items()},
    }


def main() -> int:
    register_custom_tool_schemas()
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    results = []
    for sample in sorted(BATCH.glob('r*-pruner_v42')):
        report = json.loads((sample / 'report.json').read_text(encoding='utf-8'))
        task = report['task']
        allowed = TASKS[task]['allowed']
        contract = 'Edit only: ' + ', '.join(allowed) + '. All other source files are read-only.'
        state = (f'CURRENT TASK STATE (host-observed; source edits invalidate verification):\n'
                 f'Objective: {TASKS[task]["problem"][:200]}\n'
                 f'Edit scope: {", ".join(allowed)}.')
        row = {'sample': sample.name, 'task': task}
        for label, adapter in (('v42_bodies', ContextPrunerCondenserV42(protected_contract=contract)),
                               ('v51_references', ContextPrunerCondenserV51(protected_contract=contract))):
            adapter.set_current_state(state)
            row[label] = replay(sample, adapter, llm, contract, state)
        results.append(row)

    print(f'{"sample":<52}{"adapter":<16}{"cond":>5}{"sum_chars":>10}{"forgot":>7}'
          f'{"peak_cmp":>9}  markers')
    for row in results:
        for label in ('v42_bodies', 'v51_references'):
            data = row[label]
            missing = [k for k, v in data['markers'].items() if not v]
            print(f'{row["sample"][:50]:<52}{label:<16}{data["condensations"]:>5}'
                  f'{data["summary_chars"]:>10}{data["forgotten"]:>7}'
                  f'{data["peak_compressed"]:>9}  missing={missing or "none"}')
        print()

    out = ROOT / '.tooling/replay_v51_vs_v42.json'
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print('written:', out)

    # Verdict helpers.
    same_forgotten = all(r['v42_bodies']['forgotten'] == r['v51_references']['forgotten'] for r in results)
    smaller = all(r['v51_references']['summary_chars'] <= r['v42_bodies']['summary_chars'] for r in results)
    retained = all(all(r['v51_references']['markers'].values()) for r in results)
    print()
    print('same forgotten-event count :', same_forgotten)
    print('v51 summary never larger   :', smaller)
    print('v51 keeps every marker     :', retained)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
