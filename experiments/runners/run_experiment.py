"""可复现的长程 Agent 实验入口。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_demo import (
    FaultInjectingLLM,
    KnowledgeBase,
    MockLLM,
    OpenAICompatClient,
    ReActAgent,
    load_tasks,
    resolve_method,
)
from agent_demo.logger import record_summary, write_run
from agent_demo.utils import tokenizer_name
from context_pruner import ContextBudget, SQLiteArchiveStore, __version__


METHODS = (
    "none",
    "window",
    "summary",
    "pruner",
    "pruner_v0",
    "pruner_v1",
    "ablation_a",
    "ablation_b",
    "ablation_c",
    "ablation_d",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="长程 Agent 可复现实验入口")
    parser.add_argument("--tasks", default="tasks/collection", help="任务目录或 JSON 文件")
    parser.add_argument(
        "--task-ids",
        default=None,
        help="只运行指定任务，多个 task_id 用英文逗号分隔",
    )
    parser.add_argument(
        "--sample-keys",
        default=None,
        help="只执行指定 task_id:rNN 样本，多个键用英文逗号分隔；用于套件交错调度",
    )
    parser.add_argument("--repeats", type=int, default=1, help="每个任务重复次数")
    parser.add_argument("--method", choices=METHODS, default="none")
    parser.add_argument("--mock", action="store_true", help="使用确定性 MockLLM")
    parser.add_argument("--inject-faults", action="store_true", help="启用任务中声明的故障注入")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：校验已有配置，跳过产物完整的 task/run，仅执行缺失项",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=512,
        help="单次模型回复 token 上限，防止模型续写伪造工具轨迹",
    )
    parser.add_argument("--window-steps", type=int, default=4)
    parser.add_argument("--context-soft-limit", type=int, default=None)
    parser.add_argument("--context-hard-limit", type=int, default=None)
    parser.add_argument("--context-target", type=int, default=None)
    parser.add_argument(
        "--archive-db",
        default=None,
        help="可选 SQLite 归档文件；每个任务运行使用独立 namespace",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--out", default="runs", help="实验输出根目录")
    parser.add_argument("--experiment-id", default=None, help="同一实验套件共享的唯一 ID")
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
    if args.max_turns <= 0:
        raise SystemExit("--max-turns 必须大于 0")
    if args.max_output_tokens < 64:
        raise SystemExit("--max-output-tokens 不能小于 64")
    if args.api_max_retries < 0:
        raise SystemExit("--api-max-retries 不能小于 0")
    if args.api_retry_base_delay < 0 or args.api_retry_max_delay < 0:
        raise SystemExit("API 重试等待时间不能为负数")
    if args.api_retry_max_delay < args.api_retry_base_delay:
        raise SystemExit("--api-retry-max-delay 不能小于 --api-retry-base-delay")
    if args.api_timeout <= 0:
        raise SystemExit("--api-timeout 必须大于 0")
    context_budget = _resolve_context_budget(args)

    all_tasks = load_tasks(args.tasks)
    task_positions = {task.task_id: index for index, task in enumerate(all_tasks)}
    tasks = list(all_tasks)
    selected_task_ids = _parse_task_ids(args.task_ids)
    if selected_task_ids:
        available = {task.task_id for task in tasks}
        missing = [task_id for task_id in selected_task_ids if task_id not in available]
        if missing:
            raise SystemExit(f"未找到指定任务：{', '.join(missing)}")
        selected = set(selected_task_ids)
        tasks = [task for task in tasks if task.task_id in selected]
    if not tasks:
        raise SystemExit(f"未找到任务：{args.tasks}")
    selected_sample_keys = _parse_sample_keys(args.sample_keys)
    if selected_sample_keys:
        valid_task_ids = {task.task_id for task in tasks}
        invalid = [
            key
            for key in selected_sample_keys
            if key[0] not in valid_task_ids or key[1] < 0 or key[1] >= args.repeats
        ]
        if invalid:
            rendered = ", ".join(f"{task_id}:r{rep:02d}" for task_id, rep in invalid)
            raise SystemExit(f"无效 --sample-keys：{rendered}")

    experiment_id = args.experiment_id or datetime.now().strftime("exp-%Y%m%d-%H%M%S")
    if Path(experiment_id).name != experiment_id or experiment_id in {".", ".."}:
        raise SystemExit("--experiment-id 只能是单个安全目录名")
    experiment_root = Path(args.out) / experiment_id
    out_dir = experiment_root / args.method
    manifest_path = out_dir / "run_manifest.json"
    existing_manifest = _read_json(manifest_path) if manifest_path.exists() else None
    if out_dir.exists() and any(out_dir.glob("*.summary.json")) and not args.resume:
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().astimezone().isoformat()
    started_clock = time.perf_counter()
    pricing = {
        "input_per_million": args.input_cost_per_million,
        "output_per_million": args.output_cost_per_million,
        "compressor_per_million": args.compressor_cost_per_million,
    }
    resolved_model = args.model or os.getenv("OPENAI_MODEL") or ("mock" if args.mock else "deepseek-v4-flash")
    resolved_base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or (
        None if args.mock else "https://api.deepseek.com"
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "status": "running",
        "started_at": started_at,
        "mock": args.mock,
        "fault_injection_enabled": args.inject_faults,
        "agent_model": resolved_model,
        "compressor_model": None,
        "embedding_model": None,
        "base_url": resolved_base_url,
        "temperature": args.temperature,
        "max_turns": args.max_turns,
        "max_output_tokens": args.max_output_tokens,
        "api_retry": {
            "max_retries": args.api_max_retries,
            "base_delay_seconds": args.api_retry_base_delay,
            "max_delay_seconds": args.api_retry_max_delay,
            "timeout_seconds": args.api_timeout,
        },
        "window_steps": args.window_steps,
        "context_budget": (
            {
                "soft_limit_tokens": context_budget.soft_limit_tokens,
                "hard_limit_tokens": context_budget.hard_limit_tokens,
                "target_tokens": context_budget.target_tokens,
            }
            if context_budget
            else None
        ),
        "archive_backend": "sqlite" if args.archive_db else "memory",
        "archive_db": str(Path(args.archive_db).resolve()) if args.archive_db else None,
        "seed": args.seed,
        "repeats": args.repeats,
        "method": args.method,
        "method_config": resolve_method(args.method),
        "pricing": pricing,
        "tokenizer": tokenizer_name(),
        "task_source": str(Path(args.tasks).resolve()),
        "selected_task_ids": selected_task_ids,
        "task_set_hash": _task_set_hash(args.tasks),
        "task_count": len(tasks),
        "last_sample_keys": [
            f"{task_id}:r{rep:02d}" for task_id, rep in selected_sample_keys
        ],
        "context_pruner_version": __version__,
        "python_version": platform.python_version(),
        "git_commit": _git_commit(),
    }
    if args.resume and existing_manifest is not None:
        compatibility_errors = _resume_compatibility_errors(existing_manifest, manifest)
        if compatibility_errors:
            raise SystemExit(
                "断点续跑配置与已有实验不一致：" + "；".join(compatibility_errors)
            )
        previous_failure = {
            key: existing_manifest[key]
            for key in ("status", "finished_at", "error_type", "error")
            if key in existing_manifest
        }
        manifest["started_at"] = existing_manifest.get("started_at", started_at)
        manifest["resume_count"] = int(existing_manifest.get("resume_count", 0)) + 1
        manifest["resumed_at"] = started_at
        if previous_failure:
            manifest["previous_attempt"] = previous_failure
    _write_json(manifest_path, manifest)

    summaries: list[dict] = []
    failures: list[dict] = []
    skipped_samples = 0
    executed_samples = 0
    api_retry_count = 0
    print(f"{'task_id':<24}{'run':<6}{'status':<9}{'success':<9}{'turns':<7}{'tok_in':<10}{'peak':<9}{'net_save':<10}")
    try:
        for task in tasks:
            for rep in range(args.repeats):
                if selected_sample_keys and (task.task_id, rep) not in selected_sample_keys:
                    continue
                run_id = f"r{rep:02d}"
                run_seed = args.seed + task_positions[task.task_id] * 10_000 + rep
                existing_summary = (
                    _load_completed_summary(
                        out_dir,
                        task.task_id,
                        run_id,
                        args.method,
                        experiment_id,
                    )
                    if args.resume
                    else None
                )
                if existing_summary is not None:
                    summaries.append(existing_summary)
                    if not existing_summary.get("success", False):
                        failures.append(_failure_row(existing_summary))
                    skipped_samples += 1
                    print(
                        f"{task.task_id:<24}{run_id:<6}{'skipped':<9}"
                        f"{str(bool(existing_summary.get('success'))):<9}"
                        f"{int(existing_summary.get('num_turns', 0)):<7}"
                        f"{int(existing_summary.get('tokens_in_total', 0)):<10}"
                        f"{int(existing_summary.get('peak_context_tokens', 0)):<9}"
                        f"{int(existing_summary.get('net_input_tokens_saved', 0)):<10}"
                    )
                    continue

                random.seed(run_seed)
                env = KnowledgeBase(task.docs)
                if args.mock:
                    client = MockLLM(env)
                else:
                    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
                        raise SystemExit(
                            "未配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY；"
                            "调试可加 --mock，正式实验请先设置环境变量。"
                        )
                    client = OpenAICompatClient(
                        model=args.model,
                        base_url=args.base_url,
                        max_output_tokens=args.max_output_tokens,
                        max_retries=args.api_max_retries,
                        retry_base_delay=args.api_retry_base_delay,
                        retry_max_delay=args.api_retry_max_delay,
                        timeout=args.api_timeout,
                    )
                if args.inject_faults and task.fault_injection:
                    client = FaultInjectingLLM(client, task.fault_injection)

                archive_store = None
                if args.archive_db:
                    namespace = f"{experiment_id}:{args.method}:{task.task_id}:{run_id}"
                    archive_store = SQLiteArchiveStore(args.archive_db, namespace=namespace)
                try:
                    agent = ReActAgent(
                        client,
                        env,
                        max_turns=args.max_turns,
                        method=args.method,
                        window_steps=args.window_steps,
                        pricing=pricing,
                        temperature=args.temperature,
                        context_budget=context_budget,
                        archive_store=archive_store,
                    )
                    try:
                        record = agent.run(task)
                    except BaseException:
                        # 即使全部重试后仍失败，也把已发生的 API 重试次数写入失败 manifest。
                        api_retry_count += _client_retry_count(client)
                        raise
                finally:
                    if archive_store is not None:
                        archive_store.close()
                result_metadata = {
                    "run_id": run_id,
                    "run_seed": run_seed,
                    "experiment_id": experiment_id,
                    "api_retry_count": _client_retry_count(client),
                }
                write_run(
                    out_dir,
                    record,
                    run_id=run_id,
                    summary_metadata=result_metadata,
                )
                summary = {
                    **record_summary(record),
                    **result_metadata,
                }
                summaries.append(summary)
                executed_samples += 1
                api_retry_count += int(result_metadata["api_retry_count"])
                if not record.success:
                    failures.append(_failure_row(summary))
                print(
                    f"{task.task_id:<24}{run_id:<6}{'executed':<9}{str(record.success):<9}"
                    f"{len(record.turns):<7}{record.tokens_in_total:<10}"
                    f"{record.peak_context_tokens:<9}{record.net_input_tokens_saved:<10}"
                )

        # 交错调度会分多次补齐同一方法；每次都从原子样本产物重建累计索引，
        # 避免 task_results.jsonl 被最后一个局部调用覆盖。
        summaries = _collect_completed_summaries(
            out_dir,
            tasks,
            args.repeats,
            args.method,
            experiment_id,
        )
        failures = [
            _failure_row(summary)
            for summary in summaries
            if not summary.get("success", False)
        ]
        expected_sample_count = len(tasks) * args.repeats
        run_status = "completed" if len(summaries) == expected_sample_count else "partial"
        _write_jsonl(out_dir / "task_results.jsonl", summaries)
        _write_jsonl(out_dir / "failures.jsonl", failures)
        manifest.update(
            {
                "status": run_status,
                "finished_at": datetime.now().astimezone().isoformat(),
                "duration_seconds": round(time.perf_counter() - started_clock, 6),
                "sample_count": len(summaries),
                "expected_sample_count": expected_sample_count,
                "success_count": sum(bool(row["success"]) for row in summaries),
                "failure_count": len(failures),
                "skipped_sample_count": skipped_samples,
                "executed_sample_count": executed_samples,
                "api_retry_count": sum(
                    int(row.get("api_retry_count", 0)) for row in summaries
                ),
            }
        )
        _write_json(manifest_path, manifest)
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
        manifest["skipped_sample_count"] = skipped_samples
        manifest["executed_sample_count"] = executed_samples
        manifest["api_retry_count"] = api_retry_count
        _write_json(manifest_path, manifest)
        raise

    print(f"\n记录已写入：{out_dir}")
    return out_dir


def _task_set_hash(path: str | Path) -> str:
    root = Path(path)
    files = [root] if root.is_file() else sorted(root.glob("*.json"))
    digest = hashlib.sha256()
    for file_path in files:
        digest.update(file_path.name.encode("utf-8"))
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _parse_task_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    task_ids = [item.strip() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(task_ids))


def _parse_sample_keys(raw: str | None) -> list[tuple[str, int]]:
    if not raw:
        return []
    parsed: list[tuple[str, int]] = []
    for value in (item.strip() for item in raw.split(",") if item.strip()):
        task_id, separator, run_id = value.rpartition(":")
        if not separator or not task_id or not run_id.startswith("r"):
            raise SystemExit(f"无效样本键 {value!r}，应为 task_id:rNN")
        try:
            rep = int(run_id[1:])
        except ValueError as error:
            raise SystemExit(f"无效样本键 {value!r}，应为 task_id:rNN") from error
        parsed.append((task_id, rep))
    return list(dict.fromkeys(parsed))


def _collect_completed_summaries(
    out_dir: Path,
    tasks: list,
    repeats: int,
    method: str,
    experiment_id: str,
) -> list[dict]:
    summaries: list[dict] = []
    for task in tasks:
        for rep in range(repeats):
            summary = _load_completed_summary(
                out_dir,
                task.task_id,
                f"r{rep:02d}",
                method,
                experiment_id,
            )
            if summary is not None:
                summaries.append(summary)
    return summaries


def _load_completed_summary(
    out_dir: Path,
    task_id: str,
    run_id: str,
    method: str,
    experiment_id: str,
) -> dict | None:
    """读取可安全跳过的完整样本；残缺产物必须显式处理，不能静默覆盖。"""
    prefix = f"{task_id}.{run_id}"
    summary_path = out_dir / f"{prefix}.summary.json"
    turns_path = out_dir / f"{prefix}.turns.jsonl"
    events_path = out_dir / f"{prefix}.events.jsonl"
    paths = (summary_path, turns_path, events_path)
    existing = [path.exists() for path in paths]
    if not any(existing):
        return None
    if not all(existing):
        missing = [path.name for path, present in zip(paths, existing) if not present]
        raise SystemExit(
            f"发现残缺样本 {prefix}，缺少 {', '.join(missing)}；"
            "请先备份或移走该样本的残留文件后再 --resume"
        )
    try:
        summary = _read_json(summary_path)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"无法读取已有样本 {summary_path}: {error}") from error
    expected = {
        "task_id": task_id,
        "run_id": run_id,
        "method": method,
        "experiment_id": experiment_id,
    }
    mismatches = [
        f"{key}={summary.get(key)!r}（期望 {value!r}）"
        for key, value in expected.items()
        if summary.get(key) != value
    ]
    if mismatches:
        raise SystemExit(f"已有样本 {summary_path} 元数据不一致：{'；'.join(mismatches)}")
    return summary


def _resume_compatibility_errors(existing: dict, desired: dict) -> list[str]:
    keys = (
        "experiment_id",
        "mock",
        "fault_injection_enabled",
        "agent_model",
        "base_url",
        "temperature",
        "max_turns",
        "max_output_tokens",
        "window_steps",
        "context_budget",
        "archive_backend",
        "seed",
        "method",
        "method_config",
        "task_set_hash",
        "selected_task_ids",
        "task_count",
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


def _failure_row(summary: dict) -> dict:
    return {
        "task_id": summary.get("task_id"),
        "run_id": summary.get("run_id"),
        "partial_score": summary.get("partial_score", 0.0),
        "final_answer": summary.get("final_answer", ""),
    }


def _client_retry_count(client) -> int:
    current = client
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if hasattr(current, "retry_count"):
            return int(getattr(current, "retry_count", 0))
        current = getattr(current, "inner", None)
    return 0


def _resolve_context_budget(args) -> ContextBudget | None:
    supplied = (args.context_soft_limit, args.context_hard_limit, args.context_target)
    if all(value is None for value in supplied):
        return None
    if args.context_hard_limit is None:
        raise SystemExit("设置上下文预算时必须提供 --context-hard-limit")
    hard = args.context_hard_limit
    soft = args.context_soft_limit if args.context_soft_limit is not None else int(hard * 0.8)
    target = args.context_target if args.context_target is not None else soft
    try:
        return ContextBudget(soft, hard, target)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
