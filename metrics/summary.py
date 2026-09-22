"""汇总单次实验，并生成方法统计、配对对照和恢复消融结果。"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from agent_demo.agent import _constraint_adherence, _evaluate, _partial_score
from agent_demo.env import load_tasks


@dataclass
class RunSample:
    experiment_id: str
    task_id: str
    scenario: str
    method: str
    run_id: str
    success: bool
    partial_score: float
    final_kirr: float
    constraint_adherence: float
    tool_correctness: float
    num_turns: int
    tokens_in_total: int
    tokens_out_total: int
    full_context_tokens_total: int
    compression_overhead_tokens: int
    gross_input_tokens_saved: int
    net_input_tokens_saved: int
    net_input_savings_rate: float
    peak_context_tokens: int
    total_latency: float
    p95_turn_latency: float
    estimated_total_cost: float
    recovery_count: int
    recovered_tokens: int
    recovery_precision: float
    recovery_recall: float
    recovery_amplification: float
    parse_error_count: int
    tool_error_count: int
    repeated_action_count: int
    post_sufficiency_tool_call_count: int
    max_turn_failure: bool
    budget_pressure_count: int
    budget_violation_count: int
    model_input_budget_violation_count: int
    context_hard_limit_tokens: int
    checkpoint_count: int
    sanitized_output_count: int
    sanitized_output_tokens: int
    tool_ledger_tokens_total: int
    ephemeral_context_tokens_total: int
    peak_ephemeral_context_tokens: int
    api_retry_count: int
    completion_rejection_count: int
    stale_evidence_filtered_count: int
    workspace_mutation_epoch: int
    post_mutation_read_count: int
    intermediate_test_count: int
    growth_slope: float
    final_answer: str


def discover(runs_root: str | Path) -> list[RunSample]:
    root = Path(runs_root)
    samples: list[RunSample] = []
    for summary_path in sorted(root.rglob("*.summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        stem = summary_path.name[: -len(".summary.json")]
        parts = stem.split(".")
        turns_path = summary_path.with_name(summary_path.name.replace("summary.json", "turns.jsonl"))
        samples.append(
            RunSample(
                experiment_id=str(data.get("experiment_id", _manifest_id(summary_path))),
                task_id=str(data.get("task_id", parts[0])),
                scenario=str(data.get("scenario", "")),
                method=str(data.get("method", summary_path.parent.name)),
                run_id=str(data.get("run_id", parts[1] if len(parts) > 1 else "r00")),
                success=bool(data.get("success", False)),
                partial_score=float(data.get("partial_score", 0.0)),
                final_kirr=float(data.get("final_kirr", 0.0)),
                constraint_adherence=float(data.get("constraint_adherence", 1.0)),
                tool_correctness=float(data.get("tool_correctness", 1.0)),
                num_turns=int(data.get("num_turns", 0)),
                tokens_in_total=int(data.get("tokens_in_total", 0)),
                tokens_out_total=int(data.get("tokens_out_total", 0)),
                full_context_tokens_total=int(data.get("full_context_tokens_total", data.get("tokens_in_total", 0))),
                compression_overhead_tokens=int(data.get("compression_overhead_tokens", 0)),
                gross_input_tokens_saved=int(data.get("gross_input_tokens_saved", 0)),
                net_input_tokens_saved=int(data.get("net_input_tokens_saved", 0)),
                net_input_savings_rate=float(data.get("net_input_savings_rate", 0.0)),
                peak_context_tokens=int(data.get("peak_context_tokens", 0)),
                total_latency=float(data.get("total_latency", 0.0)),
                p95_turn_latency=float(data.get("p95_turn_latency", 0.0)),
                estimated_total_cost=float(data.get("estimated_total_cost", 0.0)),
                recovery_count=int(data.get("recovery_count", 0)),
                recovered_tokens=int(data.get("recovered_tokens", 0)),
                recovery_precision=float(data.get("recovery_precision", 0.0)),
                recovery_recall=float(data.get("recovery_recall", 0.0)),
                recovery_amplification=float(data.get("recovery_amplification", 0.0)),
                parse_error_count=int(data.get("parse_error_count", 0)),
                tool_error_count=int(data.get("tool_error_count", 0)),
                repeated_action_count=int(data.get("repeated_action_count", 0)),
                post_sufficiency_tool_call_count=int(
                    data.get(
                        "post_sufficiency_tool_call_count",
                        _post_sufficiency_tool_calls(turns_path),
                    )
                ),
                max_turn_failure=bool(data.get("max_turn_failure", False)),
                budget_pressure_count=int(data.get("budget_pressure_count", 0)),
                budget_violation_count=int(data.get("budget_violation_count", 0)),
                model_input_budget_violation_count=int(
                    data.get("model_input_budget_violation_count", 0)
                ),
                context_hard_limit_tokens=int(
                    data.get("context_hard_limit_tokens", 0)
                ),
                checkpoint_count=int(data.get("checkpoint_count", 0)),
                sanitized_output_count=int(data.get("sanitized_output_count", 0)),
                sanitized_output_tokens=int(data.get("sanitized_output_tokens", 0)),
                tool_ledger_tokens_total=int(data.get("tool_ledger_tokens_total", 0)),
                ephemeral_context_tokens_total=int(
                    data.get(
                        "ephemeral_context_tokens_total",
                        data.get("tool_ledger_tokens_total", 0),
                    )
                ),
                peak_ephemeral_context_tokens=int(
                    data.get("peak_ephemeral_context_tokens", 0)
                ),
                api_retry_count=int(data.get("api_retry_count", 0)),
                completion_rejection_count=int(
                    data.get("completion_rejection_count", 0)
                ),
                stale_evidence_filtered_count=int(
                    data.get(
                        "stale_evidence_filtered_count",
                        dict(data.get("plugin_metrics") or {}).get(
                            "stale_evidence_filtered_count", 0
                        ),
                    )
                ),
                workspace_mutation_epoch=int(
                    data.get(
                        "workspace_mutation_epoch",
                        dict(data.get("environment_metrics") or {}).get(
                            "workspace_mutation_epoch", 0
                        ),
                    )
                ),
                post_mutation_read_count=int(
                    data.get(
                        "post_mutation_read_count",
                        dict(data.get("environment_metrics") or {}).get(
                            "post_mutation_read_count", 0
                        ),
                    )
                ),
                intermediate_test_count=int(
                    data.get(
                        "intermediate_test_count",
                        dict(data.get("environment_metrics") or {}).get(
                            "intermediate_test_count", 0
                        ),
                    )
                ),
                growth_slope=_growth_slope(turns_path),
                final_answer=str(data.get("final_answer", "")),
            )
        )
    return samples


def regrade_samples(samples: list[RunSample], tasks_path: str | Path) -> tuple[list[RunSample], list[dict]]:
    """使用当前评分器重算派生质量字段，返回新样本和透明审计记录。"""
    tasks = {task.task_id: task for task in load_tasks(tasks_path)}
    regraded: list[RunSample] = []
    audit: list[dict] = []
    for sample in samples:
        task = tasks.get(sample.task_id)
        if task is None:
            raise ValueError(f"评分任务集中缺少 {sample.task_id}")
        success = _evaluate(task, sample.final_answer)
        partial_score = _partial_score(task, sample.final_answer)
        constraint_adherence = _constraint_adherence(task, sample.final_answer)
        updated = replace(
            sample,
            success=success,
            partial_score=partial_score,
            constraint_adherence=constraint_adherence,
        )
        regraded.append(updated)
        if (
            success != sample.success
            or abs(partial_score - sample.partial_score) > 1e-9
            or abs(constraint_adherence - sample.constraint_adherence) > 1e-9
        ):
            audit.append(
                {
                    "task_id": sample.task_id,
                    "run_id": sample.run_id,
                    "method": sample.method,
                    "original_success": sample.success,
                    "corrected_success": success,
                    "original_partial_score": sample.partial_score,
                    "corrected_partial_score": partial_score,
                    "original_constraint_adherence": sample.constraint_adherence,
                    "corrected_constraint_adherence": constraint_adherence,
                    "reason": "current_evaluator_regrade",
                }
            )
    return regraded, audit


def summarize_methods(samples: list[RunSample]) -> list[dict]:
    groups: dict[str, list[RunSample]] = {}
    for sample in samples:
        groups.setdefault(sample.method, []).append(sample)
    rows = [_method_row(method, group) for method, group in groups.items()]
    return sorted(rows, key=lambda row: row["method"])


def summarize_scenarios(samples: list[RunSample]) -> list[dict]:
    groups: dict[tuple[str, str], list[RunSample]] = {}
    for sample in samples:
        groups.setdefault((sample.scenario, sample.method), []).append(sample)
    rows = []
    for (scenario, method), group in groups.items():
        row = _method_row(method, group)
        rows.append({"scenario": scenario, **row})
    return sorted(rows, key=lambda row: (row["scenario"], row["method"]))


def paired_comparisons(samples: list[RunSample], baseline: str = "none") -> list[dict]:
    by_key = {
        (sample.task_id, sample.run_id, sample.method): sample
        for sample in samples
    }
    methods = sorted({sample.method for sample in samples if sample.method != baseline})
    rows: list[dict] = []
    for method in methods:
        pairs = [
            (sample, by_key.get((sample.task_id, sample.run_id, baseline)))
            for sample in samples
            if sample.method == method
        ]
        pairs = [(treated, base) for treated, base in pairs if base is not None]
        if not pairs:
            continue
        net_rates = [
            (base.tokens_in_total - treated.tokens_in_total - treated.compression_overhead_tokens)
            / max(1, base.tokens_in_total)
            for treated, base in pairs
        ]
        peak_rates = [
            (base.peak_context_tokens - treated.peak_context_tokens)
            / max(1, base.peak_context_tokens)
            for treated, base in pairs
        ]
        success_delta = [float(treated.success) - float(base.success) for treated, base in pairs]
        partial_delta = [treated.partial_score - base.partial_score for treated, base in pairs]
        cost_rates = [
            (base.estimated_total_cost - treated.estimated_total_cost)
            / max(1e-12, base.estimated_total_cost)
            if base.estimated_total_cost > 0 else 0.0
            for treated, base in pairs
        ]
        net_ci = _bootstrap_mean_ci(net_rates)
        both_success_pairs = [
            (treated, base)
            for treated, base in pairs
            if treated.success and base.success
        ]
        both_success_rates = [
            (base.tokens_in_total - treated.tokens_in_total - treated.compression_overhead_tokens)
            / max(1, base.tokens_in_total)
            for treated, base in both_success_pairs
        ]
        both_success_ci = _bootstrap_mean_ci(both_success_rates)
        success_ci = _bootstrap_mean_ci(success_delta)
        rows.append(
            {
                "baseline": baseline,
                "method": method,
                "paired_n": len(pairs),
                "net_input_savings_rate_vs_baseline": _mean(net_rates),
                "net_input_savings_ci_low": net_ci[0],
                "net_input_savings_ci_high": net_ci[1],
                "net_input_savings_rate_median": _median(net_rates),
                "net_input_savings_win_rate": _mean(
                    [float(rate > 0.0) for rate in net_rates]
                ),
                "both_success_paired_n": len(both_success_pairs),
                "both_success_net_input_savings_rate": _mean(both_success_rates),
                "both_success_net_savings_ci_low": both_success_ci[0],
                "both_success_net_savings_ci_high": both_success_ci[1],
                "peak_context_savings_rate": _mean(peak_rates),
                "success_delta": _mean(success_delta),
                "success_delta_ci_low": success_ci[0],
                "success_delta_ci_high": success_ci[1],
                "partial_score_delta": _mean(partial_delta),
                "cost_savings_rate": _mean(cost_rates),
            }
        )
    return rows


def paired_comparisons_by_scenario(
    samples: list[RunSample],
    baseline: str = "none",
) -> list[dict]:
    """按场景分别计算配对差异，避免任务长度差异污染场景结论。"""
    rows: list[dict] = []
    scenarios = sorted({sample.scenario for sample in samples})
    for scenario in scenarios:
        group = [sample for sample in samples if sample.scenario == scenario]
        rows.extend(
            {"scenario": scenario, **row}
            for row in paired_comparisons(group, baseline)
        )
    return rows


def recovery_ablation(samples: list[RunSample]) -> dict | None:
    by_key = {
        (sample.task_id, sample.run_id, sample.method): sample
        for sample in samples
    }
    pairs = []
    for treated in samples:
        if treated.method != "ablation_d" or treated.scenario != "recovery":
            continue
        control = by_key.get((treated.task_id, treated.run_id, "ablation_c"))
        if control is not None:
            pairs.append((treated, control))
    if not pairs:
        return None
    return {
        "treatment": "ablation_d",
        "control": "ablation_c",
        "paired_n": len(pairs),
        "success_delta": _mean([float(t.success) - float(c.success) for t, c in pairs]),
        "partial_score_delta": _mean([t.partial_score - c.partial_score for t, c in pairs]),
        "input_token_delta": _mean([float(t.tokens_in_total - c.tokens_in_total) for t, c in pairs]),
        "recovery_recall_mean": _mean([t.recovery_recall for t, _ in pairs]),
        "recovery_precision_mean": _mean([t.recovery_precision for t, _ in pairs]),
        "recovered_tokens_mean": _mean([float(t.recovered_tokens) for t, _ in pairs]),
    }


def build_report(samples: list[RunSample], baseline: str = "none") -> dict:
    return {
        "sample_count": len(samples),
        "methods": summarize_methods(samples),
        "scenario_methods": summarize_scenarios(samples),
        "paired_to_baseline": paired_comparisons(samples, baseline),
        "scenario_paired_to_baseline": paired_comparisons_by_scenario(samples, baseline),
        "recovery_ablation": recovery_ablation(samples),
    }


def _method_row(method: str, group: list[RunSample]) -> dict:
    success_values = [float(sample.success) for sample in group]
    net_values = [sample.net_input_savings_rate for sample in group]
    success_ci = _bootstrap_mean_ci(success_values)
    net_ci = _bootstrap_mean_ci(net_values)
    return {
        "method": method,
        "n": len(group),
        "success_rate": _mean(success_values),
        "success_ci_low": success_ci[0],
        "success_ci_high": success_ci[1],
        "partial_score_mean": _mean([sample.partial_score for sample in group]),
        "final_kirr_mean": _mean([sample.final_kirr for sample in group]),
        "constraint_adherence_mean": _mean([sample.constraint_adherence for sample in group]),
        "tool_correctness_mean": _mean([sample.tool_correctness for sample in group]),
        "num_turns_mean": _mean([float(sample.num_turns) for sample in group]),
        "tokens_in_mean": _mean([float(sample.tokens_in_total) for sample in group]),
        "tokens_in_median": _median([float(sample.tokens_in_total) for sample in group]),
        "net_input_savings_rate_mean": _mean(net_values),
        "net_savings_ci_low": net_ci[0],
        "net_savings_ci_high": net_ci[1],
        "peak_context_mean": _mean([float(sample.peak_context_tokens) for sample in group]),
        "growth_slope_mean": _mean([sample.growth_slope for sample in group]),
        "latency_mean": _mean([sample.total_latency for sample in group]),
        "latency_p95": _percentile([sample.total_latency for sample in group], 0.95),
        "estimated_cost_mean": _mean([sample.estimated_total_cost for sample in group]),
        "recovery_count_mean": _mean([float(sample.recovery_count) for sample in group]),
        "recovery_precision_mean": _mean([sample.recovery_precision for sample in group]),
        "recovery_recall_mean": _mean([sample.recovery_recall for sample in group]),
        "recovery_amplification_mean": _mean([sample.recovery_amplification for sample in group]),
        "parse_errors_mean": _mean([float(sample.parse_error_count) for sample in group]),
        "tool_errors_mean": _mean([float(sample.tool_error_count) for sample in group]),
        "repeated_actions_mean": _mean([float(sample.repeated_action_count) for sample in group]),
        "post_sufficiency_tool_calls_mean": _mean(
            [float(sample.post_sufficiency_tool_call_count) for sample in group]
        ),
        "max_turn_failure_rate": _mean([float(sample.max_turn_failure) for sample in group]),
        "budget_pressure_count_mean": _mean(
            [float(sample.budget_pressure_count) for sample in group]
        ),
        "budget_violation_rate": _mean(
            [float(sample.budget_violation_count > 0) for sample in group]
        ),
        "model_input_budget_violation_rate": _mean(
            [float(sample.model_input_budget_violation_count > 0) for sample in group]
        ),
        "checkpoint_count_mean": _mean(
            [float(sample.checkpoint_count) for sample in group]
        ),
        "sanitized_output_count_mean": _mean(
            [float(sample.sanitized_output_count) for sample in group]
        ),
        "sanitized_output_tokens_mean": _mean(
            [float(sample.sanitized_output_tokens) for sample in group]
        ),
        "tool_ledger_tokens_mean": _mean(
            [float(sample.tool_ledger_tokens_total) for sample in group]
        ),
        "ephemeral_context_tokens_mean": _mean(
            [float(sample.ephemeral_context_tokens_total) for sample in group]
        ),
        "peak_ephemeral_context_tokens_mean": _mean(
            [float(sample.peak_ephemeral_context_tokens) for sample in group]
        ),
        "api_retry_count_mean": _mean(
            [float(sample.api_retry_count) for sample in group]
        ),
        "completion_rejection_count_mean": _mean(
            [float(sample.completion_rejection_count) for sample in group]
        ),
        "stale_evidence_filtered_count_mean": _mean(
            [float(sample.stale_evidence_filtered_count) for sample in group]
        ),
        "workspace_mutation_epoch_mean": _mean(
            [float(sample.workspace_mutation_epoch) for sample in group]
        ),
        "post_mutation_reads_mean": _mean(
            [float(sample.post_mutation_read_count) for sample in group]
        ),
        "intermediate_tests_mean": _mean(
            [float(sample.intermediate_test_count) for sample in group]
        ),
    }


def _manifest_id(summary_path: Path) -> str:
    manifest_path = summary_path.parent / "run_manifest.json"
    if not manifest_path.exists():
        return summary_path.parent.parent.name
    try:
        return str(json.loads(manifest_path.read_text(encoding="utf-8")).get("experiment_id", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def _growth_slope(turns_path: Path) -> float:
    if not turns_path.exists():
        return 0.0
    steps: list[float] = []
    tokens: list[float] = []
    try:
        for line in turns_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                data = json.loads(line)
                steps.append(float(data.get("step", 0)))
                tokens.append(float(data.get("tokens_in", 0)))
    except (OSError, json.JSONDecodeError):
        return 0.0
    if len(steps) < 2:
        return 0.0
    mean_x, mean_y = _mean(steps), _mean(tokens)
    denominator = sum((x - mean_x) ** 2 for x in steps)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(steps, tokens)) / denominator


def _post_sufficiency_tool_calls(turns_path: Path) -> int:
    """从旧版逐轮日志审计证据完整后的工具调用，不修改历史结果。"""
    if not turns_path.exists():
        return 0
    count = 0
    try:
        for line in turns_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                isinstance(row.get("action"), dict)
                and float(row.get("kirr") or 0.0) >= 1.0
            ):
                count += 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    return count


def _bootstrap_mean_ci(values: list[float], confidence: float = 0.95, rounds: int = 2000) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1 or len(set(values)) == 1:
        value = _mean(values)
        return (value, value)
    rng = random.Random(20260912)
    means = [
        _mean([values[rng.randrange(len(values))] for _ in values])
        for _ in range(rounds)
    ]
    alpha = (1 - confidence) / 2
    return (_percentile(means, alpha), _percentile(means, 1 - alpha))


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print_rows(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  无可用数据")
        return
    for row in rows:
        compact = ", ".join(f"{key}={_fmt(value)}" for key, value in row.items())
        print("  " + compact)


def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="汇总单个实验目录")
    parser.add_argument("runs", help="实验目录，例如 runs/stage2/<experiment-id>")
    parser.add_argument("--baseline", default="none")
    parser.add_argument("--out", default=None, help="方法汇总 CSV")
    parser.add_argument("--paired-out", default=None, help="配对比较 CSV")
    parser.add_argument("--json", default=None, help="完整报告 JSON")
    parser.add_argument("--tasks", default=None, help="可选：使用当前评分器和该任务集重算质量")
    parser.add_argument("--audit-out", default=None, help="重评分差异审计 JSON")
    args = parser.parse_args()

    samples = discover(args.runs)
    if not samples:
        raise SystemExit(f"未找到 summary.json：{args.runs}")
    audit: list[dict] = []
    if args.tasks:
        samples, audit = regrade_samples(samples, args.tasks)
    report = build_report(samples, baseline=args.baseline)
    if args.tasks:
        report["evaluation"] = {
            "mode": "current_evaluator_regrade",
            "tasks": str(Path(args.tasks).resolve()),
            "changed_sample_count": len(audit),
        }
    _print_rows("方法汇总", report["methods"])
    _print_rows("分场景汇总", report["scenario_methods"])
    _print_rows("相对基线的配对比较", report["paired_to_baseline"])
    if report["recovery_ablation"]:
        _print_rows("恢复模块消融", [report["recovery_ablation"]])

    if args.out:
        _write_csv(Path(args.out), report["methods"])
    if args.paired_out:
        _write_csv(Path(args.paired_out), report["paired_to_baseline"])
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.audit_out:
        path = Path(args.audit_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
