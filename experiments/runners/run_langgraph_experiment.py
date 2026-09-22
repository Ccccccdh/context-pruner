"""Phase-3 LangGraph plugin on/off paired experiment runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from agent_demo import KnowledgeBase, MockLLM, OpenAICompatClient, load_tasks
from agent_demo.langgraph_agent import LangGraphReActAgent
from context_pruner import ContextBudget, __version__
from metrics.summary import build_report, discover, _write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangGraph 插件开关配对实验")
    parser.add_argument("--tasks", default="tasks/stage2")
    parser.add_argument("--task-ids", default=None)
    parser.add_argument("--methods", default="none,pruner_v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--context-soft-limit", type=int, default=800)
    parser.add_argument("--context-hard-limit", type=int, default=1000)
    parser.add_argument("--context-target", type=int, default=650)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", default="runs/stage3-langgraph")
    parser.add_argument("--experiment-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> Path:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    if args.repeats <= 0 or args.max_turns <= 0:
        raise SystemExit("--repeats 与 --max-turns 必须大于 0")
    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    if not methods or any(item not in {"none", "pruner_v1"} for item in methods):
        raise SystemExit("--methods 目前只支持 none,pruner_v1")
    if len(methods) != len(set(methods)):
        raise SystemExit("--methods 不能重复")
    selected = {item.strip() for item in (args.task_ids or "").split(",") if item.strip()}
    all_tasks = load_tasks(args.tasks)
    tasks = [task for task in all_tasks if not selected or task.task_id in selected]
    missing = selected - {task.task_id for task in tasks}
    if missing:
        raise SystemExit(f"未找到指定任务：{', '.join(sorted(missing))}")
    if not tasks:
        raise SystemExit("没有可运行任务")

    experiment_id = args.experiment_id or datetime.now().strftime("langgraph-%Y%m%d-%H%M%S")
    if Path(experiment_id).name != experiment_id or experiment_id in {".", ".."}:
        raise SystemExit("--experiment-id 只能是安全目录名")
    root = Path(args.out) / experiment_id
    manifest_path = root / "suite_manifest.json"
    existing_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else None
    )
    if manifest_path.exists() and not args.resume:
        raise SystemExit(f"实验目录已存在，拒绝覆盖：{root}")
    root.mkdir(parents=True, exist_ok=True)
    budget = ContextBudget(
        args.context_soft_limit,
        args.context_hard_limit,
        args.context_target,
    )
    manifest = {
        "schema_version": 1,
        "status": "running",
        "framework": "langgraph",
        "experiment_id": experiment_id,
        "started_at": datetime.now().astimezone().isoformat(),
        "methods": methods,
        "tasks": str(Path(args.tasks).resolve()),
        "selected_task_ids": sorted(selected),
        "task_set_hash": _hash_file_or_directory(Path(args.tasks)),
        "task_count": len(tasks),
        "repeats": args.repeats,
        "model": args.model or ("mock" if args.mock else "deepseek-v4-flash"),
        "base_url": args.base_url or (None if args.mock else "https://api.deepseek.com"),
        "temperature": args.temperature,
        "max_turns": args.max_turns,
        "context_budget": {
            "soft_limit_tokens": budget.soft_limit_tokens,
            "hard_limit_tokens": budget.hard_limit_tokens,
            "target_tokens": budget.target_tokens,
        },
        "execution_order": "pair-interleaved",
        "context_pruner_version": __version__,
        "python_version": platform.python_version(),
    }
    if args.resume and existing_manifest is not None:
        errors = _resume_compatibility_errors(existing_manifest, manifest)
        if errors:
            raise SystemExit("断点续跑配置不一致：" + "；".join(errors))
        manifest["started_at"] = existing_manifest.get(
            "started_at", manifest["started_at"]
        )
        manifest["resume_count"] = int(existing_manifest.get("resume_count", 0)) + 1
        manifest["resumed_at"] = datetime.now().astimezone().isoformat()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        for sample_index, (task, repeat) in enumerate(
            (task, repeat) for task in tasks for repeat in range(args.repeats)
        ):
            offset = sample_index % len(methods)
            ordered = [*methods[offset:], *methods[:offset]]
            run_id = f"r{repeat:02d}"
            print(f"\n===== {task.task_id}:{run_id}；方法顺序：{','.join(ordered)} =====")
            for method in ordered:
                method_dir = root / method
                method_dir.mkdir(parents=True, exist_ok=True)
                summary_path = method_dir / f"{task.task_id}.{run_id}.summary.json"
                turns_path = method_dir / f"{task.task_id}.{run_id}.turns.jsonl"
                state_path = method_dir / f"{task.task_id}.{run_id}.plugin-state.json"
                messages_path = method_dir / f"{task.task_id}.{run_id}.messages.json"
                if args.resume and all(
                    path.exists() for path in (summary_path, turns_path, state_path, messages_path)
                ):
                    print(f"{method:<10} skipped")
                    continue
                env = KnowledgeBase(task.docs)
                client = (
                    MockLLM(env)
                    if args.mock
                    else OpenAICompatClient(
                        model=args.model,
                        base_url=args.base_url,
                        max_output_tokens=args.max_output_tokens,
                    )
                )
                result = LangGraphReActAgent(
                    client,
                    env,
                    max_turns=args.max_turns,
                    method=method,
                    temperature=args.temperature,
                    context_budget=budget,
                ).run(task)
                summary_path.write_text(
                    json.dumps(result.summary(experiment_id, run_id), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                turns_path.write_text(
                    "".join(
                        json.dumps(
                            {
                                "step": turn.step,
                                "tokens_in": turn.tokens_in,
                                "full_tokens_in": turn.full_tokens_in,
                                "tokens_out": turn.tokens_out,
                                "latency": turn.latency,
                                "tool_ledger_tokens": turn.tool_ledger_tokens,
                                "ephemeral_context_tokens": turn.ephemeral_context_tokens,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                        for turn in result.turns
                    ),
                    encoding="utf-8",
                )
                state_path.write_text(
                    json.dumps(result.plugin_state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                messages_path.write_text(
                    json.dumps(result.messages, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{method:<10} success={result.success} turns={result.num_turns} "
                    f"tokens={result.tokens_in_total} peak={result.peak_context_tokens}"
                )

        samples = discover(root)
        report = build_report(samples, baseline="none")
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_csv(root / "method_summary.csv", report["methods"])
        _write_csv(root / "paired_comparison.csv", report["paired_to_baseline"])
        manifest.update(
            {
                "status": "completed",
                "finished_at": datetime.now().astimezone().isoformat(),
                "sample_count": len(samples),
                "api_retry_count": sum(sample.api_retry_count for sample in samples),
                "completion_rejection_count": sum(
                    sample.completion_rejection_count for sample in samples
                ),
            }
        )
        print(f"\n阶段三 LangGraph 报告已生成：{root}")
    except Exception as error:
        manifest.update(
            {
                "status": "failed",
                "failed_at": datetime.now().astimezone().isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        raise
    finally:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return root


def _hash_file_or_directory(path: Path) -> str:
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    for file in files:
        digest.update(file.name.encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _resume_compatibility_errors(existing: dict, current: dict) -> list[str]:
    keys = (
        "framework",
        "experiment_id",
        "methods",
        "tasks",
        "selected_task_ids",
        "task_set_hash",
        "task_count",
        "repeats",
        "model",
        "base_url",
        "temperature",
        "max_turns",
        "context_budget",
        "execution_order",
        "context_pruner_version",
    )
    return [
        f"{key}: existing={existing.get(key)!r}, current={current.get(key)!r}"
        for key in keys
        if existing.get(key) != current.get(key)
    ]


if __name__ == "__main__":
    main()
