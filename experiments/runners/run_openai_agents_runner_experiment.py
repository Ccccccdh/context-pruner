"""Paired offline experiment on the real OpenAI Agents SDK Runner loop."""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter
from context_pruner.types import estimate_tokens

try:
    from agents import Agent, ModelResponse, RunConfig, Runner, function_tool
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        "OpenAI Agents SDK is required. Install: pip install -e \".[openai-agents]\""
    ) from error


SCENARIOS = (
    "message_history",
    "single_tool",
    "parallel_tools",
    "multi_tool_chain",
)


@dataclass(frozen=True)
class RunnerCase:
    scenario: str
    repeat: int
    codename: str
    history: list[dict[str, str]]
    expected_terms: tuple[str, ...]
    expected_model_calls: int
    expected_tool_calls: int


@function_tool
def lookup_project(codename: str) -> str:
    """Return locally verified project metadata."""
    return json.dumps({"codename": codename, "status": "active"})


@function_tool
def read_status(codename: str) -> str:
    """Return the local project status."""
    return json.dumps({"codename": codename, "status": "active"})


@function_tool
def read_owner(codename: str) -> str:
    """Return the local project owner."""
    return json.dumps({"codename": codename, "owner": "team-blue"})


@function_tool
def check_policy(codename: str) -> str:
    """Return the local policy validation result."""
    return json.dumps({"codename": codename, "policy": "compliant"})


TOOLS = [lookup_project, read_status, read_owner, check_policy]


class LocalScenarioModel(Model):
    """Deterministic local model that emits real Responses output item types."""

    def __init__(self, case: RunnerCase) -> None:
        self.case = case
        self.inputs: list[list[Any]] = []
        self.instructions: list[str] = []
        self.emitted_tool_calls = 0

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
        self.inputs.append(items)
        self.instructions.append(str(system_instructions or ""))
        call_index = len(self.inputs) - 1
        output = self._output_for_call(call_index)
        return ModelResponse(
            output=output,
            usage=Usage(requests=1),
            response_id=f"local-{self.case.scenario}-{self.case.repeat}-{call_index}",
        )

    def stream_response(self, *args, **kwargs):
        async def empty_stream():
            if False:
                yield None

        return empty_stream()

    def _output_for_call(self, call_index: int) -> list[Any]:
        scenario = self.case.scenario
        if scenario == "message_history":
            return [self._final_message(f"Verified {self.case.codename}.", call_index)]
        if scenario == "single_tool":
            if call_index == 0:
                return [self._tool_call("lookup_project", "lookup", call_index)]
            return [
                self._final_message(
                    f"Verified {self.case.codename}: active.",
                    call_index,
                )
            ]
        if scenario == "parallel_tools":
            if call_index == 0:
                return [
                    self._tool_call("read_status", "status", call_index),
                    self._tool_call("read_owner", "owner", call_index),
                ]
            return [
                self._final_message(
                    f"Verified {self.case.codename}: active, owner team-blue.",
                    call_index,
                )
            ]
        if scenario == "multi_tool_chain":
            if call_index == 0:
                return [self._tool_call("lookup_project", "lookup", call_index)]
            if call_index == 1:
                return [self._tool_call("check_policy", "policy", call_index)]
            return [
                self._final_message(
                    f"Verified {self.case.codename}: active and policy compliant.",
                    call_index,
                )
            ]
        raise RuntimeError(f"unsupported scenario: {scenario}")

    def _tool_call(self, name: str, suffix: str, call_index: int):
        self.emitted_tool_calls += 1
        return ResponseFunctionToolCall(
            arguments=json.dumps({"codename": self.case.codename}),
            call_id=(
                f"call_{self.case.scenario}_{self.case.repeat}_{call_index}_{suffix}"
            ),
            name=name,
            type="function_call",
        )

    def _final_message(self, text: str, call_index: int):
        return ResponseOutputMessage(
            id=f"msg_{self.case.scenario}_{self.case.repeat}_{call_index}",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text=text,
                    type="output_text",
                )
            ],
            role="assistant",
            status="completed",
            type="message",
        )


def build_case(scenario: str, repeat: int) -> RunnerCase:
    codename = f"Aurora-Runner-{repeat}"
    history = [
        {
            "role": "system",
            "content": f"Hard constraint: project codename {codename} must remain.",
        },
        {"role": "user", "content": "Review all accumulated project evidence."},
    ]
    for index in range(10 + repeat):
        history.extend(
            [
                {
                    "role": "assistant",
                    "content": f"routine analysis {index}: " + "background detail " * 24,
                },
                {
                    "role": "user",
                    "content": f"routine observation {index}: " + "historical noise " * 20,
                },
            ]
        )
    history.append(
        {
            "role": "user",
            "content": f"Complete scenario {scenario} and report {codename}.",
        }
    )
    expected = {
        "message_history": (codename,),
        "single_tool": (codename, "active"),
        "parallel_tools": (codename, "active", "team-blue"),
        "multi_tool_chain": (codename, "active", "compliant"),
    }[scenario]
    model_calls = 1 if scenario == "message_history" else 3 if scenario == "multi_tool_chain" else 2
    tool_calls = 0 if scenario == "message_history" else 2 if scenario in {"parallel_tools", "multi_tool_chain"} else 1
    return RunnerCase(
        scenario=scenario,
        repeat=repeat,
        codename=codename,
        history=history,
        expected_terms=expected,
        expected_model_calls=model_calls,
        expected_tool_calls=tool_calls,
    )


def run_case(
    case: RunnerCase,
    *,
    method: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
) -> dict[str, Any]:
    model = LocalScenarioModel(case)
    context_filter = None
    if method == "pruner_v1":
        context_filter = OpenAIAgentsContextFilter(
            ContextPluginConfig(method="pruner_v1", budget=budget),
            task_state=f"Complete {case.scenario} for {case.codename}",
            fixed_reserved_tokens=fixed_reserved_tokens,
        )
    agent = Agent(
        name=f"Offline Runner {case.scenario}",
        instructions="Use local function tools when needed, then answer concisely.",
        model=model,
        tools=TOOLS,
    )
    started = time.perf_counter()
    result = Runner.run_sync(
        agent,
        case.history,
        max_turns=5,
        run_config=RunConfig(
            call_model_input_filter=context_filter,
            tracing_disabled=True,
        ),
    )
    latency = time.perf_counter() - started
    call_tokens = [
        _model_input_tokens(items, instructions, fixed_reserved_tokens)
        for items, instructions in zip(model.inputs, model.instructions)
    ]
    final_output = str(result.final_output)
    answer_correct = all(term in final_output for term in case.expected_terms)
    model_call_correct = len(model.inputs) == case.expected_model_calls
    tool_call_correct = model.emitted_tool_calls == case.expected_tool_calls
    pairing_integrity = all(_unmatched_call_count(items) == 0 for items in model.inputs)
    constraint_preserved = all(
        case.codename in json.dumps(
            [_jsonable(item) for item in items],
            ensure_ascii=False,
            default=str,
        )
        for items in model.inputs
    )
    metrics = context_filter.metrics_dict() if context_filter is not None else {}
    restore_failures = int(
        metrics.get("openai_agents_group_restore_failure_count", 0)
    )
    unmatched = int(metrics.get("openai_agents_unmatched_call_count", 0))
    resync_count = int(metrics.get("resync_count", 0))
    structure_safe = pairing_integrity and restore_failures == 0 and unmatched == 0
    success = (
        answer_correct
        and model_call_correct
        and tool_call_correct
        and constraint_preserved
        and structure_safe
    )
    return {
        "scenario": case.scenario,
        "repeat": case.repeat,
        "method": method,
        "success": success,
        "answer_correct": answer_correct,
        "model_call_correct": model_call_correct,
        "tool_call_correct": tool_call_correct,
        "constraint_preserved": constraint_preserved,
        "pairing_integrity": pairing_integrity,
        "structure_safe": structure_safe,
        "model_calls": len(model.inputs),
        "tool_calls": model.emitted_tool_calls,
        "tokens_in_total": sum(call_tokens),
        "peak_context": max(call_tokens, default=0),
        "latency_seconds": latency,
        "hard_budget_violation_count": sum(
            tokens > budget.hard_limit_tokens for tokens in call_tokens
        ),
        "restore_failure_count": restore_failures,
        "unmatched_call_count": unmatched,
        "resync_count": resync_count,
        "protected_group_count": int(
            metrics.get("openai_agents_protected_group_count", 0)
        ),
        "final_output": final_output,
    }


def build_report(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    methods = []
    for method in ("none", "pruner_v1"):
        rows = [row for row in samples if row["method"] == method]
        methods.append(
            {
                "method": method,
                "n": len(rows),
                "success_rate": _mean(rows, "success"),
                "answer_correct_rate": _mean(rows, "answer_correct"),
                "constraint_preservation_rate": _mean(rows, "constraint_preserved"),
                "pairing_integrity_rate": _mean(rows, "pairing_integrity"),
                "structure_safety_rate": _mean(rows, "structure_safe"),
                "tokens_in_total_mean": _mean(rows, "tokens_in_total"),
                "peak_context_mean": _mean(rows, "peak_context"),
                "model_calls_mean": _mean(rows, "model_calls"),
                "tool_calls_mean": _mean(rows, "tool_calls"),
                "latency_mean": _mean(rows, "latency_seconds"),
                "hard_budget_violation_count": sum(
                    int(row["hard_budget_violation_count"]) for row in rows
                ),
                "restore_failure_count": sum(
                    int(row["restore_failure_count"]) for row in rows
                ),
                "unmatched_call_count": sum(
                    int(row["unmatched_call_count"]) for row in rows
                ),
                "resync_count": sum(int(row["resync_count"]) for row in rows),
            }
        )

    paired = _paired_rows(samples)
    savings = [
        (pair["none"]["tokens_in_total"] - pair["pruner_v1"]["tokens_in_total"])
        / pair["none"]["tokens_in_total"]
        for pair in paired
    ]
    peak_savings = [
        (pair["none"]["peak_context"] - pair["pruner_v1"]["peak_context"])
        / pair["none"]["peak_context"]
        for pair in paired
    ]
    low, high = _bootstrap_mean_ci(savings)
    paired_summary = {
        "baseline": "none",
        "method": "pruner_v1",
        "paired_n": len(paired),
        "input_savings_rate_vs_baseline": statistics.fmean(savings),
        "input_savings_ci_low": low,
        "input_savings_ci_high": high,
        "input_savings_win_rate": sum(value > 0 for value in savings) / len(savings),
        "peak_context_savings_rate": statistics.fmean(peak_savings),
        "success_delta": _paired_delta(paired, "success"),
        "structure_safety_delta": _paired_delta(paired, "structure_safe"),
        "model_calls_delta": _paired_delta(paired, "model_calls"),
        "tool_calls_delta": _paired_delta(paired, "tool_calls"),
        "latency_delta_seconds": _paired_delta(paired, "latency_seconds"),
    }
    scenario_paired = []
    for scenario in SCENARIOS:
        rows = [
            pair
            for pair in paired
            if pair["none"]["scenario"] == scenario
        ]
        scenario_savings = [
            (pair["none"]["tokens_in_total"] - pair["pruner_v1"]["tokens_in_total"])
            / pair["none"]["tokens_in_total"]
            for pair in rows
        ]
        scenario_paired.append(
            {
                "scenario": scenario,
                "paired_n": len(rows),
                "input_savings_rate_vs_baseline": statistics.fmean(scenario_savings),
                "success_delta": _paired_delta(rows, "success"),
                "structure_safety_delta": _paired_delta(rows, "structure_safe"),
            }
        )
    return {
        "methods": methods,
        "paired": paired_summary,
        "scenario_paired": scenario_paired,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--soft", type=int, default=900)
    parser.add_argument("--hard", type=int, default=1400)
    parser.add_argument("--target", type=int, default=700)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=120)
    parser.add_argument("--out", default="runs/stage5-openai-agents-runner-mock")
    parser.add_argument("--experiment-id", default="runner-4x3-v072")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    root = Path(args.out) / args.experiment_id
    samples_path = root / "samples.jsonl"
    manifest = {
        "experiment_id": args.experiment_id,
        "kind": "offline_openai_agents_real_runner_paired_mock",
        "network_calls": 0,
        "sdk": "openai-agents",
        "methods": ["none", "pruner_v1"],
        "scenarios": list(SCENARIOS),
        "repeats": args.repeats,
        "budget": {
            "soft": args.soft,
            "hard": args.hard,
            "target": args.target,
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
        },
    }
    if root.exists() and any(root.iterdir()) and not args.resume:
        raise SystemExit(f"experiment directory already has results: {root}")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if args.resume and manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing_manifest != manifest:
            raise SystemExit("resume manifest does not match current arguments")
    else:
        _write_json(manifest_path, manifest)

    samples = _read_samples(samples_path)
    completed = {
        (str(row["scenario"]), int(row["repeat"]), str(row["method"]))
        for row in samples
    }
    budget = ContextBudget(args.soft, args.hard, args.target)
    for repeat in range(args.repeats):
        for scenario_index, scenario in enumerate(SCENARIOS):
            methods = (
                ("none", "pruner_v1")
                if (repeat + scenario_index) % 2 == 0
                else ("pruner_v1", "none")
            )
            case = build_case(scenario, repeat)
            for method in methods:
                key = (scenario, repeat, method)
                if key in completed:
                    continue
                sample = run_case(
                    case,
                    method=method,
                    budget=budget,
                    fixed_reserved_tokens=args.fixed_reserved_tokens,
                )
                with samples_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                samples.append(sample)
                completed.add(key)
                print(
                    f"{scenario} r{repeat:02d} {method}: "
                    f"success={sample['success']} calls={sample['model_calls']} "
                    f"tokens={sample['tokens_in_total']}"
                )

    report = build_report(samples)
    _write_json(root / "report.json", report)
    _write_csv(root / "method_summary.csv", report["methods"])
    _write_csv(root / "scenario_paired.csv", report["scenario_paired"])
    paired = report["paired"]
    print(
        f"paired_n={paired['paired_n']} savings="
        f"{paired['input_savings_rate_vs_baseline']:.4f} "
        f"CI={paired['input_savings_ci_low']:.4f}..{paired['input_savings_ci_high']:.4f} "
        f"peak={paired['peak_context_savings_rate']:.4f}"
    )
    print(f"Report written to: {root}")
    return 0


def _model_input_tokens(items: Sequence[Any], instructions: str, fixed: int) -> int:
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


def _read_samples(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _paired_rows(samples: Sequence[dict[str, Any]]):
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in samples:
        key = (str(row["scenario"]), int(row["repeat"]))
        grouped.setdefault(key, {})[str(row["method"])] = row
    return [pair for pair in grouped.values() if {"none", "pruner_v1"} <= pair.keys()]


def _mean(rows: Sequence[dict[str, Any]], key: str) -> float:
    return statistics.fmean(float(row[key]) for row in rows) if rows else 0.0


def _paired_delta(pairs, key: str) -> float:
    return statistics.fmean(
        float(pair["pruner_v1"][key]) - float(pair["none"][key])
        for pair in pairs
    ) if pairs else 0.0


def _bootstrap_mean_ci(values: Sequence[float], rounds: int = 2000):
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(42)
    boot = sorted(
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(rounds)
    )
    return boot[int(rounds * 0.025)], boot[min(rounds - 1, int(rounds * 0.975))]


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
