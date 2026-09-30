"""Zero-request replay of the frozen checkpoint that lost parser evidence."""
import json
import os
import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
from openhands.sdk.event import ActionEvent, ObservationEvent, MessageEvent
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.sdk.tool.builtins.finish import FinishAction
parser = argparse.ArgumentParser()
parser.add_argument('--version', choices=['v2', 'v3', 'v4'], default='v2')
args = parser.parse_args()
if args.version == 'v4':
    from context_pruner.adapters.openhands_v4 import project_events
elif args.version == 'v3':
    from context_pruner.adapters.openhands_v3 import project_events
else:
    from context_pruner.adapters.openhands_v2 import project_events
from context_pruner.middleware import ContextPrunerMiddleware
from context_pruner.plugin import ContextPluginConfig
from context_pruner.types import ContextBudget
from litellm import token_counter

sample = ROOT / 'runs/stage5-openhands/dotenv-three-arm-3x3-v6/r3-quoted_roundtrip-pruner_v1'
old = json.loads((sample / 'pruner-state.json').read_text(encoding='utf-8'))
ids = set(old['audit'][0]['forgotten_ids'])
events = []
classes = {'ActionEvent': ActionEvent, 'ObservationEvent': ObservationEvent, 'MessageEvent': MessageEvent}
for path in sorted(sample.glob('conversation/*/events/*.json')):
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw['id'] in ids:
        events.append(classes[raw['kind']].model_validate(raw))
assert len(events) == len(ids)
rows = project_events(events)
counter = lambda text: token_counter(model='gpt-4o', text=text)
middleware = ContextPrunerMiddleware(ContextPluginConfig(budget=ContextBudget(12000, 16000, 10000)), token_counter=counter)
hook = middleware.before_model(rows, task_state='Fix quoted value backslash roundtrip; preserve parser decoding behavior.', reserved_tokens=5000)
memory = '\n'.join(m['content'] for m in hook.messages)
symbols = ['decode_escapes', '_single_quote_escapes', 'parse_value']
source = (ROOT / '.tooling/upstream/python-dotenv-v1.2.1/src/dotenv/parser.py').read_text(encoding='utf-8')
source_lines = source.splitlines()
body_checks = {}
for node in ast.parse(source).body:
    if isinstance(node, ast.FunctionDef) and node.name in ('decode_escapes', 'parse_value'):
        body_checks[node.name] = all(line.strip() in memory for line in source_lines[node.lineno - 1:node.end_lineno] if line.strip())
result = {'api_requests': 0, 'checkpoint': str(sample.relative_to(ROOT)),
          'source_events': len(events), 'projected_tokens': counter('\n'.join(r['content'] for r in rows)),
          'memory_tokens': counter(memory), 'symbol_preservation': {s: s in memory for s in symbols},
          'task_state_present': bool(middleware.export_state()['plugin']['task_state']),
          'complete_function_body_preservation': body_checks}
(ROOT / f'runs/stage5-openhands/dotenv-three-arm-v7-check/replay-{args.version}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
assert all(result['symbol_preservation'].values()), 'Required code missing at replay checkpoint'
assert all(body_checks.values()), 'Function body missing at replay checkpoint'
