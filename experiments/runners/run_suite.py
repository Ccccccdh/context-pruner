"""一键运行阶段二主对照、消融实验并生成可复现报告。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_demo import load_tasks


MAIN_METHODS = ("none", "window", "summary", "pruner_v0", "pruner_v1")
ABLATION_METHODS = ("ablation_a", "ablation_b", "ablation_c", "ablation_d")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行阶段二完整对照实验")
    parser.add_argument("--suite", choices=("main", "ablation", "all"), default="all")
    parser.add_argument(
        "--methods",
        default=None,
        help="覆盖 suite 预设，多个方法用英文逗号分隔",
    )
    parser.add_argument("--baseline", default=None, help="配对比较基线，必须包含在方法列表中")
    parser.add_argument("--tasks", default="tasks/stage2", help="任务目录或 JSON 文件")
    parser.add_argument(
        "--task-ids",
        default=None,
        help="只运行指定任务，多个 task_id 用英文逗号分隔",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--execution-order",
        choices=("method-major", "pair-interleaved"),
        default="method-major",
        help="方法整批运行，或按 task/run 交错并轮换方法顺序以减小 API 时段偏差",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--window-steps", type=int, default=4)
    parser.add_argument("--context-soft-limit", type=int, default=None)
    parser.add_argument("--context-hard-limit", type=int, default=None)
    parser.add_argument("--context-target", type=int, default=None)
    parser.add_argument("--archive-db", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--out", default="runs/stage2")
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑已有套件，并让各方法跳过已完成的 task/run",
    )
    parser.add_argument(
        "--no-fault-injection",
        action="store_true",
        help="关闭任务集声明的确定性故障；默认开启",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-max-retries", type=int, default=5)
    parser.add_argument("--api-retry-base-delay", type=float, default=1.0)
    parser.add_argument("--api-retry-max-delay", type=float, default=20.0)
    parser.add_argument("--api-timeout", type=float, default=60.0)
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument("--compressor-cost-per-million", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> Path:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    if args.repeats <= 0:
        raise SystemExit("--repeats 必须大于 0")
    experiment_id = args.experiment_id or datetime.now().strftime("stage2-%Y%m%d-%H%M%S")
    if Path(experiment_id).name != experiment_id or experiment_id in {".", ".."}:
        raise SystemExit("--experiment-id 只能是单个安全目录名")

    methods = _resolve_methods(args.suite, args.methods)
    baseline = args.baseline or ("ablation_a" if args.suite == "ablation" and not args.methods else methods[0])
    experiment_root = Path(args.out) / experiment_id
    suite_manifest_path = experiment_root / "suite_manifest.json"
    baseline_has_results = (
        (experiment_root / baseline).exists()
        and any((experiment_root / baseline).glob("*.summary.json"))
    )
    if baseline not in methods and not (args.resume and baseline_has_results):
        raise SystemExit(f"配对基线 {baseline!r} 不在方法列表中或已有结果中")
    if suite_manifest_path.exists() and not args.resume:
        raise SystemExit(f"实验套件目录已存在，拒绝混入新结果：{experiment_root}")
    experiment_root.mkdir(parents=True, exist_ok=True)

    started_clock = time.perf_counter()
    started_at = datetime.now().astimezone().isoformat()
    existing_manifest = (
        json.loads(suite_manifest_path.read_text(encoding="utf-8"))
        if suite_manifest_path.exists()
        else None
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "status": "running",
        "started_at": started_at,
        "suite": args.suite,
        "methods": list(methods),
        "baseline": baseline,
        "tasks": str(Path(args.tasks).resolve()),
        "selected_task_ids": _parse_csv(args.task_ids),
        "repeats": args.repeats,
        "execution_order": args.execution_order,
        "max_turns": args.max_turns,
        "max_output_tokens": args.max_output_tokens,
        "window_steps": args.window_steps,
        "temperature": args.temperature,
        "model": args.model,
        "base_url": args.base_url,
        "context_budget": {
            "soft_limit_tokens": args.context_soft_limit,
            "hard_limit_tokens": args.context_hard_limit,
            "target_tokens": args.context_target,
        },
        "api_retry": {
            "max_retries": args.api_max_retries,
            "base_delay_seconds": args.api_retry_base_delay,
            "max_delay_seconds": args.api_retry_max_delay,
            "timeout_seconds": args.api_timeout,
        },
        "seed": args.seed,
        "mock": args.mock,
        "fault_injection_enabled": not args.no_fault_injection,
        "command": [
            sys.executable,
            "-m",
            "experiments.runners.run_suite",
            *(argv or sys.argv[1:]),
        ],
    }
    if args.resume and existing_manifest is not None:
        compatibility_errors = _suite_resume_compatibility_errors(existing_manifest, manifest)
        if compatibility_errors:
            raise SystemExit(
                "断点续跑套件配置不一致：" + "；".join(compatibility_errors)
            )
        manifest["started_at"] = existing_manifest.get("started_at", started_at)
        manifest["methods"] = list(
            dict.fromkeys([*existing_manifest.get("methods", []), *methods])
        )
        manifest["resume_count"] = int(existing_manifest.get("resume_count", 0)) + 1
        manifest["resumed_at"] = started_at
        manifest["previous_attempt"] = {
            key: existing_manifest[key]
            for key in ("status", "finished_at", "error_type", "error")
            if key in existing_manifest
        }
    _write_json(suite_manifest_path, manifest)

    try:
        if args.execution_order == "pair-interleaved":
            task_ids = _selected_task_ids(args.tasks, args.task_ids)
            for sample_index, (task_id, rep) in enumerate(
                (task_id, rep)
                for task_id in task_ids
                for rep in range(args.repeats)
            ):
                offset = sample_index % len(methods)
                ordered_methods = (*methods[offset:], *methods[:offset])
                sample_key = f"{task_id}:r{rep:02d}"
                print(
                    f"\n===== 交错样本：{sample_key}；方法顺序："
                    f"{','.join(ordered_methods)} ====="
                )
                for method in ordered_methods:
                    method_manifest = experiment_root / method / "run_manifest.json"
                    command = _method_command(
                        args,
                        experiment_id,
                        method,
                        resume=args.resume or method_manifest.exists(),
                        sample_key=sample_key,
                    )
                    subprocess.run(command, check=True, cwd=PROJECT_ROOT)
        else:
            for method in methods:
                print(f"\n===== 阶段二方法：{method} =====")
                command = _method_command(
                    args,
                    experiment_id,
                    method,
                    resume=args.resume,
                )
                subprocess.run(command, check=True, cwd=PROJECT_ROOT)

        report_path = experiment_root / "report.json"
        summary_path = experiment_root / "method_summary.csv"
        paired_path = experiment_root / "paired_comparison.csv"
        subprocess.run(
            [
                sys.executable,
                "-m", "metrics.summary",
                str(experiment_root),
                "--baseline", baseline,
                "--out", str(summary_path),
                "--paired-out", str(paired_path),
                "--json", str(report_path),
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "status": "completed",
                "finished_at": datetime.now().astimezone().isoformat(),
                "duration_seconds": round(time.perf_counter() - started_clock, 6),
                "sample_count": report["sample_count"],
                "report": report_path.name,
                "method_summary": summary_path.name,
                "paired_comparison": paired_path.name,
            }
        )
        _write_json(suite_manifest_path, manifest)
    except BaseException as error:
        manifest.update(
            {
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(),
                "duration_seconds": round(time.perf_counter() - started_clock, 6),
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        _write_json(suite_manifest_path, manifest)
        raise

    print(f"\n阶段二报告已生成：{experiment_root}")
    return experiment_root


def _suite_methods(suite: str) -> tuple[str, ...]:
    if suite == "main":
        return MAIN_METHODS
    if suite == "ablation":
        return ABLATION_METHODS
    return MAIN_METHODS + ABLATION_METHODS


def _resolve_methods(suite: str, raw: str | None) -> tuple[str, ...]:
    if not raw:
        return _suite_methods(suite)
    methods = tuple(_parse_csv(raw))
    known = set(MAIN_METHODS + ABLATION_METHODS)
    unknown = [method for method in methods if method not in known]
    if unknown:
        raise SystemExit(f"未知实验方法：{', '.join(unknown)}")
    if not methods:
        raise SystemExit("--methods 至少需要一个方法")
    return methods


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(values))


def _selected_task_ids(tasks_path: str, raw_task_ids: str | None) -> list[str]:
    all_ids = [task.task_id for task in load_tasks(tasks_path)]
    selected = _parse_csv(raw_task_ids)
    if not selected:
        return all_ids
    missing = [task_id for task_id in selected if task_id not in set(all_ids)]
    if missing:
        raise SystemExit(f"未找到指定任务：{', '.join(missing)}")
    selected_set = set(selected)
    return [task_id for task_id in all_ids if task_id in selected_set]


def _method_command(
    args,
    experiment_id: str,
    method: str,
    *,
    resume: bool,
    sample_key: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "experiments.runners.run_experiment",
        "--tasks", args.tasks,
        "--repeats", str(args.repeats),
        "--method", method,
        "--max-turns", str(args.max_turns),
        "--max-output-tokens", str(args.max_output_tokens),
        "--window-steps", str(args.window_steps),
        "--seed", str(args.seed),
        "--temperature", str(args.temperature),
        "--out", args.out,
        "--experiment-id", experiment_id,
        "--input-cost-per-million", str(args.input_cost_per_million),
        "--output-cost-per-million", str(args.output_cost_per_million),
        "--compressor-cost-per-million", str(args.compressor_cost_per_million),
        "--api-max-retries", str(args.api_max_retries),
        "--api-retry-base-delay", str(args.api_retry_base_delay),
        "--api-retry-max-delay", str(args.api_retry_max_delay),
        "--api-timeout", str(args.api_timeout),
    ]
    if resume:
        command.append("--resume")
    if sample_key:
        command.extend(("--sample-keys", sample_key))
    if args.mock:
        command.append("--mock")
    if args.task_ids:
        command.extend(("--task-ids", args.task_ids))
    if not args.no_fault_injection:
        command.append("--inject-faults")
    if args.model:
        command.extend(("--model", args.model))
    if args.base_url:
        command.extend(("--base-url", args.base_url))
    if args.context_soft_limit is not None:
        command.extend(("--context-soft-limit", str(args.context_soft_limit)))
    if args.context_hard_limit is not None:
        command.extend(("--context-hard-limit", str(args.context_hard_limit)))
    if args.context_target is not None:
        command.extend(("--context-target", str(args.context_target)))
    if args.archive_db:
        command.extend(("--archive-db", args.archive_db))
    return command


def _suite_resume_compatibility_errors(existing: dict, desired: dict) -> list[str]:
    keys = (
        "experiment_id",
        "suite",
        "baseline",
        "tasks",
        "selected_task_ids",
        "max_turns",
        "max_output_tokens",
        "window_steps",
        "temperature",
        "model",
        "base_url",
        "context_budget",
        "seed",
        "mock",
        "fault_injection_enabled",
        "execution_order",
    )
    errors = [
        f"{key}: 已有 {existing.get(key)!r}，当前 {desired.get(key)!r}"
        for key in keys
        if key in existing and existing.get(key) != desired.get(key)
    ]
    previous_repeats = int(existing.get("repeats", 1))
    if int(desired.get("repeats", 1)) < previous_repeats:
        errors.append(
            f"repeats: 已有 {previous_repeats}，当前不能缩减为 {desired.get('repeats')}"
        )
    return errors


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
