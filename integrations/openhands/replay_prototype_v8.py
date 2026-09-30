"""Replay frozen v12 raw events into v6. No tool execution or API requests."""
import os
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
from openhands.sdk.event import ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool
from openhands.tools.file_editor import FileEditorTool
from prototype_v8 import ContextPrunerCondenserV8
from verification_tool import ScopedTestsTool
from independent_tasks import TASKS

class ScopedEditorTool(FileEditorTool):
    pass

def main():
    source = ROOT / 'runs/stage5-openhands/independent-two-projects-3x3-v14'
    target = ROOT / 'runs/stage5-openhands/independent-replay-v8-prototype'
    target.mkdir(parents=True, exist_ok=False)
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    classes = {c.__name__: c for c in (ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent)}
    rows = []
    for sample in sorted(source.glob('r*-pruner_v6')):
        adapter, events, checkpoints = ContextPrunerCondenserV8(), [], []
        task = json.loads((sample / 'report.json').read_text(encoding='utf-8'))['task']
        for p in sorted(sample.glob('conversation/**/events/*.json')):
            raw = json.loads(p.read_text(encoding='utf-8'))
            if raw['kind'] in classes:
                events.append(classes[raw['kind']].model_validate(raw))
            elif raw['kind'] == 'Condensation':
                result = adapter.condense(View(events=list(events)), llm)
                if isinstance(result, Condensation):
                    events = list(result.apply(events))
                    state = adapter.export_state()
                    record = state['audit'][-1]
                    assert record['after'] <= adapter.hard_tokens
                    # The full observed task contract must survive once archived.
                    if record['task_contract_chars']:
                        assert TASKS[task]['problem'] in result.summary
                    checkpoints.append({k:record[k] for k in ('before','after','protected_tokens','selected_units','omitted_units','task_contract_chars')})
        state = adapter.export_state()
        (target / (sample.name + '-state.json')).write_text(json.dumps(state, indent=2),encoding='utf-8')
        rows.append({'sample': sample.name, 'checkpoints': checkpoints,
                     'skips': [r for r in state['audit'] if 'skipped' in r]})
    result = {'api_requests': 0, 'samples': rows,
              'note': 'Identical recorded events; different derived view. This verifies mechanics, not live quality or task input savings.'}
    (target / 'replay.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'samples':len(rows),'accepted_checkpoints':sum(len(r['checkpoints']) for r in rows),
                      'skips':sum(len(r['skips']) for r in rows),'api_requests':0}))

if __name__ == '__main__':
    main()
