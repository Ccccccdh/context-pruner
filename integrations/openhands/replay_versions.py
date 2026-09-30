"""Same frozen action stream, two derived views; no model or filesystem tool calls."""
import json
import os
import ast
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
from openhands.sdk.event import ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.context.view import View
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.llm import LLM
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool
from openhands.tools.file_editor import FileEditorTool
from context_pruner.adapters.openhands_v5 import ContextPrunerCondenserV5


class ScopedEditorTool(FileEditorTool):
    pass


def main():
    source = ROOT / 'runs/stage5-openhands/dotenv-three-arm-3x3-v9-validation'
    target = ROOT / 'runs/stage5-openhands/semantic-version-replay-v5'
    target.mkdir(parents=True, exist_ok=True)
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    classes = {c.__name__: c for c in (ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent)}
    rows = []
    for sample in sorted(source.glob('r*-pruner_v4')):
        old_view, new_view = [], []
        adapter = ContextPrunerCondenserV5()
        observations = {}
        for file in sorted(sample.glob('conversation/*/events/*.json')):
            raw = json.loads(file.read_text(encoding='utf-8'))
            if raw['kind'] in classes:
                event = classes[raw['kind']].model_validate(raw)
                old_view.append(event); new_view.append(event)
                if hasattr(event, 'observation'):
                    observations[event.id] = event.observation
            elif raw['kind'] == 'Condensation':
                old = Condensation.model_validate(raw)
                old_after = list(old.apply(old_view))
                before = get_total_token_count(new_view, llm)
                candidate = adapter.condense(View(events=list(new_view)), llm)
                accepted = isinstance(candidate, Condensation)
                if accepted:
                    new_view = list(candidate.apply(new_view))
                after = get_total_token_count(new_view, llm)
                summaries = [e.summary for e in new_view if type(e).__name__ == 'CondensationSummaryEvent']
                memory = '\n'.join(summaries)
                row = {'sample': sample.name, 'old_condensation_id': old.id,
                       'old_after': get_total_token_count(old_after, llm), 'v5_before': before,
                       'v5_after': after, 'v5_accepted': accepted,
                       'memory_chars': len(memory), 'nested_headers': memory.count('Archived evidence rebuilt from original events.'),
                       'symbols_present': {s: s in memory for s in ('decode_escapes', '_single_quote_escapes', 'parse_value')},
                       'audit': adapter.export_state()['audit'][-1] if adapter.export_state()['audit'] else None}
                snapshots = [obs.new_content for key, obs in observations.items()
                             if key in adapter.export_state()['archived_sdk_source_ids']
                             and getattr(obs, 'path', '') and obs.path.endswith('parser.py')
                             and getattr(obs, 'new_content', None) is not None and not obs.is_error]
                if snapshots and accepted:
                    source_lines = snapshots[-1].splitlines()
                    row['function_bodies_preserved'] = {
                        node.name: all(line.strip() in memory for line in source_lines[node.lineno - 1:node.end_lineno] if line.strip())
                        for node in ast.parse(snapshots[-1]).body
                        if isinstance(node, ast.FunctionDef) and node.name in ('decode_escapes', 'parse_value')}
                    assert all(row['function_bodies_preserved'].values())
                assert row['nested_headers'] <= 1
                if accepted:
                    assert after < before
                    assert all(row['symbols_present'].values())
                rows.append(row)
                old_view = old_after
        (target / (sample.name + '-state.json')).write_text(json.dumps(adapter.export_state(), indent=2), encoding='utf-8')
    result = {'api_requests': 0, 'source': str(source.relative_to(ROOT)), 'checkpoints': rows,
              'note': 'Identical recorded action stream, different derived views. This does not measure live task-level savings.'}
    (target / 'replay.json').write_text(json.dumps(result, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
