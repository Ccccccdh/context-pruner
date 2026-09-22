"""Offline paired validation for the OpenAI Agents input-filter adapter."""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter
from context_pruner.types import estimate_tokens


@dataclass(frozen=True)
class MockCase:
    scenario: str
    task_state: str
    instructions: str
    input_items: list[Any]
    expected_constraint: str
    protected_items: list[Any]


def build_case(scenario: str, repeat: int) -> MockCase:
    codename = f"Aurora-17-R{repeat}"
    history: list[Any] = [
        {
            "role": "system",
            "content": f"Hard constraint: project codename {codename} must remain available.",
        },
        {"role": "user", "content": "Analyze the accumulated project evidence."},
    ]
    for index in range(12 + repeat):
        history.extend(
            [
                {
                    "role": "assistant",
                    "content": f"background analysis {index}: " + "routine detail " * 28,
                },
                {
                    "role": "user",
                    "content": f"observation {index}: " + "low value history " * 22,
                },
            ]
        )

    protected: list[Any] = []
    if scenario == "single_tool_group":
        protected = [
            {"type": "reasoning", "id": f"rs_single_{repeat}", "summary": []},
            {
                "type": "function_call",
                "name": "lookup_project",
                "call_id": f"call_single_{repeat}",
                "arguments": json.dumps({"codename": codename}),
            },
            {
                "type": "function_call_output",
                "call_id": f"call_single_{repeat}",
                "output": json.dumps({"codename": codename, "status": "verified"}),
            },
            {
                "type": "message",
                "role": "assistant",
                "content": f"Verified {codename}.",
            },
        ]
    elif scenario == "parallel_tool_group":
        protected = [
            {"type": "reasoning", "id": f"rs_parallel_{repeat}", "summary": []},
            {
                "type": "function_call",
                "name": "read_status",
                "call_id": f"call_status_{repeat}",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "name": "read_owner",
                "call_id": f"call_owner_{repeat}",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": f"call_status_{repeat}",
                "output": json.dumps({"status": "active"}),
            },
            {
                "type": "function_call_output",
                "call_id": f"call_owner_{repeat}",
                "output": json.dumps({"owner": "team-blue"}),
            },
            {
                "type": "message",
                "role": "assistant",
                "content": f"{codename} is active and owned by team-blue.",
            },
        ]
    elif scenario != "message_history":
        raise ValueError(f"unsupported scenario: {scenario}")

    history.extend(protected)
    return MockCase(
        scenario=scenario,
        task_state=f"Preserve and report {codename}",
        instructions="Answer from verified context and preserve tool-call integrity.",
        input_items=history,
        expected_constraint=codename,
        protected_items=protected,
    )


def run_sample(
    case: MockCase,
    *,
    method: str,
    repeat: int,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
) -> dict[str, Any]:
    context_filter = OpenAIAgentsContextFilter(
        ContextPluginConfig(method=method, budget=budget),
        task_state=case.task_state,
        fixed_reserved_tokens=fixed_reserved_tokens,
    )
    outcome = context_filter.filter_items(case.input_items, case.instructions)
    tokens_before = _input_tokens(
        case.input_items,
        case.instructions,
        fixed_reserved_tokens,
    )
    tokens_after = _input_tokens(
        outcome.input_items,
        case.instructions,
        fixed_reserved_tokens,
    )
    encoded = json.dumps(
        [_jsonable(item) for item in outcome.input_items],
        ensure_ascii=False,
        default=str,
    )
    constraint_preserved = case.expected_constraint in encoded
    protected_integrity = _contains_subsequence(
        outcome.input_items,
        case.protected_items,
    )
    pairing_integrity = _unmatched_call_count(outcome.input_items) == 0
    success = constraint_preserved and protected_integrity and pairing_integrity
    return {
        "scenario": case.scenario,
        "repeat": repeat,
        "method": method,
        "success": success,
        "constraint_preserved": constraint_preserved,
        "protected_integrity": protected_integrity,
        "pairing_integrity": pairing_integrity,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "input_savings_rate": (
            (tokens_before - tokens_after) / tokens_before if tokens_before else 0.0
        ),
        "hard_budget_violation": tokens_after > budget.hard_limit_tokens,
        "protected_group_count": outcome.protected_group_count,
        "protected_item_count": outcome.protected_item_count,
        "unmatched_call_count": outcome.unmatched_call_count,
        "restore_failure_count": outcome.metrics[
            "openai_agents_group_restore_failure_count"
        ],
        "filter_metrics": outcome.metrics,
    }


def build_report(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    methods: list[dict[str, Any]] = []
    for method in sorted({str(sample["method"]) for sample in samples}):
        rows = [sample for sample in samples if sample["method"] == method]
        methods.append(
            {
                "method": method,
                "n": len(rows),
                "success_rate": _mean(rows, "success"),
                "constraint_preservation_rate": _mean(rows, "constraint_preserved"),
                "protected_integrity_rate": _mean(rows, "protected_integrity"),
                "pairing_integrity_rate": _mean(rows, "pairing_integrity"),
                "tokens_after_mean": _mean(rows, "tokens_after"),
                "input_savings_rate_mean": _mean(rows, "input_savings_rate"),
                "hard_budget_violation_rate": _mean(rows, "hard_budget_violation"),
                "restore_failure_count": sum(
                    int(row["restore_failure_count"]) for row in rows
                ),
                "unmatched_call_count": sum(
                    int(row["unmatched_call_count"]) for row in rows
                ),
            }
        )

    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for sample in samples:
        key = (str(sample["scenario"]), int(sample["repeat"]))
        by_key.setdefault(key, {})[str(sample["method"])] = sample
    paired = [pair for pair in by_key.values() if {"none", "pruner_v1"} <= pair.keys()]
    savings = [
        (pair["none"]["tokens_after"] - pair["pruner_v1"]["tokens_after"])
        / pair["none"]["tokens_after"]
        for pair in paired
        if pair["none"]["tokens_after"]
    ]
    ci_low, ci_high = _bootstrap_mean_ci(savings)
    paired_summary = {
        "baseline": "none",
        "method": "pruner_v1",
        "paired_n": len(paired),
        "input_savings_rate_vs_baseline": statistics.fmean(savings) if savings else 0.0,
        "input_savings_ci_low": ci_low,
        "input_savings_ci_high": ci_high,
        "input_savings_win_rate": (
            sum(value > 0 for value in savings) / len(savings) if savings else 0.0
        ),
        "success_delta": _paired_delta(paired, "success"),
        "protected_integrity_delta": _paired_delta(paired, "protected_integrity"),
        "pairing_integrity_delta": _paired_delta(paired, "pairing_integrity"),
        "hard_budget_violation_delta": _paired_delta(
            paired,
            "hard_budget_violation",
        ),
    }
    scenario_pairs: list[dict[str, Any]] = []
    for scenario in sorted({key[0] for key in by_key}):
        rows = [
            pair
            for key, pair in by_key.items()
            if key[0] == scenario and {"none", "pruner_v1"} <= pair.keys()
        ]
        rates = [
            (pair["none"]["tokens_after"] - pair["pruner_v1"]["tokens_after"])
            / pair["none"]["tokens_after"]
            for pair in rows
            if pair["none"]["tokens_after"]
        ]
        scenario_pairs.append(
            {
                "scenario": scenario,
                "paired_n": len(rows),
                "input_savings_rate_vs_baseline": (
                    statistics.fmean(rates) if rates else 0.0
                ),
                "success_delta": _paired_delta(rows, "success"),
                "protected_integrity_delta": _paired_delta(
                    rows,
                    "protected_integrity",
                ),
            }
        )
    return {
        "methods": methods,
        "paired": paired_summary,
        "scenario_paired": scenario_pairs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--soft", type=int, default=900)
    parser.add_argument("--hard", type=int, default=1400)
    parser.add_argument("--target", type=int, default=700)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=120)
    parser.add_argument("--out", default="runs/stage5-openai-agents-mock")
    parser.add_argument("--experiment-id", default="grouped-input-3x3-v070")
    args = parser.parse_args(argv)
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")

    root = Path(args.out) / args.experiment_id
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"experiment directory already has results: {root}")
    root.mkdir(parents=True, exist_ok=True)
    budget = ContextBudget(args.soft, args.hard, args.target)
    scenarios = ("message_history", "single_tool_group", "parallel_tool_group")
    samples: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        for scenario in scenarios:
            case = build_case(scenario, repeat)
            for method in ("none", "pruner_v1"):
                samples.append(
                    run_sample(
                        case,
                        method=method,
                        repeat=repeat,
                        budget=budget,
                        fixed_reserved_tokens=args.fixed_reserved_tokens,
                    )
                )

    report = build_report(samples)
    manifest = {
        "experiment_id": args.experiment_id,
        "kind": "offline_openai_agents_input_filter_mock",
        "network_calls": 0,
        "methods": ["none", "pruner_v1"],
        "scenarios": list(scenarios),
        "repeats": args.repeats,
        "budget": {
            "soft": args.soft,
            "hard": args.hard,
            "target": args.target,
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
        },
    }
    _write_json(root / "manifest.json", manifest)
    _write_json(root / "report.json", report)
    with (root / "samples.jsonl").open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    _write_csv(root / "method_summary.csv", report["methods"])
    _write_csv(root / "scenario_paired.csv", report["scenario_paired"])

    print(f"Offline samples: {len(samples)}")
    for row in report["methods"]:
        print(
            f"{row['method']}: success={row['success_rate']:.4f}, "
            f"integrity={row['protected_integrity_rate']:.4f}, "
            f"tokens={row['tokens_after_mean']:.1f}, "
            f"budget_violation={row['hard_budget_violation_rate']:.4f}"
        )
    paired = report["paired"]
    print(
        "paired savings="
        f"{paired['input_savings_rate_vs_baseline']:.4f} "
        f"(95% CI {paired['input_savings_ci_low']:.4f}.."
        f"{paired['input_savings_ci_high']:.4f})"
    )
    print(f"Report written to: {root}")
    return 0


def _input_tokens(items: Sequence[Any], instructions: str, fixed: int) -> int:
    encoded = json.dumps(
        [_jsonable(item) for item in items],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return estimate_tokens(encoded) + estimate_tokens(instructions) + max(0, fixed)


def _jsonable(item: Any) -> Any:
    if isinstance(item, dict):
        return item
    model_dump = getattr(item, "model_dump", None)
    return model_dump(exclude_none=True) if callable(model_dump) else item


def _contains_subsequence(items: Sequence[Any], expected: Sequence[Any]) -> bool:
    if not expected:
        return True
    for index in range(len(items) - len(expected) + 1):
        if list(items[index : index + len(expected)]) == list(expected):
            return True
    return False


def _unmatched_call_count(items: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    outputs: Counter[str] = Counter()
    for raw in items:
        item = _jsonable(raw)
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        call_id = str(item.get("call_id") or "")
        if not call_id or "call" not in item_type:
            continue
        (outputs if item_type.endswith("_output") else calls)[call_id] += 1
    return sum(abs(calls[key] - outputs[key]) for key in calls.keys() | outputs.keys())


def _mean(rows: Sequence[dict[str, Any]], key: str) -> float:
    return statistics.fmean(float(row[key]) for row in rows) if rows else 0.0


def _paired_delta(pairs: Sequence[dict[str, dict[str, Any]]], key: str) -> float:
    if not pairs:
        return 0.0
    return statistics.fmean(
        float(pair["pruner_v1"][key]) - float(pair["none"][key])
        for pair in pairs
    )


def _bootstrap_mean_ci(values: Sequence[float], rounds: int = 2000) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    rng = random.Random(42)
    boot = sorted(
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(rounds)
    )
    return boot[int(0.025 * rounds)], boot[min(rounds - 1, int(0.975 * rounds))]


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
