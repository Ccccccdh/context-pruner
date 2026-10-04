"""Balanced r4 multi-task development batch with per-sample failure preservation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI

from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as r4


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('mock', 'api'), default='mock')
    parser.add_argument('--task-ids', default='incident_triage,release_readiness,customer_migration')
    parser.add_argument('--methods', default=','.join(r4.METHODS))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--model', default='deepseek-v4-flash')
    parser.add_argument('--base-url', default='https://api.deepseek.com')
    parser.add_argument('--provider-soft', type=int, default=1200)
    parser.add_argument('--provider-hard', type=int, default=3000)
    parser.add_argument('--provider-target', type=int, default=900)
    parser.add_argument('--soft-limit', type=int, default=0)
    parser.add_argument('--hard-limit', type=int, default=0)
    parser.add_argument('--target', type=int, default=0)
    parser.add_argument('--fixed-reserved-tokens', type=int, default=300)
    parser.add_argument('--max-output-tokens', type=int, default=512)
    parser.add_argument('--max-summary-tokens', type=int, default=1024)
    parser.add_argument('--max-summary-calls', type=int, default=4)
    parser.add_argument('--max-api-requests', type=int, default=200)
    parser.add_argument('--confirm-send-synthetic-data', action='store_true')
    parser.add_argument('--out', default='runs/stage5-crewai')
    parser.add_argument('--experiment-id', default='crewai-two-role-r4-multitask-dev-01')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.max_api_requests < 1:
        raise SystemExit('repeats and max-api-requests must be positive')
    selected = tuple(part.strip() for part in args.task_ids.split(',') if part.strip())
    known = base.load_tasks(r4.TASK_FILE)
    tasks = [task for task in known if task['task_id'] in selected]
    if not tasks or set(selected) != {task['task_id'] for task in tasks}:
        raise SystemExit('unknown or missing task')
    requested = tuple(part.strip() for part in args.methods.split(',') if part.strip())
    if set(requested) != set(r4.METHODS) or len(requested) != len(r4.METHODS):
        raise SystemExit('multi-task batch requires all three arms')
    methods = r4.METHODS
    plan = r4.balanced_plan(tasks, methods, args.repeats)
    print(f'r4 multi-task plan: {len(plan)} samples, {len(tasks)} tasks, '
          f'{args.repeats} repeats, {args.max_api_requests} request slots', flush=True)
    if args.plan:
        print('No API request was sent in --plan mode.', flush=True)
        return
    if args.mode == 'api' and not args.confirm_send_synthetic_data:
        raise SystemExit('API mode requires --confirm-send-synthetic-data')
    key = os.getenv('OPENAI_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or ''
    if args.mode == 'api' and not key:
        raise SystemExit('OPENAI_API_KEY or DEEPSEEK_API_KEY is not set')
    output = Path(args.out) / args.experiment_id
    if output.exists():
        raise SystemExit(f'experiment directory already exists: {output}')
    budget, calibration = base.resolve_budget(args)
    output.mkdir(parents=True)
    manifest = {
        'protocol': 'crewai-two-role-r4-handoff-quality',
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'task_sha256': hashlib.sha256(r4.TASK_FILE.read_bytes()).hexdigest(),
        'tasks': [task['task_id'] for task in tasks],
        'methods': methods, 'repeats': args.repeats,
        'model': args.model, 'mode': args.mode, 'base_url': args.base_url,
        'budget': calibration.as_manifest(),
        'limits': {
            'fixed_reserved_tokens': args.fixed_reserved_tokens,
            'max_output_tokens': args.max_output_tokens,
            'max_summary_tokens': args.max_summary_tokens,
            'max_summary_calls': args.max_summary_calls,
        },
        'max_api_requests': args.max_api_requests,
        'failure_policy': 'preserve sample failure and continue unless provider unavailable or request cap',
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    request_budget = base.RequestBudget(args.max_api_requests)
    client = OpenAI(api_key=key, base_url=args.base_url, max_retries=0) if args.mode == 'api' else None
    try:
        with (output / 'results.jsonl').open('w', encoding='utf-8') as handle:
            for task, repeat, method in plan:
                row = r4.run_case(task, method, repeat, args.mode, client,
                                  request_budget, args, budget)
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                handle.flush()
                print(task['task_id'], repeat, method, row['success'],
                      row['all_arm_total_tokens'], row['error'], flush=True)
                if request_budget.used >= request_budget.limit:
                    break
                error = row['error'].lower()
                if any(term in error for term in (
                    'apiconnectionerror', 'apitimeouterror', 'connection error',
                    'insufficient balance', 'rate limit', 'authenticationerror',
                )):
                    break
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    main()
