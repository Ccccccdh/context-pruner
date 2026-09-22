"""阶段四真实工作区、代码修改和测试工具的插件开关配对实验。"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_demo import (
    LangGraphReActAgent,
    OpenAICompatClient,
    ScriptedWorkspaceLLM,
    WorkspaceEnvironment,
    load_tasks,
)
from agent_demo.agent import DEFAULT_SYSTEM_PROMPT
from context_pruner import ContextBudget, __version__
from metrics.summary import _write_csv, build_report, discover


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="阶段四真实工作区配对实验")
    parser.add_argument("--phase", choices=("stage4a", "stage4b"), default="stage4a")
    parser.add_argument("--tasks", default="tasks/stage4a/tasks.json")
    parser.add_argument("--task-ids", default=None)
    parser.add_argument("--methods", default="none,pruner_v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--context-soft-limit", type=int, default=1_600)
    parser.add_argument("--context-hard-limit", type=int, default=2_100)
    parser.add_argument("--context-target", type=int, default=1_250)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-output-tokens", type=int, default=700)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", default="runs/stage4a")
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
    tasks = [task for task in load_tasks(args.tasks) if not selected or task.task_id in selected]
    missing = selected - {task.task_id for task in tasks}
    if missing:
        raise SystemExit(f"未找到指定任务：{', '.join(sorted(missing))}")
    if not tasks:
        raise SystemExit("没有可运行任务")
    for task in tasks:
        if not task.workspace:
            raise SystemExit(f"阶段四任务缺少 workspace：{task.task_id}")

    experiment_id = args.experiment_id or datetime.now().strftime(
        f"{args.phase}-%Y%m%d-%H%M%S"
    )
    if Path(experiment_id).name != experiment_id or experiment_id in {".", ".."}:
        raise SystemExit("--experiment-id 只能是安全目录名")
    root = Path(args.out) / experiment_id
    manifest_path = root / "suite_manifest.json"
    existing = _read_json(manifest_path) if manifest_path.exists() else None
    if existing and not args.resume:
        raise SystemExit(f"实验目录已存在，拒绝覆盖：{root}")
    root.mkdir(parents=True, exist_ok=True)

    budget = ContextBudget(
        args.context_soft_limit,
        args.context_hard_limit,
        args.context_target,
    )
    task_root = Path(args.tasks).resolve()
    manifest = {
        "schema_version": 1,
        "phase": args.phase,
        "status": "running",
        "framework": "langgraph-real-workspace",
        "experiment_id": experiment_id,
        "started_at": datetime.now().astimezone().isoformat(),
        "methods": methods,
        "tasks": str(task_root),
        "selected_task_ids": sorted(selected),
        "task_set_hash": _hash_task_bundle(task_root, tasks),
        "task_count": len(tasks),
        "repeats": args.repeats,
        "model": args.model or ("scripted-workspace-mock" if args.mock else "deepseek-v4-flash"),
        "base_url": args.base_url or (None if args.mock else "https://api.deepseek.com"),
        "temperature": args.temperature,
        "max_turns": args.max_turns,
        "context_budget": {
            "soft_limit_tokens": budget.soft_limit_tokens,
            "hard_limit_tokens": budget.hard_limit_tokens,
            "target_tokens": budget.target_tokens,
        },
        "execution_order": "pair-interleaved-isolated-workspaces",
        "context_pruner_version": __version__,
        "python_version": platform.python_version(),
    }
    if args.resume and existing:
        errors = _resume_errors(existing, manifest)
        if errors:
            raise SystemExit("断点续跑配置不一致：" + "；".join(errors))
        manifest["started_at"] = existing.get("started_at", manifest["started_at"])
        manifest["resume_count"] = int(existing.get("resume_count", 0)) + 1
        manifest["resumed_at"] = datetime.now().astimezone().isoformat()
    _write_json(manifest_path, manifest)

    try:
        pairs = [(task, repeat) for task in tasks for repeat in range(args.repeats)]
        for sample_index, (task, repeat) in enumerate(pairs):
            offset = sample_index % len(methods)
            ordered = [*methods[offset:], *methods[:offset]]
            run_id = f"r{repeat:02d}"
            print(f"\n===== {task.task_id}:{run_id}；方法顺序：{','.join(ordered)} =====")
            for method in ordered:
                method_dir = root / method
                method_dir.mkdir(parents=True, exist_ok=True)
                paths = _artifact_paths(method_dir, task.task_id, run_id)
                if args.resume and all(path.exists() for path in paths.values()):
                    print(f"{method:<10} skipped")
                    continue

                workspace_dir = root / "workspaces" / method / f"{task.task_id}.{run_id}"
                env = WorkspaceEnvironment.materialize(task, workspace_dir, replace=True)
                client = (
                    ScriptedWorkspaceLLM(task)
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
                    system_prompt=(
                        DEFAULT_SYSTEM_PROMPT
                        + "\n工作区任务的 final_answer 必须简洁，不超过 500 个中文字符；"
                        "只报告结论、涉及文件和测试状态，不要复述完整测试代码。"
                        "replace_text 成功结果中的 new 文本已证明写入，不要仅为确认写入而回读。"
                        "多文件修复先完成所有已有证据支持的修改，再集中运行测试；"
                        "仅当测试失败且继续修改后才复测。"
                    ),
                ).run(task)
                _write_json(paths["summary"], result.summary(experiment_id, run_id))
                _write_json(paths["state"], result.plugin_state)
                _write_json(paths["messages"], result.messages)
                _write_json(paths["workspace_audit"], env.audit())
                _write_jsonl(
                    paths["turns"],
                    [
                        {
                            "step": turn.step,
                            "tokens_in": turn.tokens_in,
                            "full_tokens_in": turn.full_tokens_in,
                            "tokens_out": turn.tokens_out,
                            "latency": turn.latency,
                            "tool_ledger_tokens": turn.tool_ledger_tokens,
                            "ephemeral_context_tokens": turn.ephemeral_context_tokens,
                        }
                        for turn in result.turns
                    ],
                )
                print(
                    f"{method:<10} success={result.success} turns={result.num_turns} "
                    f"tokens={result.tokens_in_total} peak={result.peak_context_tokens} "
                    f"changed={result.environment_metrics.get('changed_file_count', 0)} "
                    f"tests={result.environment_metrics.get('tests_passed', False)}"
                )

        samples = discover(root)
        report = build_report(samples, baseline="none")
        _write_json(root / "report.json", report)
        _write_csv(root / "method_summary.csv", report["methods"])
        _write_csv(root / "paired_comparison.csv", report["paired_to_baseline"])
        validation = _validation_summary(root, methods)
        _write_json(root / "workspace_validation.json", validation)
        stage4b_diagnostics = None
        if args.phase == "stage4b":
            stage4b_diagnostics = _stage4b_diagnostics(root, report)
            _write_json(root / "stage4b_diagnostics.json", stage4b_diagnostics)
        manifest.update(
            {
                "status": "completed",
                "finished_at": datetime.now().astimezone().isoformat(),
                "sample_count": len(samples),
                "workspace_validation": validation,
                "stage4b_diagnostics": stage4b_diagnostics,
            }
        )
        print(f"\n{args.phase} 报告已生成：{root}")
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
        _write_json(manifest_path, manifest)
    return root


def _artifact_paths(method_dir: Path, task_id: str, run_id: str) -> dict[str, Path]:
    prefix = f"{task_id}.{run_id}"
    return {
        "summary": method_dir / f"{prefix}.summary.json",
        "turns": method_dir / f"{prefix}.turns.jsonl",
        "state": method_dir / f"{prefix}.plugin-state.json",
        "messages": method_dir / f"{prefix}.messages.json",
        "workspace_audit": method_dir / f"{prefix}.workspace-audit.json",
    }


def _hash_task_bundle(task_path: Path, tasks: list[Any]) -> str:
    digest = hashlib.sha256()
    task_files = [task_path] if task_path.is_file() else sorted(task_path.glob("*.json"))
    source_files: set[Path] = set(task_files)
    for task in tasks:
        source_dir = Path(str(task.metadata.get("_task_source_dir", ".")))
        workspace = (source_dir / task.workspace).resolve()
        source_files.update(path for path in workspace.rglob("*") if path.is_file())
    for path in sorted(source_files, key=lambda item: str(item)):
        digest.update(str(path).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _resume_errors(existing: dict[str, Any], current: dict[str, Any]) -> list[str]:
    keys = (
        "phase",
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


def _validation_summary(root: Path, methods: list[str]) -> dict[str, Any]:
    by_method: dict[str, Any] = {}
    for method in methods:
        rows = [_read_json(path) for path in sorted((root / method).glob("*.summary.json"))]
        passed = sum(
            bool(row.get("environment_validation", {}).get("success")) for row in rows
        )
        by_method[method] = {
            "samples": len(rows),
            "passed": passed,
            "pass_rate": passed / len(rows) if rows else 0.0,
            "tests_passed": sum(
                bool(row.get("environment_metrics", {}).get("tests_passed")) for row in rows
            ),
            "changed_files": sum(
                int(row.get("environment_metrics", {}).get("changed_file_count", 0)) for row in rows
            ),
        }
    return {"methods": by_method}


def _stage4b_diagnostics(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Build state/convergence gates and replay every persisted checkpoint.

    The checkpoint replay is a structural counterfactual audit: it verifies token
    reduction and protected-source retention without making a second LLM call.
    Behavioral quality remains the responsibility of the paired Agent runs.
    """
    rows: list[dict[str, Any]] = []
    for state_path in sorted((root / "pruner_v1").glob("*.plugin-state.json")):
        state = _read_json(state_path)
        entries = (
            dict(state.get("manager") or {})
            .get("checkpoint_store", {})
            .get("entries", [])
        )
        sample_name = state_path.name.removesuffix(".plugin-state.json")
        for checkpoint in entries:
            baseline_chunks = list(checkpoint.get("baseline_chunks") or [])
            treated_chunks = list(checkpoint.get("treated_chunks") or [])
            protected_ids = {
                str(source_id)
                for chunk in baseline_chunks
                if chunk.get("ctype") in {"system", "task_state"}
                for source_id in chunk.get("source_event_ids", [])
            }
            treated_ids = {
                str(source_id)
                for chunk in treated_chunks
                for source_id in chunk.get("source_event_ids", [])
            }
            protected_recall = (
                len(protected_ids & treated_ids) / len(protected_ids)
                if protected_ids
                else 1.0
            )
            state_memories = [
                chunk
                for chunk in treated_chunks
                if dict(chunk.get("metadata") or {}).get("workspace_state_aware")
            ]
            baseline_tokens = int(checkpoint.get("baseline_tokens", 0))
            treated_tokens = int(checkpoint.get("treated_tokens", 0))
            row = {
                "sample": sample_name,
                "checkpoint_id": str(checkpoint.get("checkpoint_id", "")),
                "turn": int(checkpoint.get("turn", 0)),
                "baseline_tokens": baseline_tokens,
                "treated_tokens": treated_tokens,
                "token_savings": baseline_tokens - treated_tokens,
                "token_savings_rate": (
                    (baseline_tokens - treated_tokens) / max(1, baseline_tokens)
                ),
                "protected_source_recall": protected_recall,
                "workspace_state_memory_count": len(state_memories),
                "stale_evidence_filtered_count": max(
                    (
                        int(
                            dict(chunk.get("metadata") or {}).get(
                                "stale_evidence_filtered_count", 0
                            )
                        )
                        for chunk in state_memories
                    ),
                    default=0,
                ),
                "strict_nonexpansion": treated_tokens <= baseline_tokens,
                # A tiny derived-memory header can exceed the raw baseline by a
                # handful of tokens before later checkpoints become smaller.
                # Structural safety is provenance retention plus bounded framing
                # overhead; strict token non-expansion is reported separately.
                "structurally_accepted": (
                    protected_recall >= 1.0
                    and treated_tokens
                    <= baseline_tokens + max(16, int(baseline_tokens * 0.01))
                ),
            }
            rows.append(row)
    _write_csv(root / "checkpoint_replay.csv", rows)

    paired = list(report.get("paired_to_baseline") or [])
    paired_row = next(
        (row for row in paired if row.get("method") == "pruner_v1"), {}
    )
    method_rows = {
        str(row.get("method", "")): row
        for row in report.get("methods") or []
    }
    baseline_row = method_rows.get("none", {})
    treated_row = method_rows.get("pruner_v1", {})
    post_mutation_read_delta = float(
        treated_row.get("post_mutation_reads_mean", 0.0)
    ) - float(baseline_row.get("post_mutation_reads_mean", 0.0))
    intermediate_test_delta = float(
        treated_row.get("intermediate_tests_mean", 0.0)
    ) - float(baseline_row.get("intermediate_tests_mean", 0.0))
    turn_delta = float(treated_row.get("num_turns_mean", 0.0)) - float(
        baseline_row.get("num_turns_mean", 0.0)
    )
    summaries = [
        _read_json(path)
        for path in sorted((root / "pruner_v1").glob("*.summary.json"))
    ]
    post_completion = sum(
        int(row.get("post_completion_tool_call_count", 0)) for row in summaries
    )
    stale_filtered = sum(
        int(row.get("stale_evidence_filtered_count", 0)) for row in summaries
    )
    compression_budget_violations = sum(
        int(row.get("compression_budget_violation_count", row.get("budget_violation_count", 0)))
        for row in summaries
    )
    model_input_budget_violations = sum(
        int(row.get("model_input_budget_violation_count", 0)) for row in summaries
    )
    mutated_samples = sum(
        int(dict(row.get("environment_metrics") or {}).get("workspace_mutation_epoch", 0))
        > 0
        for row in summaries
    )
    quality_delta = float(paired_row.get("success_delta", 0.0))
    structural_acceptance = (
        sum(bool(row["structurally_accepted"]) for row in rows) / len(rows)
        if rows
        else 0.0
    )
    strict_nonexpansion = (
        sum(bool(row["strict_nonexpansion"]) for row in rows) / len(rows)
        if rows
        else 0.0
    )
    return {
        "schema_version": 1,
        "replay_kind": "offline_structural_counterfactual",
        "checkpoint_count": len(rows),
        "checkpoint_structural_acceptance_rate": structural_acceptance,
        "checkpoint_strict_nonexpansion_rate": strict_nonexpansion,
        "checkpoint_mean_token_savings_rate": (
            sum(float(row["token_savings_rate"]) for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "mutated_sample_count": mutated_samples,
        "stale_evidence_filtered_count": stale_filtered,
        "post_completion_tool_call_count": post_completion,
        "compression_budget_violation_count": compression_budget_violations,
        "model_input_budget_violation_count": model_input_budget_violations,
        "post_mutation_read_delta": post_mutation_read_delta,
        "intermediate_test_delta": intermediate_test_delta,
        "turn_delta": turn_delta,
        "paired_success_delta": quality_delta,
        "paired_input_savings_rate": float(
            paired_row.get("net_input_savings_rate_vs_baseline", 0.0)
        ),
        "paired_peak_context_savings_rate": float(
            paired_row.get("peak_context_savings_rate", 0.0)
        ),
        "gates": {
            "quality_non_inferior": quality_delta >= 0.0,
            "state_invalidation_observed": mutated_samples == 0 or stale_filtered > 0,
            "no_post_completion_tools": post_completion == 0,
            "compression_budget_feasible": compression_budget_violations == 0,
            "model_input_within_hard_limit": model_input_budget_violations == 0,
            "post_mutation_reads_not_increased": post_mutation_read_delta <= 0.0,
            "intermediate_tests_not_increased": intermediate_test_delta <= 0.0,
            "turns_not_increased": turn_delta <= 0.0,
            "all_checkpoints_structurally_accepted": bool(rows)
            and structural_acceptance >= 1.0,
        },
        "limitations": (
            "检查点回放只验证 token 与受保护来源保留；模型行为效果由 none/pruner_v1 "
            "配对运行验证，不能用结构审计替代真实模型对照。"
        ),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    main()
