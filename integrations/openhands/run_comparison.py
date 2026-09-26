"""Bounded three-arm OpenHands experiment on fixed public bug tasks."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from task_suite import TASKS, COMMIT, prepare, hashes, evaluate

ROOT = Path(__file__).resolve().parents[2]
ARMS = ('none', 'native_summary', 'pruner_v1')
PROTOCOL = {
    'version': 'openhands-dotenv-three-arm-v6',
    'model': 'openai/deepseek-v4-flash', 'temperature': 0,
    'thinking': 'disabled', 'api_retries': 0,
    'source_commit': COMMIT, 'tasks': list(TASKS),
    'arms': list(ARMS), 'repeats': 3,
    'trigger_input_tokens': 12000, 'pruner_target_tokens': 10000,
    'pruner_hard_tokens': 16000, 'native_max_events': 240,
    'max_agent_calls_per_sample': 14, 'max_summary_calls_per_sample': 5,
    'max_output_tokens': 1536, 'max_single_estimated_input': 40000,
    'max_total_estimated_input_per_sample': 300000,
    'phase_plan': ['read_and_diagnose_no_edit', 'implement', 'evaluate_and_correct'],
    'tools': ['scoped_editor', 'finish'],
    'initial_read_ranges': {'README.md': [167, 210], 'src/dotenv/main.py': [155, 230],
                            'src/dotenv/parser.py': [1, -1], 'tests/test_parser.py': [1, 100]},
    'acceptance': 'task-specific tests + unchanged upstream parser/variables tests + file boundary',
    'upstream_test_limitation': 'test_main.py uses POSIX-only sh; excluded on Windows',
    'cost': 'provider tokens include native summary overhead; money unknown, SDK model price not mapped',
    'ordering': 'rotate three arms by task and repeat; independent workspace and conversation per sample',
    'data': 'public MIT python-dotenv source; no private documents',
}

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    p.add_argument('--run', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    out = Path(args.out).resolve()
    if out.exists() and not args.resume:
        raise SystemExit('Output exists; --resume preserves completed samples.')
    out.mkdir(parents=True, exist_ok=True)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    from pydantic import PrivateAttr, SecretStr
    from litellm import token_counter
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.context.condenser import NoOpCondenser, LLMSummarizingCondenser
    from openhands.sdk.tool import Tool, register_tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from context_pruner.adapters.openhands import ContextPrunerCondenser

    upstream = ROOT / '.tooling/upstream/python-dotenv-v1.2.1'
    import subprocess
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=upstream, text=True).strip()
    assert actual == COMMIT
    manifest = dict(PROTOCOL)
    manifest['versions'] = {n: importlib.metadata.version(n) for n in
                            ('openhands-sdk', 'openhands-tools', 'litellm', 'context-pruner', 'pytest')}
    sources = [Path(__file__), Path(__file__).with_name('task_suite.py'),
               ROOT / 'context_pruner/adapters/openhands.py']
    manifest['source_hashes'] = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sources}
    if (out / 'manifest.json').exists():
        assert json.loads((out / 'manifest.json').read_text(encoding='utf-8')) == manifest, 'Frozen protocol changed'
    else:
        save(out / 'manifest.json', manifest)
    if args.check:
        baselines = {}
        for task in TASKS:
            sample = out / 'baseline' / task
            prepare(upstream, sample / 'workspace', task)
            result = evaluate(sample / 'workspace', task, sample / 'evaluation')
            assert result['returncode'] == 1 and 'failed,' in result['summary'], result
            baselines[task] = result
        save(out / 'baseline.json', baselines)
        print(json.dumps(baselines, indent=2))
        return 0
    if not args.run:
        print(json.dumps(manifest, indent=2))
        return 0
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('DEEPSEEK_API_KEY unavailable')

    class BoundedLLM(LLM):
        _ledger: dict = PrivateAttr(default_factory=dict)
        _sample: Path = PrivateAttr()
        _kind: str = PrivateAttr(default='agent')

        def completion(self, *a, **kw):
            messages = kw.get('messages', a[0] if a else [])
            tools = kw.get('tools') or []
            data = {'messages': [m.model_dump(mode='json') for m in messages],
                    'tools': [t.model_dump(mode='json') for t in tools]}
            estimated = token_counter(model='gpt-4o', text=json.dumps(data)) + 256
            ledger = self._ledger
            count = sum(c['kind'] == self._kind for c in ledger['calls'])
            limit = PROTOCOL['max_agent_calls_per_sample' if self._kind == 'agent' else 'max_summary_calls_per_sample']
            if count >= limit or estimated > 40000 or ledger['estimated_input'] + estimated > 300000:
                raise RuntimeError('Frozen experiment call/token limit')
            # Validate tool call/result consistency on every outgoing request.
            pending = set()
            for message in messages:
                if message.role == 'tool':
                    assert message.tool_call_id in pending, 'Orphan tool result'
                    pending.remove(message.tool_call_id)
                else:
                    assert not pending, 'Incomplete tool batch'
                    pending.update(c.id for c in message.tool_calls or [])
            assert not pending, 'Unresolved tool call'
            record = {'kind': self._kind, 'estimated_input': estimated,
                      'structural_valid': True, 'status': 'started'}
            ledger['calls'].append(record)
            ledger['estimated_input'] += estimated
            save(self._sample / 'ledger.json', ledger)
            print(f'{self._sample.name} {self._kind} call={count + 1} input~{estimated}', flush=True)
            start = time.perf_counter()
            try:
                response = super().completion(*a, **kw)
                record['status'] = 'returned'
                return response
            except Exception as exc:
                record.update(status='failed', error_type=type(exc).__name__)
                raise
            finally:
                record['latency_seconds'] = time.perf_counter() - start
                save(self._sample / 'ledger.json', ledger)

    class ScopedEditorExecutor(FileEditorExecutor):
        def __init__(self, **kw):
            self.scope = Path(kw['workspace_root']).resolve()
            super().__init__(**kw)

        def __call__(self, action, conversation=None):
            if not Path(action.path).resolve().is_relative_to(self.scope):
                return FileEditorObservation.from_text(text='Outside workspace blocked',
                                                       command=action.command, is_error=True)
            return super().__call__(action, conversation)

    # One sample at a time; registry factory receives the current immutable scope.
    current = {}
    class ScopedEditorTool(FileEditorTool):
        @classmethod
        def create(cls, conv_state):
            executor = ScopedEditorExecutor(workspace_root=str(current['workspace']),
                                            allowed_edits_files=[str(current['allowed'])])
            return [tool.set_executor(executor) for tool in super().create(conv_state)]
    register_tool(ScopedEditorTool.name, ScopedEditorTool)

    for repeat in range(3):
        for index, task in enumerate(TASKS):
            rotation = (repeat + index) % 3
            for arm in ARMS[rotation:] + ARMS[:rotation]:
                sample = out / f'r{repeat + 1}-{task}-{arm}'
                if (sample / 'report.json').exists():
                    continue
                if sample.exists():
                    raise SystemExit(f'Interrupted sample needs audit before rerun: {sample.name}')
                workspace = sample / 'workspace'
                original = prepare(upstream, workspace, task)
                save(sample / 'original_hashes.json', original)
                current.update(workspace=workspace, allowed=workspace / TASKS[task]['allowed'])
                ledger = {'calls': [], 'estimated_input': 0}
                def make_llm(kind):
                    llm = BoundedLLM(model=PROTOCOL['model'], api_key=SecretStr(key), base_url='https://api.deepseek.com',
                                     temperature=0, num_retries=0, timeout=60, max_output_tokens=1536,
                                     litellm_extra_body={'thinking': {'type': 'disabled'}}, usage_id=f'{sample.name}-{kind}')
                    llm._ledger = ledger
                    llm._sample = sample
                    llm._kind = kind
                    return llm
                agent_llm, summary_llm = make_llm('agent'), make_llm('summary')
                condenser = (NoOpCondenser() if arm == 'none' else
                             LLMSummarizingCondenser(llm=summary_llm, max_size=240, max_tokens=12000,
                                                     keep_first=2, hard_context_reset_max_retries=1)
                             if arm == 'native_summary' else ContextPrunerCondenser())
                agent = Agent(llm=agent_llm, tools=[Tool(name=ScopedEditorTool.name)],
                              include_default_tools=['FinishTool'], condenser=condenser)
                events = []
                def callback(event):
                    events.append({'type': type(event).__name__, 'id': str(event.id)})
                report = {'task': task, 'arm': arm, 'repeat': repeat + 1, 'success': False}
                conversation = None
                started = time.perf_counter()
                try:
                    conversation = Conversation(agent=agent, workspace=str(workspace), callbacks=[callback],
                                                persistence_dir=str(sample / 'conversation'), delete_on_close=False,
                                                visualizer=None, max_iteration_per_run=14)
                    conversation.send_message(
                        f'Work only inside {workspace}. This is python-dotenv v1.2.1. '
                        'Read TASK.md, README.md lines 167-210, src/dotenv/main.py lines 155-230, '
                        'src/dotenv/parser.py, and tests/test_parser.py lines 1-100 using view_range. '
                        f'to investigate the reported behavior: {TASKS[task]["problem"]} '
                        'For this first phase, do not edit. Read those ranges once, then use finish '
                        'with a diagnosis and change plan of at most 150 words. Do not read other files in this phase. '
                        'No terminal is available; tests will be run by the host after implementation.')
                    conversation.run()
                    report['investigation_no_edits'] = hashes(workspace) == original
                    conversation.send_message(
                        f'Implement the repair now. Only edit {TASKS[task]["allowed"]}. '
                        'Do not modify tests, documentation, other modules or dependencies. '
                        'Preserve existing public behavior outside the bug. Finish once the implementation is ready.')
                    conversation.run()
                    first = evaluate(workspace, task, sample / 'evaluation-first')
                    report['first_evaluation'] = first
                    feedback = 'All host acceptance and selected upstream regression tests passed.' if first['passed'] else (
                        'Host tests failed. Read-only test feedback follows:\n' +
                        (sample / 'evaluation-first/pytest.txt').read_text(encoding='utf-8')[-16000:])
                    conversation.send_message(feedback + f' Complete the final review; if needed correct only {TASKS[task]["allowed"]}. '
                                              'Do not edit tests. Finish with a concise summary and any remaining limitations.')
                    conversation.run()
                    final = evaluate(workspace, task, sample / 'evaluation-final')
                    changed = sorted(k for k in set(original) | set(hashes(workspace))
                                     if original.get(k) != hashes(workspace).get(k))
                    report.update(final_evaluation=final, changed_files=changed,
                                  file_boundary_ok=set(changed) <= {TASKS[task]['allowed']})
                    report['success'] = bool(final['passed'] and report['file_boundary_ok'] and report['investigation_no_edits'])
                except Exception as exc:
                    report['error_type'] = type(exc).__name__
                    import traceback
                    report['diagnostic'] = traceback.format_exc().replace(key, '[REDACTED]')
                finally:
                    report.update(elapsed_seconds=time.perf_counter() - started, events=events, ledger=ledger,
                                  agent_metrics=agent_llm.metrics.model_dump(mode='json'),
                                  summary_metrics=summary_llm.metrics.model_dump(mode='json'))
                    if arm == 'pruner_v1':
                        save(sample / 'pruner-state.json', condenser.finalize({'success': report['success']}))
                    if conversation:
                        conversation.close()
                    save(sample / 'report.json', report)
                    summarize(out)
                    print(json.dumps({k: report.get(k) for k in ('task', 'arm', 'repeat', 'success', 'error_type')}) , flush=True)
                if report.get('error_type') and not ledger['calls']:
                    raise SystemExit('Local pre-request failure; preserved report, stopped batch for diagnosis.')
    summarize(out)
    return 0

def summarize(out):
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(out.glob('r*/report.json'))]
    stats = {}
    for arm in ARMS:
        rows = [r for r in reports if r['arm'] == arm]
        token = lambda r, kind, key: r[kind + '_metrics']['accumulated_token_usage'][key]
        stats[arm] = {
            'samples': len(rows), 'successes': sum(r['success'] for r in rows),
            'agent_input_tokens': sum(token(r, 'agent', 'prompt_tokens') for r in rows),
            'summary_input_tokens': sum(token(r, 'summary', 'prompt_tokens') for r in rows),
            'total_input_tokens': sum(token(r, k, 'prompt_tokens') for r in rows for k in ('agent', 'summary')),
            'total_output_tokens': sum(token(r, k, 'completion_tokens') for r in rows for k in ('agent', 'summary')),
            'model_calls': sum(len(r['ledger']['calls']) for r in rows),
            'summary_calls': sum(c['kind'] == 'summary' for r in rows for c in r['ledger']['calls']),
            'failed_calls': sum(c['status'] != 'returned' for r in rows for c in r['ledger']['calls']),
            'peak_estimated_input': max((c['estimated_input'] for r in rows for c in r['ledger']['calls']), default=0),
            'elapsed_seconds': sum(r['elapsed_seconds'] for r in rows),
            'condensation_events': sum(e['type'] == 'Condensation' for r in rows for e in r['events']),
        }
    save(out / 'summary.json', {'expected_samples': 27, 'completed_samples': len(reports),
                              'complete': len(reports) == 27, 'arms': stats})

if __name__ == '__main__':
    raise SystemExit(main())
