"""Paired real-API experiment through the OpenAI Agents SDK Runner.

The payload contains synthetic task history and synthetic tool results only.  A
positive command-line confirmation is required before any request is sent.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter
from context_pruner.types import estimate_tokens
from experiments.runners import token_policy
from experiments.runners.native_summary_common import SummaryCallLedger  # noqa: F401
from experiments.runners.openai_agents_native_summary import NativeSummaryInputFilter
from experiments.runners.trigger_gate import BudgetTriggeredFilter
from experiments.runners.run_openai_agents_runner_experiment import (
    check_policy,
    lookup_project,
    read_owner,
    read_status,
)

try:
    from agents import (
        Agent,
        ModelSettings,
        OpenAIChatCompletionsModel,
        RunConfig,
        Runner,
    )
    from agents.models.interface import Model
    from openai import AsyncOpenAI
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        "OpenAI Agents SDK is required. Install: pip install -e \".[openai-agents]\""
    ) from error


SCENARIOS = ("single_tool", "parallel_tools", "multi_tool_chain")
#: Canonical arm order. `none` is the paired baseline, `native_summary` is the
#: host-native baseline (see openai_agents_native_summary.py) and `pruner_v1` is
#: the Context-Pruner plugin arm. Report order follows this tuple.
METHODS = ("none", "pruner_v1", "native_summary")
SYNTHETIC_DISCLOSURE = (
    "Only synthetic task history, codenames, tool arguments, and synthetic tool "
    "results are sent to the configured API endpoint. No workspace files are read."
)


@dataclass(frozen=True)
class ApiCase:
    scenario: str
    repeat: int
    codename: str
    history: list[dict[str, str]]
    tools: list[Any]
    expected_terms: tuple[str, ...]
    expected_tool_names: tuple[str, ...]
    expected_model_calls: int
    final_contract: str
    require_parallel_first_batch: bool = False
    answer_pattern: str = ""
    allow_repeat_tools: bool = False
    disable_thinking: bool = False


@dataclass
class RequestBudget:
    limit: int
    used: int = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(
                f"global API request limit reached ({self.used}/{self.limit})"
            )
        self.used += 1


class RecordingRetryModel(Model):
    """Record filtered model inputs, provider usage, and bounded API retries."""

    def __init__(
        self,
        inner: OpenAIChatCompletionsModel,
        request_budget: RequestBudget,
        *,
        max_retries: int,
        retry_base_delay: float,
    ) -> None:
        self.inner = inner
        self.request_budget = request_budget
        self.max_retries = max(0, max_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.inputs: list[list[Any]] = []
        self.instructions: list[str] = []
        self.responses: list[Any] = []
        self.retry_count = 0

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
    ):
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
        for attempt in range(self.max_retries + 1):
            self.request_budget.consume()
            try:
                response = await self.inner.get_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                )
                self.inputs.append(items)
                self.instructions.append(str(system_instructions or ""))
                self.responses.append(response)
                return response
            except Exception as error:
                if attempt >= self.max_retries or not _retryable_api_error(error):
                    raise
                self.retry_count += 1
                await asyncio.sleep(self.retry_base_delay * (2**attempt))

    def stream_response(self, *args, **kwargs):
        return self.inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request):
        return self.inner.get_retry_advice(request)


def build_case(scenario: str, repeat: int) -> ApiCase:
    codename = f"Aurora-API-{repeat:02d}"
    history: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "This is a synthetic evaluation. Preserve the project codename "
                f"{codename} exactly and use only the supplied local tools."
            ),
        },
        {
            "role": "user",
            "content": "Review the accumulated synthetic project notes before acting.",
        },
    ]
    for index in range(6 + repeat):
        history.extend(
            [
                {
                    "role": "assistant",
                    "content": (
                        f"old synthetic note {index}: "
                        + "routine background observation " * 18
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"old synthetic follow-up {index}: "
                        + "nonbinding historical detail " * 16
                    ),
                },
            ]
        )

    if scenario == "single_tool":
        request = (
            f"Call lookup_project for {codename}. Do not answer before the tool result. "
            "Then report the exact codename and returned status in one concise line."
        )
        tools = [lookup_project]
        expected_terms = (codename, "active")
        expected_tools = ("lookup_project",)
        expected_calls = 2
        final_contract = (
            f"RESULT codename={codename} status=<returned_status>"
        )
        parallel = False
    elif scenario == "parallel_tools":
        request = (
            f"For {codename}, call read_status and read_owner together in the same "
            "model turn. Then report the exact codename, returned status, and returned "
            "owner in one concise line."
        )
        tools = [read_status, read_owner]
        expected_terms = (codename, "active", "team-blue")
        expected_tools = ("read_status", "read_owner")
        expected_calls = 2
        final_contract = (
            f"RESULT codename={codename} status=<returned_status> "
            "owner=<returned_owner>"
        )
        parallel = True
    elif scenario == "multi_tool_chain":
        request = (
            f"For {codename}, first call lookup_project. Only after observing that result, "
            "call check_policy. Then report the exact codename, returned status, and "
            "returned policy in one concise line."
        )
        tools = [lookup_project, check_policy]
        expected_terms = (codename, "active", "compliant")
        expected_tools = ("lookup_project", "check_policy")
        expected_calls = 3
        final_contract = (
            f"RESULT codename={codename} status=<returned_status> "
            "policy=<returned_policy>"
        )
        parallel = False
    else:
        raise ValueError(f"unsupported scenario: {scenario}")
    history.append({"role": "user", "content": request})
    return ApiCase(
        scenario=scenario,
        repeat=repeat,
        codename=codename,
        history=history,
        tools=tools,
        expected_terms=expected_terms,
        expected_tool_names=expected_tools,
        expected_model_calls=expected_calls,
        final_contract=final_contract,
        require_parallel_first_batch=parallel,
    )


async def run_case(
    case: ApiCase,
    *,
    method: str,
    client: AsyncOpenAI,
    model_name: str,
    request_budget: RequestBudget,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_turns: int,
    max_output_tokens: int,
    max_api_retries: int,
    retry_base_delay: float,
    input_cost_per_million: float,
    output_cost_per_million: float,
    context_filter: Any | None = None,
    gate_model: Any | None = None,
) -> dict[str, Any]:
    requests_before = request_budget.used
    base_model = OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=client,
        strict_feature_validation=False,
    )
    # ``gate_model`` is only supplied by the zero-API gate: it replaces the
    # provider wrapper with a deterministic local model that charges the same
    # request budget and records the same inputs.
    model = gate_model if gate_model is not None else RecordingRetryModel(
        base_model,
        request_budget,
        max_retries=max_api_retries,
        retry_base_delay=retry_base_delay,
    )
    agent = Agent(
        name=f"Synthetic DeepSeek validation {case.scenario}",
        instructions=(
            "Follow the latest user request exactly. Use the supplied tools in the "
            "requested order. Never invent tool results. The final response must be exactly "
            "one line, begin with RESULT, contain at most 160 characters, and follow this "
            f"contract: {case.final_contract}. Replace angle-bracket fields with tool values. "
            "Do not discuss old notes, trust, safety, reasoning, or possible next steps."
        ),
        model=model,
        tools=case.tools,
        model_settings=ModelSettings(
            temperature=0,
            max_tokens=max_output_tokens,
            parallel_tool_calls=True,
            extra_body={"thinking": {"type": "disabled"}} if case.disable_thinking else None,
        ),
    )
    started = time.perf_counter()
    error_type = ""
    error_message = ""
    final_output = ""
    try:
        result = await Runner.run(
            agent,
            case.history,
            max_turns=max_turns,
            run_config=RunConfig(
                call_model_input_filter=context_filter,
                tracing_disabled=True,
            ),
        )
        final_output = str(result.final_output or "")
    except Exception as error:  # Persist failures so --resume can recover deterministically.
        error_type = type(error).__name__
        error_message = str(error)[:500]
    latency = time.perf_counter() - started

    tool_batches = [_tool_names(response.output) for response in model.responses]
    tool_names = tuple(name for batch in tool_batches for name in batch)
    expected_counter = Counter(case.expected_tool_names)
    tool_counter = Counter(tool_names)
    tool_correct = (
        set(tool_names) == set(case.expected_tool_names)
        if case.allow_repeat_tools else tool_counter == expected_counter
    )
    # Parallel calls are an unordered batch; only chained calls require sequence order.
    tool_order_correct = case.require_parallel_first_batch or _is_subsequence(
        case.expected_tool_names, tool_names
    )
    parallel_correct = (
        not case.require_parallel_first_batch
        or (tool_batches and Counter(tool_batches[0]) == expected_counter)
    )
    answer_lower = final_output.lower()
    answer_correct = all(term.lower() in answer_lower for term in case.expected_terms)
    if case.answer_pattern:
        answer_correct = answer_correct and bool(
            re.fullmatch(case.answer_pattern, final_output, flags=re.IGNORECASE)
        )
    final_format_correct = (
        final_output.startswith("RESULT ")
        and "\n" not in final_output
        and len(final_output) <= 160
    )
    pairing_integrity = all(_unmatched_call_count(items) == 0 for items in model.inputs)
    constraint_preserved = bool(model.inputs) and all(
        case.codename
        in json.dumps([_jsonable(item) for item in items], ensure_ascii=False, default=str)
        for items in model.inputs
    )
    metrics = context_filter.metrics_dict() if context_filter is not None else {}
    # Extra request cost paid by the host-native summary arm. It is reported
    # separately and added back into the three-arm totals so the arm cannot hide
    # work in an unmetered side channel.
    summary_input_tokens = int(metrics.get("native_summary_input_tokens", 0))
    summary_output_tokens = int(metrics.get("native_summary_output_tokens", 0))
    summary_calls = int(metrics.get("native_summary_calls", 0))
    restore_failures = int(metrics.get("openai_agents_group_restore_failure_count", 0))
    unmatched = int(metrics.get("openai_agents_unmatched_call_count", 0))
    resync_count = int(metrics.get("resync_count", 0))
    structure_safe = pairing_integrity and restore_failures == 0 and unmatched == 0
    actual_inputs = [int(response.usage.input_tokens or 0) for response in model.responses]
    actual_outputs = [int(response.usage.output_tokens or 0) for response in model.responses]
    approximate_inputs = [
        _model_input_tokens(items, instructions, fixed_reserved_tokens)
        for items, instructions in zip(model.inputs, model.instructions)
    ]
    actual_input_total = sum(actual_inputs)
    actual_output_total = sum(actual_outputs)
    estimated_cost = (
        actual_input_total * input_cost_per_million
        + actual_output_total * output_cost_per_million
    ) / 1_000_000
    success = (
        not error_type
        and answer_correct
        and final_format_correct
        and tool_correct
        and tool_order_correct
        and parallel_correct
        and constraint_preserved
        and structure_safe
    )
    return {
        "scenario": case.scenario,
        "repeat": case.repeat,
        "method": method,
        "success": success,
        "answer_correct": answer_correct,
        "final_format_correct": final_format_correct,
        "final_output_chars": len(final_output),
        "tool_correct": tool_correct,
        "tool_order_correct": tool_order_correct,
        "parallel_correct": parallel_correct,
        "constraint_preserved": constraint_preserved,
        "pairing_integrity": pairing_integrity,
        "structure_safe": structure_safe,
        "model_calls": len(model.responses),
        "expected_model_calls": case.expected_model_calls,
        "tool_calls": len(tool_names),
        "tool_names": list(tool_names),
        "actual_input_tokens": actual_input_total,
        "actual_output_tokens": actual_output_total,
        "actual_input_tokens_by_call": actual_inputs,
        "actual_output_tokens_by_call": actual_outputs,
        "actual_peak_input_tokens": max(actual_inputs, default=0),
        "approximate_input_tokens": sum(approximate_inputs),
        "approximate_peak_input_tokens": max(approximate_inputs, default=0),
        "summary_calls": summary_calls,
        "summary_input_tokens": summary_input_tokens,
        "summary_output_tokens": summary_output_tokens,
        "all_arm_input_tokens": actual_input_total + summary_input_tokens,
        "all_arm_output_tokens": actual_output_total + summary_output_tokens,
        "all_arm_total_tokens": (
            actual_input_total
            + actual_output_total
            + summary_input_tokens
            + summary_output_tokens
        ),
        "latency_seconds": latency,
        "estimated_cost": estimated_cost,
        "api_retry_count": model.retry_count,
        "api_request_attempts": request_budget.used - requests_before,
        "restore_failure_count": restore_failures,
        "unmatched_call_count": unmatched,
        "resync_count": resync_count,
        "protected_group_count": int(
            metrics.get("openai_agents_protected_group_count", 0)
        ),
        "filter_calls": int(
            metrics.get("openai_agents_filter_calls", 0)
            + metrics.get("native_summary_filter_calls", 0)
        ),
        "compressed_calls": int(
            metrics.get("openai_agents_compressed_calls", 0)
            + metrics.get("native_summary_applied_calls", 0)
        ),
        "context_compression_events": int(
            metrics.get("compression_count", 0)
        ) + int(metrics.get("native_summary_applied_calls", 0)),
        "context_compression_skip_reason": str(
            metrics.get("openai_agents_last_skip_reason")
            or metrics.get("native_summary_skipped_reason")
            or ""
        ),
        "error_type": error_type,
        "error_message": error_message,
        "final_output": final_output,
    }


def resolve_budget(args) -> tuple[ContextBudget, "Thresholds"]:
    """Build the arm budget, declared in provider tokens and converted per host.

    Estimator-unit overrides (``--soft``/``--hard``/``--target`` > 0) exist only to
    reproduce older batches; the default path is the calibrated one, so a frozen
    manifest always states the physical budget as well as the local one.
    """
    thresholds = token_policy.thresholds(
        "openai_agents",
        soft_provider=args.provider_soft,
        hard_provider=args.provider_hard,
        target_provider=args.provider_target,
    )
    soft = args.soft or thresholds.soft_estimated
    hard = args.hard or thresholds.hard_estimated
    target = args.target or thresholds.target_estimated
    return ContextBudget(soft, hard, target), thresholds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "deepseek-v4-flash"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument(
        "--provider-soft", type=int, default=1200,
        help="Soft budget in provider tokens (the unit the vendor bills).",
    )
    parser.add_argument("--provider-hard", type=int, default=3000)
    parser.add_argument("--provider-target", type=int, default=900)
    parser.add_argument(
        "--soft", type=int, default=0,
        help="Override the soft budget in estimator units (0 = derive from provider tokens).",
    )
    parser.add_argument("--hard", type=int, default=0)
    parser.add_argument("--target", type=int, default=0)
    parser.add_argument(
        "--trigger-policy", choices=("symmetric_budget", "always"), default="symmetric_budget",
        help="symmetric_budget gates every arm on the same soft budget; always is the v1 behaviour.",
    )
    parser.add_argument("--fixed-reserved-tokens", type=int, default=512)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--max-summary-tokens", type=int, default=1024)
    parser.add_argument("--max-summary-calls", type=int, default=16)
    parser.add_argument("--max-api-requests", type=int, default=60)
    parser.add_argument("--max-api-retries", type=int, default=2)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument("--out", default="runs/stage5-openai-agents-api")
    parser.add_argument("--experiment-id", default="deepseek-runner-3x3-v3arm")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenarios = _parse_scenarios(args.scenarios)
    methods = _parse_methods(args.methods)
    budget, thresholds = resolve_budget(args)
    _validate_args(args, scenarios, budget)
    minimum_model_requests = args.repeats * len(methods) * sum(
        build_case(scenario, 0).expected_model_calls for scenario in scenarios
    )
    # Arm-specific overhead that must fit inside the hard request cap. Only the
    # host-native arm makes extra model requests; the bound is derived, not
    # guessed, so the frozen cap is justified in --plan output.
    summary_headroom = (
        args.max_summary_calls
        * args.repeats
        * len(scenarios)
        if "native_summary" in methods
        else 0
    )
    minimum_requests = minimum_model_requests + summary_headroom
    if args.plan:
        _print_plan(args, scenarios, methods, minimum_requests, summary_headroom)
        return 0
    if not args.confirm_send_synthetic_data:
        raise SystemExit(
            "refusing to send API data without --confirm-send-synthetic-data; "
            "use --plan to inspect the request plan"
        )
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY or OPENAI_API_KEY is not set")
    if args.max_api_requests < minimum_requests:
        raise SystemExit(
            "--max-api-requests is below the minimum planned model calls: "
            f"{args.max_api_requests} < {minimum_requests}"
        )
    return asyncio.run(
        _run_experiment(args, scenarios, methods, api_key, minimum_requests)
    )


async def _run_experiment(
    args, scenarios, methods, api_key: str, minimum_requests: int
) -> int:
    root = Path(args.out) / args.experiment_id
    samples_path = root / "samples.jsonl"
    budget, thresholds = resolve_budget(args)
    manifest = {
        "experiment_id": args.experiment_id,
        "kind": "openai_agents_runner_real_api_paired",
        "data_class": "synthetic_only",
        "disclosure": SYNTHETIC_DISCLOSURE,
        "sdk": "openai-agents",
        "model": args.model,
        "base_url": args.base_url,
        "methods": list(methods),
        "scenarios": list(scenarios),
        "repeats": args.repeats,
        "minimum_planned_requests": minimum_requests,
        "maximum_api_requests": args.max_api_requests,
        "max_api_retries": args.max_api_retries,
        "max_turns": args.max_turns,
        "max_output_tokens": args.max_output_tokens,
        "max_summary_tokens": args.max_summary_tokens,
        "max_summary_calls": args.max_summary_calls,
        "trigger_policy": args.trigger_policy,
        "native_summary_contract": {
            "trigger": "estimated input tokens > soft budget",
            "retention": "last units up to target token budget, kept verbatim",
            "pinned": "leading user statement always kept verbatim",
            "boundary": "Responses groups are never split across a call/output pair",
            "acceptance": "summary accepted only when it shrinks the payload",
            "failure_policy": "provider error leaves the input untouched",
            "accounting": "summary requests consume the same global request budget",
            "regrowth_rule": "an already-summarised payload is not summarised again until it grows 25%",
        },
        "final_output_contract": {
            "prefix": "RESULT ",
            "single_line": True,
            "max_chars": 160,
        },
        "budget": {
            "soft": budget.soft_limit_tokens,
            "hard": budget.hard_limit_tokens,
            "target": budget.target_tokens,
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
        },
        "budget_calibration": thresholds.as_manifest(),
        "cost_rates_per_million": {
            "input": args.input_cost_per_million,
            "output": args.output_cost_per_million,
        },
    }
    if root.exists() and any(root.iterdir()) and not args.resume:
        raise SystemExit(f"experiment directory already has results: {root}")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if args.resume and manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise SystemExit("resume manifest does not match current arguments")
    else:
        _write_json(manifest_path, manifest)

    samples = _latest_samples(_read_samples(samples_path))
    completed = {
        (str(row["scenario"]), int(row["repeat"]), str(row["method"]))
        for row in samples
        if not args.retry_failed or bool(row.get("success"))
    }
    request_budget = RequestBudget(args.max_api_requests)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=0,
    )
    try:
        for repeat in range(args.repeats):
            for scenario_index, scenario in enumerate(scenarios):
                order = _arm_order(methods, repeat + scenario_index)
                case = build_case(scenario, repeat)
                for method in order:
                    key = (scenario, repeat, method)
                    if key in completed:
                        continue
                    context_filter = _build_filter(
                        method,
                        case=case,
                        client=client,
                        model_name=args.model,
                        budget=budget,
                        fixed_reserved_tokens=args.fixed_reserved_tokens,
                        max_summary_tokens=args.max_summary_tokens,
                        max_summary_calls=args.max_summary_calls,
                        timeout=args.timeout,
                        request_budget=request_budget,
                        trigger_policy=args.trigger_policy,
                    )
                    sample = await run_case(
                        case,
                        method=method,
                        client=client,
                        model_name=args.model,
                        request_budget=request_budget,
                        budget=budget,
                        fixed_reserved_tokens=args.fixed_reserved_tokens,
                        max_turns=args.max_turns,
                        max_output_tokens=args.max_output_tokens,
                        max_api_retries=args.max_api_retries,
                        retry_base_delay=args.retry_base_delay,
                        input_cost_per_million=args.input_cost_per_million,
                        output_cost_per_million=args.output_cost_per_million,
                        context_filter=context_filter,
                    )
                    _append_jsonl(samples_path, sample)
                    samples = _replace_latest(samples, sample)
                    completed.add(key)
                    print(
                        f"{scenario} r{repeat:02d} {method}: "
                        f"success={sample['success']} calls={sample['model_calls']} "
                        f"input={sample['actual_input_tokens']} "
                        f"summary_calls={sample['summary_calls']} "
                        f"requests={request_budget.used}/{request_budget.limit}"
                    )
    finally:
        await client.close()

    all_sample_rows = _read_samples(samples_path)
    report = build_report(samples)
    report["request_budget"] = {
        "used_this_invocation": request_budget.used,
        "limit_this_invocation": request_budget.limit,
        "recorded_attempts_all_rows": sum(
            _request_attempts(row) for row in all_sample_rows
        ),
        "recorded_attempts_latest_samples": sum(
            _request_attempts(row) for row in samples
        ),
    }
    _write_json(root / "report.json", report)
    _write_csv(root / "method_summary.csv", report["methods"])
    _write_csv(root / "scenario_paired.csv", report["scenario_paired"])
    paired = report["paired"]
    print(
        f"paired_n={paired['paired_n']} actual_input_savings="
        f"{paired['actual_input_savings_rate_vs_baseline']:.4f} "
        f"success_delta={paired['success_delta']:.4f}"
    )
    print(f"Report written to: {root}")
    return 0


def _build_filter(
    method: str,
    *,
    case: ApiCase,
    client: AsyncOpenAI,
    model_name: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_summary_tokens: int,
    max_summary_calls: int,
    timeout: float,
    request_budget: RequestBudget,
    trigger_policy: str = "symmetric_budget",
) -> Any | None:
    """Build the per-arm ``call_model_input_filter``.

    ``none`` installs no filter at all, which is the literal no-compression
    baseline. ``native_summary`` installs the host-native summariser and
    ``pruner_v1`` installs the Context-Pruner adapter. Both non-baseline arms
    receive the same derived recent-window budget (the plugin's target tokens) and
    - under the default ``symmetric_budget`` policy - the same trigger: the arm
    acts only above the soft budget. Without that gate the plugin compresses on
    every call while the native arm waits for the threshold, which would make the
    two arms incomparable for reasons unrelated to the compression mechanism.
    """
    if method == "none":
        return None
    if method == "native_summary":
        arm_filter: Any = NativeSummaryInputFilter(
            # A fresh async client per summary request: an AsyncOpenAI built on
            # one event loop cannot be awaited on another, and the SDK awaits the
            # filter inside its own Runner loop.
            lambda: AsyncOpenAI(
                api_key=client.api_key,
                base_url=str(client.base_url),
                timeout=timeout,
                max_retries=0,
            ),
            model_name=model_name,
            soft_limit_tokens=budget.soft_limit_tokens,
            hard_limit_tokens=budget.hard_limit_tokens,
            target_tokens=budget.target_tokens,
            retention_tokens=budget.target_tokens,
            max_summary_tokens=max_summary_tokens,
            max_summary_calls=max_summary_calls,
            timeout_seconds=timeout,
            request_budget=request_budget,
            # The same provider switch the agent loop uses. Without it the
            # reasoning model can spend the whole summary budget on hidden
            # reasoning and return an empty message, silently disabling the arm.
            extra_body={"thinking": {"type": "disabled"}},
        )
    elif method == "pruner_v1":
        arm_filter = OpenAIAgentsContextFilter(
            ContextPluginConfig(method="pruner_v1", budget=budget),
            task_state=f"Complete {case.scenario} for {case.codename}",
            fixed_reserved_tokens=fixed_reserved_tokens,
        )
    else:
        raise ValueError(f"unsupported method: {method}")
    if trigger_policy == "always":
        return arm_filter
    return BudgetTriggeredFilter(
        arm_filter, soft_limit_tokens=budget.soft_limit_tokens
    )


def _arm_order(methods: Sequence[str], offset: int) -> tuple[str, ...]:
    """Rotate arm order deterministically so ordering effects stay balanced."""
    values = tuple(methods)
    if not values:
        return ()
    shift = offset % len(values)
    return values[shift:] + values[:shift]


def _parse_methods(raw: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    unknown = sorted(set(values) - set(METHODS))
    if unknown:
        raise SystemExit(f"unknown methods: {', '.join(unknown)}")
    if not values:
        raise SystemExit("at least one method is required")
    return tuple(method for method in METHODS if method in values)


def build_report(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    latest = _latest_samples(samples)
    methods = []
    for method in METHODS:
        rows = [row for row in latest if row["method"] == method]
        methods.append(
            {
                "method": method,
                "n": len(rows),
                "success_rate": _mean(rows, "success"),
                "answer_correct_rate": _mean(rows, "answer_correct"),
                "final_format_correct_rate": _mean(rows, "final_format_correct"),
                "final_output_chars_mean": _mean(rows, "final_output_chars"),
                "tool_correct_rate": _mean(rows, "tool_correct"),
                "structure_safety_rate": _mean(rows, "structure_safe"),
                "actual_input_tokens_mean": _mean(rows, "actual_input_tokens"),
                "actual_output_tokens_mean": _mean(rows, "actual_output_tokens"),
                "actual_total_tokens_mean": _mean_total_tokens(rows),
                "summary_calls_total": sum(int(row.get("summary_calls", 0)) for row in rows),
                "summary_input_tokens_total": sum(
                    int(row.get("summary_input_tokens", 0)) for row in rows
                ),
                "summary_output_tokens_total": sum(
                    int(row.get("summary_output_tokens", 0)) for row in rows
                ),
                "all_arm_input_tokens_mean": _mean_optional(rows, "all_arm_input_tokens"),
                "all_arm_total_tokens_mean": _mean_optional(rows, "all_arm_total_tokens"),
                "context_compression_events_total": sum(
                    int(row.get("context_compression_events", 0)) for row in rows
                ),
                "actual_peak_input_tokens_mean": _mean(
                    rows, "actual_peak_input_tokens"
                ),
                "model_calls_mean": _mean(rows, "model_calls"),
                "tool_calls_mean": _mean(rows, "tool_calls"),
                "latency_mean": _mean(rows, "latency_seconds"),
                "estimated_cost_total": sum(
                    float(row["estimated_cost"]) for row in rows
                ),
                "api_retry_count": sum(int(row["api_retry_count"]) for row in rows),
                "restore_failure_count": sum(
                    int(row["restore_failure_count"]) for row in rows
                ),
                "unmatched_call_count": sum(
                    int(row["unmatched_call_count"]) for row in rows
                ),
                "resync_count": sum(int(row["resync_count"]) for row in rows),
                "error_rate": _safe_fmean([float(bool(row.get("error_type"))) for row in rows]),
            }
        )
    comparisons = _comparisons(latest)
    paired = (comparisons.get("pruner_v1_vs_none") or comparisons.get(
        "native_summary_vs_none", {})
    )
    scenario_paired = []
    for scenario in SCENARIOS:
        rows = [pair for pair in _paired_rows(latest) if pair["none"]["scenario"] == scenario]
        scenario_paired.append(
            {
                "scenario": scenario,
                "paired_n": len(rows),
                "actual_input_savings_rate_vs_baseline": _safe_fmean(
                    [_saving(pair, "actual_input_tokens") for pair in rows]
                ),
                "actual_total_token_savings_rate_vs_baseline": _safe_fmean(
                    [_total_token_saving(pair) for pair in rows]
                ),
                "actual_all_arm_savings_rate_vs_baseline": _safe_fmean(
                    [_all_arm_saving(pair) for pair in rows]
                ),
                "success_delta": _paired_delta(rows, "success"),
            }
        )
    return {
        "methods": methods,
        "paired": paired,
        "comparisons": comparisons,
        "scenario_paired": scenario_paired,
    }


#: Which arms are compared against the shared `none` baseline.
COMPARED_ARMS = ("native_summary", "pruner_v1")


def _comparisons(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in COMPARED_ARMS:
        pairs = _paired_rows(samples, arm)
        if not pairs:
            continue
        savings = [_saving(pair, "actual_input_tokens", arm) for pair in pairs]
        peak_savings = [
            _saving(pair, "actual_peak_input_tokens", arm) for pair in pairs
        ]
        total_savings = [_total_token_saving(pair, arm) for pair in pairs]
        all_arm_savings = [_all_arm_saving(pair, arm) for pair in pairs]
        output_changes = [_output_token_change(pair, arm) for pair in pairs]
        low, high = _bootstrap_mean_ci(savings)
        total_low, total_high = _bootstrap_mean_ci(total_savings)
        all_low, all_high = _bootstrap_mean_ci(all_arm_savings)
        out[f"{arm}_vs_none"] = {
            "baseline": "none",
            "method": arm,
            "paired_n": len(pairs),
            "actual_input_savings_rate_vs_baseline": _safe_fmean(savings),
            "actual_input_savings_ci_low": low,
            "actual_input_savings_ci_high": high,
            "actual_input_savings_win_rate": _safe_fmean(
                [float(value > 0) for value in savings]
            ),
            "actual_peak_input_savings_rate": _safe_fmean(peak_savings),
            "actual_total_token_savings_rate_vs_baseline": _safe_fmean(total_savings),
            "actual_total_token_savings_ci_low": total_low,
            "actual_total_token_savings_ci_high": total_high,
            "all_arm_total_token_savings_rate_vs_baseline": _safe_fmean(all_arm_savings),
            "all_arm_total_token_savings_ci_low": all_low,
            "all_arm_total_token_savings_ci_high": all_high,
            "actual_output_token_change_rate": _safe_fmean(output_changes),
            "success_delta": _paired_delta(pairs, "success", arm),
            "model_calls_delta": _paired_delta(pairs, "model_calls", arm),
            "tool_calls_delta": _paired_delta(pairs, "tool_calls", arm),
            "latency_delta_seconds": _paired_delta(pairs, "latency_seconds", arm),
            "estimated_cost_savings_rate": _paired_cost_savings(pairs, arm),
            "without_compression_subsample": {
                "paired_n": sum(
                    1
                    for pair in pairs
                    if int(pair[arm].get("context_compression_events", 0)) == 0
                ),
                "actual_input_savings_rate": _safe_fmean(
                    [
                        _saving(pair, "actual_input_tokens", arm)
                        for pair in pairs
                        if int(pair[arm].get("context_compression_events", 0)) == 0
                    ]
                ),
            },
        }
    return out


def _parse_scenarios(raw: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    unknown = sorted(set(values) - set(SCENARIOS))
    if unknown:
        raise SystemExit(f"unknown scenarios: {', '.join(unknown)}")
    if not values:
        raise SystemExit("at least one scenario is required")
    return values


def _validate_args(
    args, scenarios: Sequence[str], budget: ContextBudget | None = None
) -> None:
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    effective = budget or resolve_budget(args)[0]
    if not (
        0
        < effective.target_tokens
        <= effective.soft_limit_tokens
        <= effective.hard_limit_tokens
    ):
        raise SystemExit("budget must satisfy 0 < target <= soft <= hard")
    if args.max_api_requests <= 0 or args.max_turns <= 0:
        raise SystemExit("request and turn limits must be positive")
    if args.max_summary_calls < 0 or args.max_summary_tokens <= 0:
        raise SystemExit("summary limits must be non-negative and positive")
    if not args.base_url.startswith(("https://", "http://")):
        raise SystemExit(
            "--base-url must be a plain http(s) URL, not Markdown link syntax"
        )
    if any(value < 0 for value in (args.input_cost_per_million, args.output_cost_per_million)):
        raise SystemExit("cost rates cannot be negative")


def _print_plan(
    args,
    scenarios: Sequence[str],
    methods: Sequence[str],
    minimum_requests: int,
    summary_headroom: int,
) -> None:
    model_requests = args.repeats * len(methods) * sum(
        build_case(scenario, 0).expected_model_calls for scenario in scenarios
    )
    print(SYNTHETIC_DISCLOSURE)
    print(f"endpoint={args.base_url}")
    print(f"model={args.model}")
    print(f"scenarios={','.join(scenarios)}")
    print(f"methods={','.join(methods)}")
    print(f"paired_runs={len(scenarios) * args.repeats}")
    print(f"runner_executions={len(scenarios) * args.repeats * len(methods)}")
    print(f"minimum_planned_model_requests={model_requests}")
    print(f"summary_request_headroom={summary_headroom}")
    print(f"minimum_planned_requests={minimum_requests}")
    print(f"hard_request_cap={args.max_api_requests}")
    budget, thresholds = resolve_budget(args)
    print(
        "budget_provider_tokens="
        f"soft:{thresholds.soft_provider},hard:{thresholds.hard_provider},"
        f"target:{thresholds.target_provider}"
    )
    print(
        "budget_estimated_tokens="
        f"soft:{budget.soft_limit_tokens},hard:{budget.hard_limit_tokens},"
        f"target:{budget.target_tokens}"
    )
    print(f"calibration_ratio={thresholds.ratio}")
    print(f"trigger_policy={args.trigger_policy}")
    print(f"arm_order_rotation=rotate_by_(repeat_index + scenario_index)_mod_{len(methods)}")
    print("No API request was sent in --plan mode.")


def _tool_names(output: Sequence[Any]) -> list[str]:
    names = []
    for item in output:
        raw = _jsonable(item)
        if isinstance(raw, dict) and raw.get("type") == "function_call":
            names.append(str(raw.get("name") or ""))
    return [name for name in names if name]


def _is_subsequence(expected: Sequence[str], actual: Sequence[str]) -> bool:
    position = 0
    for value in actual:
        if position < len(expected) and value == expected[position]:
            position += 1
    return position == len(expected)


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


def _retryable_api_error(error: Exception) -> bool:
    if type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
    }:
        return True
    status = getattr(error, "status_code", None)
    return status in {408, 409, 425, 429} or (
        isinstance(status, int) and 500 <= status < 600
    )


def _read_samples(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _latest_samples(samples: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in samples:
        key = (str(row["scenario"]), int(row["repeat"]), str(row["method"]))
        latest[key] = row
    return list(latest.values())


def _replace_latest(
    samples: Sequence[dict[str, Any]], sample: dict[str, Any]
) -> list[dict[str, Any]]:
    return _latest_samples([*samples, sample])


def _append_jsonl(path: Path, sample: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _paired_rows(samples: Sequence[dict[str, Any]], arm: str = "pruner_v1"):
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in samples:
        key = (str(row["scenario"]), int(row["repeat"]))
        grouped.setdefault(key, {})[str(row["method"])] = row
    return [pair for pair in grouped.values() if {"none", arm} <= pair.keys()]


def _saving(pair: dict[str, dict[str, Any]], key: str, arm: str = "pruner_v1") -> float:
    baseline = float(pair["none"][key])
    return (baseline - float(pair[arm][key])) / baseline if baseline else 0.0


def _total_token_saving(pair: dict[str, dict[str, Any]], arm: str = "pruner_v1") -> float:
    baseline = float(pair["none"]["actual_input_tokens"]) + float(
        pair["none"]["actual_output_tokens"]
    )
    pruned = float(pair[arm]["actual_input_tokens"]) + float(
        pair[arm]["actual_output_tokens"]
    )
    return (baseline - pruned) / baseline if baseline else 0.0


def _all_arm_saving(pair: dict[str, dict[str, Any]], arm: str = "pruner_v1") -> float:
    """Savings including the arm's own auxiliary (summary) requests."""
    baseline = _all_arm_tokens(pair["none"])
    other = _all_arm_tokens(pair[arm])
    return (baseline - other) / baseline if baseline else 0.0


def _all_arm_tokens(row: Mapping[str, Any]) -> float:
    if "all_arm_total_tokens" in row:
        return float(row["all_arm_total_tokens"])
    return float(row["actual_input_tokens"]) + float(row["actual_output_tokens"])


def _output_token_change(pair: dict[str, dict[str, Any]], arm: str = "pruner_v1") -> float:
    baseline = float(pair["none"]["actual_output_tokens"])
    pruned = float(pair[arm]["actual_output_tokens"])
    return (pruned - baseline) / baseline if baseline else 0.0


def _mean_optional(rows: Sequence[dict[str, Any]], key: str) -> float:
    """Mean over rows that carry an optional metric (newer samples only)."""
    return _safe_fmean([float(row[key]) for row in rows if key in row])


def _mean(rows: Sequence[dict[str, Any]], key: str) -> float:
    return statistics.fmean(float(_sample_value(row, key)) for row in rows) if rows else 0.0


def _sample_value(row: dict[str, Any], key: str) -> Any:
    if key == "final_format_correct" and key not in row:
        output = str(row.get("final_output") or "")
        return output.startswith("RESULT ") and "\n" not in output and len(output) <= 160
    if key == "final_output_chars" and key not in row:
        return len(str(row.get("final_output") or ""))
    return row[key]


def _mean_total_tokens(rows: Sequence[dict[str, Any]]) -> float:
    return _safe_fmean(
        [
            float(row["actual_input_tokens"]) + float(row["actual_output_tokens"])
            for row in rows
        ]
    )


def _request_attempts(row: dict[str, Any]) -> int:
    return int(
        row.get(
            "api_request_attempts",
            int(row.get("model_calls", 0)) + int(row.get("api_retry_count", 0)),
        )
    )


def _safe_fmean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _paired_delta(pairs, key: str, arm: str = "pruner_v1") -> float:
    return _safe_fmean(
        [
            float(pair[arm][key]) - float(pair["none"][key])
            for pair in pairs
        ]
    )


def _paired_cost_savings(pairs, arm: str = "pruner_v1") -> float:
    baseline = sum(float(pair["none"]["estimated_cost"]) for pair in pairs)
    pruned = sum(float(pair[arm]["estimated_cost"]) for pair in pairs)
    return (baseline - pruned) / baseline if baseline else 0.0


def _bootstrap_mean_ci(values: Sequence[float], rounds: int = 2000):
    if not values:
        return 0.0, 0.0
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
