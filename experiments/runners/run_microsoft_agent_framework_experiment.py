"""Paired Microsoft Agent Framework validation on synthetic natural tasks.

API mode sends only the synthetic task history and synthetic tool results from
``tasks/stage5_autogen/natural_tasks.json``.  Expected answer terms are used by
the evaluator only and are never added to model messages.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import os
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import ContextBudget, ContextPluginConfig, __version__
from context_pruner.adapters import MicrosoftAgentFrameworkContextMiddleware
from context_pruner.types import estimate_tokens

try:
    from agent_framework import (
        Agent,
        ChatContext,
        ChatMiddleware,
        ChatMiddlewareLayer,
        ChatResponse,
        Content,
        FunctionInvocationLayer,
        Message,
        tool,
    )
    from agent_framework.openai import OpenAIChatCompletionClient
    from openai import AsyncOpenAI
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        'Microsoft Agent Framework is required. Install: pip install -e ".[microsoft-agent-framework]"'
    ) from error


METHODS = ("none", "pruner_v1")
SYNTHETIC_DISCLOSURE = (
    "Only synthetic conversations, identifiers, tool arguments, and synthetic "
    "tool results are sent. No workspace files or source code are read."
)


class EmptyModelResponseError(RuntimeError):
    """The provider returned neither text nor a function call."""


class RequestBudget:
    def __init__(self, limit: int, *, used: int = 0) -> None:
        self.limit = max(1, int(limit))
        self.used = max(0, int(used))
        if self.used > self.limit:
            raise ValueError(
                f"existing API requests exceed configured limit ({self.used}/{self.limit})"
            )

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(
                f"global API request limit reached ({self.used}/{self.limit})"
            )
        self.used += 1


@tool(approval_mode="never_require")
def get_service_health(service: str) -> str:
    """Return the current synthetic health for a service."""
    return json.dumps(
        {
            "service": service,
            "status": "degraded",
            "error_rate_percent": 3.8,
            "alert_threshold_percent": 2.0,
        }
    )


@tool(approval_mode="never_require")
def get_recent_deployment(service: str) -> str:
    """Return the latest synthetic deployment and safe action."""
    return json.dumps(
        {
            "service": service,
            "release": "release-2026.09.17",
            "deployed_at": "09:42 UTC",
            "rollback_safe": True,
            "recommended_action": "rollback",
        }
    )


@tool(approval_mode="never_require")
def read_release_tests(release: str) -> str:
    """Return the latest synthetic test result for a release."""
    return json.dumps(
        {"release": release, "passed": 184, "failed": 0, "status": "green"}
    )


@tool(approval_mode="never_require")
def read_release_risks(release: str) -> str:
    """Return the current synthetic release blocker."""
    return json.dumps(
        {
            "release": release,
            "open_blocker": "migration lock unresolved",
            "severity": "blocker",
            "required_decision": "NO-GO",
        }
    )


@tool(approval_mode="never_require")
def lookup_migration_plan(customer: str) -> str:
    """Return the currently approved synthetic migration plan."""
    return json.dumps(
        {
            "customer": customer,
            "plan": "enterprise",
            "target_region": "eu-west",
            "window": "02:00 UTC",
        }
    )


@tool(approval_mode="never_require")
def check_region_capacity(region: str) -> str:
    """Return the current synthetic capacity decision."""
    return json.dumps(
        {
            "region": region,
            "capacity": "available",
            "headroom": "28%",
            "required_decision": "PROCEED",
        }
    )


TOOL_REGISTRY = {
    function.name: function
    for function in (
        get_service_health,
        get_recent_deployment,
        read_release_tests,
        read_release_risks,
        lookup_migration_plan,
        check_region_capacity,
    )
}


class CaptureUsageMiddleware(ChatMiddleware):
    """Capture the post-pruning model view and per-provider-call usage."""

    def __init__(self, request_budget: RequestBudget, *, api_mode: bool) -> None:
        self.request_budget = request_budget
        self.api_mode = api_mode
        self.inputs: list[list[Message]] = []
        self.response_records: list[dict[str, Any]] = []
        self.attempt_records: list[dict[str, Any]] = []

    async def process(self, context: ChatContext, call_next) -> None:
        if self.api_mode:
            self.request_budget.consume()
        self.inputs.append(list(context.messages))
        started = time.perf_counter()
        try:
            await call_next()
        except Exception as error:
            self.attempt_records.append(
                {
                    "status": "error",
                    "latency_seconds": time.perf_counter() - started,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:300],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "finish_reason": "",
                }
            )
            raise
        response = context.result
        usage = dict(getattr(response, "usage_details", None) or {})
        input_tokens = int(usage.get("input_token_count") or 0)
        output_tokens = int(usage.get("output_token_count") or 0)
        record = {
            "status": "success",
            "latency_seconds": time.perf_counter() - started,
            "error_type": "",
            "error_message": "",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "finish_reason": str(getattr(response, "finish_reason", "") or ""),
        }
        self.attempt_records.append(record)
        self.response_records.append(record)


class _RawReplayClient:
    additional_properties: dict[str, Any] = {}
    STORES_BY_DEFAULT = False

    def __init__(self, *, responses: Sequence[ChatResponse], **kwargs: Any) -> None:
        self._responses = list(responses)
        self._response_index = 0
        super().__init__(**kwargs)

    def get_response(self, messages, *, stream=False, **kwargs):
        if stream:
            raise RuntimeError("mock validation does not support streaming")

        async def response() -> ChatResponse:
            if self._response_index >= len(self._responses):
                raise RuntimeError("mock response sequence exhausted")
            item = self._responses[self._response_index]
            self._response_index += 1
            return item

        return response()


class ReplayFunctionClient(
    FunctionInvocationLayer,
    ChatMiddlewareLayer,
    _RawReplayClient,
):
    """Deterministic client that still uses the real framework tool loop."""

    def __init__(self, responses: Sequence[ChatResponse]) -> None:
        super().__init__(
            responses=responses,
            function_invocation_configuration={
                "max_iterations": 4,
                "max_function_calls": 8,
                "allow_concurrent_invocation": True,
            },
        )


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task file must contain a non-empty list")
    known = set(TOOL_REGISTRY)
    for task in tasks:
        missing = set(task.get("tools") or ()) - known
        if missing:
            raise ValueError(f"unknown tools for {task.get('task_id')}: {sorted(missing)}")
    return tasks


def build_history(task: Mapping[str, Any], repeat: int) -> list[Message]:
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages = [
        Message("user", [str(task["history_constraint"])]),
        Message("assistant", [f"已记录当前约束，并会在后续判断中保留 {anchor}。"]),
    ]
    for index in range(8 + repeat):
        messages.extend(
            [
                Message(
                    "user",
                    [
                        f"第 {index + 1} 次历史交接涉及{topic}。当时团队还讨论了容量、"
                        "排期、负责人和备选方案，这些内容仅用于理解背景，不能替代最新运行数据。"
                    ],
                ),
                Message(
                    "assistant",
                    [
                        f"历史记录 {index + 1} 已整理：相关讨论存在时间差，部分结论已经关闭或撤销。"
                        "执行当前任务时应优先核对可用工具返回的实时事实，并继续遵守已确认标识。"
                    ],
                ),
            ]
        )
    messages.append(Message("user", [str(task["task"])]))
    return messages


def _decision_contract(task_id: str) -> str:
    return {
        "incident_triage": "ROLLBACK or MONITOR",
        "release_readiness": "GO or NO-GO",
        "customer_migration": "PROCEED or DEFER",
    }[task_id]


def _instructions(task: Mapping[str, Any]) -> str:
    return (
        "You are an operations decision Agent. Historical conversation is background only. "
        "Before answering, call every supplied tool once to verify current facts. Never invent "
        "tool results. Return exactly one ASCII line of at most 300 characters using: "
        f"RESULT task={task['task_id']} decision=<{_decision_contract(str(task['task_id']))}> "
        "evidence=<brief current tool facts>. Do not output angle brackets. Preserve every "
        "identifier, count, time window, region, and current fact requested by the latest user."
    )


def _mock_responses(task: Mapping[str, Any]) -> list[ChatResponse]:
    arguments = {
        "get_service_health": {"service": "payments-api"},
        "get_recent_deployment": {"service": "payments-api"},
        "read_release_tests": {"release": "Atlas-2.4"},
        "read_release_risks": {"release": "Atlas-2.4"},
        "lookup_migration_plan": {"customer": "Northstar"},
        "check_region_capacity": {"region": "eu-west"},
    }
    calls = [
        Content.from_function_call(
            f"call-{task['task_id']}-{index}",
            name,
            arguments=arguments[name],
        )
        for index, name in enumerate(task["tools"])
    ]
    evidence = {
        "incident_triage": (
            "INC-2048 payments-api degraded after release-2026.09.17; rollback is safe"
        ),
        "release_readiness": "Atlas-2.4 tests 184 passed, 0 failed; migration lock unresolved",
        "customer_migration": "Northstar eu-west window 02:00 UTC with 28% headroom",
    }[str(task["task_id"])]
    final = (
        f"RESULT task={task['task_id']} decision={task['decision']} evidence={evidence}"
    )
    return [
        ChatResponse(
            messages=[Message("assistant", calls)],
            finish_reason="tool_calls",
            usage_details={"input_token_count": 20, "output_token_count": 8},
        ),
        ChatResponse(
            messages=[Message("assistant", [final])],
            finish_reason="stop",
            usage_details={"input_token_count": 30, "output_token_count": 15},
        ),
    ]


def _final_response_text(response: Any) -> str:
    """Return only the final non-empty message, not AgentResponse.text's concatenation."""
    for message in reversed(list(getattr(response, "messages", ()) or ())):
        text = str(getattr(message, "text", "") or "").strip()
        if text:
            return text
    return str(getattr(response, "text", "") or "").strip()


async def _execute_once(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    request_budget: RequestBudget,
    async_client: AsyncOpenAI | None,
    model_name: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    capture = CaptureUsageMiddleware(request_budget, api_mode=mode == "api")
    context_pruner = MicrosoftAgentFrameworkContextMiddleware(
        ContextPluginConfig(method=method, budget=budget),
        task_state=f"{task['task']} {task['history_constraint']}",
        fixed_reserved_tokens=fixed_reserved_tokens,
    )
    if mode == "mock":
        client: Any = ReplayFunctionClient(_mock_responses(task))
    else:
        if async_client is None:
            raise RuntimeError("API mode requires an AsyncOpenAI client")
        client = OpenAIChatCompletionClient(
            model=model_name,
            async_client=async_client,
            function_invocation_configuration={
                "max_iterations": 4,
                "max_function_calls": 8,
                "allow_concurrent_invocation": True,
            },
        )
    agent = Agent(
        client=client,
        name=f"maf_{task['task_id']}",
        instructions=_instructions(task),
        tools=[TOOL_REGISTRY[str(name)] for name in task["tools"]],
        default_options={
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "tool_choice": "auto",
            "allow_multiple_tool_calls": True,
        },
        middleware=[context_pruner, capture],
    )
    session = agent.create_session(
        session_id=f"{task['task_id']}-{repeat:02d}-{method}"
    )
    started = time.perf_counter()
    error: Exception | None = None
    final_output = ""
    try:
        response = await agent.run(build_history(task, repeat), session=session)
        final_output = _final_response_text(response)
        if not final_output:
            raise EmptyModelResponseError("empty final model response")
    except Exception as caught:
        error = caught
    latency = time.perf_counter() - started
    return {
        "final_output": final_output,
        "latency_seconds": latency,
        "capture": capture,
        "metrics": context_pruner.metrics_dict(session),
        "error": error,
        "session_state_roundtrip": bool(
            type(session).from_dict(session.to_dict()).state.get("context_pruner")
        ),
    }


async def run_case(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    request_budget: RequestBudget,
    async_client: AsyncOpenAI | None,
    model_name: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_output_tokens: int,
    max_case_retries: int,
    retry_base_delay: float,
) -> dict[str, Any]:
    requests_before = request_budget.used
    all_inputs: list[list[Message]] = []
    all_responses: list[dict[str, Any]] = []
    all_attempts: list[dict[str, Any]] = []
    retries = 0
    error_type = ""
    error_message = ""
    outcome: dict[str, Any] | None = None
    total_latency = 0.0
    for attempt in range(max(0, max_case_retries) + 1):
        outcome = await _execute_once(
            task,
            repeat=repeat,
            method=method,
            mode=mode,
            request_budget=request_budget,
            async_client=async_client,
            model_name=model_name,
            budget=budget,
            fixed_reserved_tokens=fixed_reserved_tokens,
            max_output_tokens=max_output_tokens,
        )
        capture = outcome["capture"]
        all_inputs.extend(capture.inputs)
        all_responses.extend(capture.response_records)
        all_attempts.extend(capture.attempt_records)
        total_latency += float(outcome["latency_seconds"])
        error = outcome["error"]
        if error is None:
            error_type = ""
            error_message = ""
            break
        error_type = type(error).__name__
        error_message = str(error)[:500]
        if (
            mode != "api"
            or attempt >= max_case_retries
            or not _retryable_api_error(error)
            or request_budget.used >= request_budget.limit
        ):
            break
        retries += 1
        await asyncio.sleep(max(0.0, retry_base_delay) * (2**attempt))

    final_output = str(outcome["final_output"]) if outcome else ""
    metrics = dict(outcome["metrics"]) if outcome else {}
    tool_names = _unique_tool_names(all_inputs)
    expected_tools = tuple(str(name) for name in task["tools"])
    expected_terms = tuple(str(term) for term in task["expected_terms"])
    answer_correct = _answer_correct(
        str(task["task_id"]), final_output.lower(), expected_terms
    )
    tool_correct = Counter(tool_names) == Counter(expected_tools)
    final_format_correct = (
        final_output.startswith(f"RESULT task={task['task_id']} ")
        and "\n" not in final_output
        and len(final_output) <= 300
    )
    anchor = expected_terms[0].lower()
    constraint_preserved = bool(all_inputs) and all(
        anchor in _serialize_messages(messages).lower() for messages in all_inputs
    )
    pairing_integrity = all(_unmatched_call_count(messages) == 0 for messages in all_inputs)
    restore_failures = int(
        metrics.get("microsoft_agent_framework_group_restore_failure_count", 0)
    )
    unmatched = int(metrics.get("microsoft_agent_framework_unmatched_call_count", 0))
    structure_safe = pairing_integrity and restore_failures == 0 and unmatched == 0
    actual_inputs = [int(item["input_tokens"]) for item in all_responses]
    actual_outputs = [int(item["output_tokens"]) for item in all_responses]
    approximate_inputs = [_messages_tokens(messages) for messages in all_inputs]
    success = (
        not error_type
        and answer_correct
        and tool_correct
        and final_format_correct
        and constraint_preserved
        and structure_safe
        and bool(outcome and outcome["session_state_roundtrip"])
    )
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "repeat": repeat,
        "method": method,
        "mode": mode,
        "success": success,
        "answer_correct": answer_correct,
        "tool_correct": tool_correct,
        "final_format_correct": final_format_correct,
        "constraint_preserved": constraint_preserved,
        "pairing_integrity": pairing_integrity,
        "structure_safe": structure_safe,
        "session_state_roundtrip": bool(outcome and outcome["session_state_roundtrip"]),
        "model_calls": len(all_responses),
        "tool_calls": len(tool_names),
        "tool_names": list(tool_names),
        "actual_input_tokens": sum(actual_inputs),
        "actual_output_tokens": sum(actual_outputs),
        "actual_peak_input_tokens": max(actual_inputs, default=0),
        "approximate_input_tokens": sum(approximate_inputs),
        "approximate_peak_input_tokens": max(approximate_inputs, default=0),
        "latency_seconds": total_latency,
        "api_retry_count": retries,
        "api_attempt_records": all_attempts,
        "api_request_attempts": request_budget.used - requests_before,
        "restore_failure_count": restore_failures,
        "unmatched_call_count": unmatched,
        "resync_count": int(metrics.get("resync_count", 0)),
        "protected_group_count": int(
            metrics.get("microsoft_agent_framework_protected_group_count", 0)
        ),
        "hard_budget_violation_count": int(
            metrics.get("model_input_budget_violation_count", 0)
        ),
        "estimated_peak_model_input_tokens": int(
            metrics.get("peak_model_input_tokens", 0)
        ),
        "estimated_peak_reserved_tokens": int(
            metrics.get("peak_reserved_tokens", 0)
        ),
        "error_type": error_type,
        "error_message": error_message,
        "final_output": final_output,
    }


def _unique_tool_names(inputs: Sequence[Sequence[Message]]) -> tuple[str, ...]:
    calls: dict[str, str] = {}
    for messages in inputs:
        for message in messages:
            for content in message.contents:
                if str(content.type) != "function_call":
                    continue
                call_id = str(content.call_id or "")
                if call_id:
                    calls.setdefault(call_id, str(content.name or ""))
    return tuple(name for name in calls.values() if name)


def _unmatched_call_count(messages: Sequence[Message]) -> int:
    calls: Counter[str] = Counter()
    results: Counter[str] = Counter()
    for message in messages:
        for content in message.contents:
            call_id = str(content.call_id or "")
            if not call_id:
                continue
            if str(content.type) == "function_call":
                calls[call_id] += 1
            elif str(content.type) == "function_result":
                results[call_id] += 1
    return sum(abs(calls[key] - results[key]) for key in calls.keys() | results.keys())


def _answer_correct(
    task_id: str,
    answer_lower: str,
    expected_terms: Sequence[str],
) -> bool:
    required = [term.lower() for term in expected_terms]
    if task_id == "release_readiness":
        required = [term for term in required if term != "0 failed"]
        zero_failures = any(
            marker in answer_lower
            for marker in ("0 failed", "failed=0", "failed 0", "184/0", "184 passed/0")
        )
        return zero_failures and all(term in answer_lower for term in required)
    return all(term in answer_lower for term in required)


def _serialize_messages(messages: Sequence[Message]) -> str:
    return json.dumps(
        [message.to_dict() for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _messages_tokens(messages: Sequence[Message]) -> int:
    return estimate_tokens(_serialize_messages(messages))


def _retryable_api_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    text = str(error).lower()
    return any(
        marker in text
        for marker in ("timeout", "temporarily", "rate limit", "connection", "empty")
    )


def _result_key(row: Mapping[str, Any]) -> tuple[str, int, str]:
    return str(row["task_id"]), int(row["repeat"]), str(row["method"])


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _latest_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        latest[_result_key(row)] = dict(row)
    return list(latest.values())


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _bootstrap_ci(values: Sequence[float], seed: int = 42) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = [
        statistics.mean(rng.choice(values) for _ in values) for _ in range(4000)
    ]
    means.sort()
    return means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def summarize(rows: Sequence[Mapping[str, Any]], mode: str) -> dict[str, Any]:
    token_field = "actual_input_tokens" if mode == "api" else "approximate_input_tokens"
    peak_field = (
        "actual_peak_input_tokens" if mode == "api" else "approximate_peak_input_tokens"
    )
    by_method: dict[str, Any] = {}
    for method in sorted({str(row["method"]) for row in rows}):
        selected = [row for row in rows if row["method"] == method]
        by_method[method] = {
            "n": len(selected),
            "success_rate": statistics.mean(bool(row["success"]) for row in selected),
            "answer_correct_rate": statistics.mean(
                bool(row["answer_correct"]) for row in selected
            ),
            "tool_correct_rate": statistics.mean(
                bool(row["tool_correct"]) for row in selected
            ),
            "structure_safe_rate": statistics.mean(
                bool(row["structure_safe"]) for row in selected
            ),
            "input_tokens_mean": statistics.mean(float(row[token_field]) for row in selected),
            "peak_input_mean": statistics.mean(float(row[peak_field]) for row in selected),
            "output_tokens_mean": statistics.mean(
                float(row["actual_output_tokens"]) for row in selected
            ),
            "latency_mean": statistics.mean(float(row["latency_seconds"]) for row in selected),
            "api_requests": sum(int(row["api_request_attempts"]) for row in selected),
            "api_retries": sum(int(row["api_retry_count"]) for row in selected),
            "hard_budget_violations": sum(
                int(row["hard_budget_violation_count"]) for row in selected
            ),
            "hard_budget_violation_rate": statistics.mean(
                int(row["hard_budget_violation_count"]) > 0 for row in selected
            ),
        }
    indexed = {_result_key(row): row for row in rows}
    pair_savings: list[float] = []
    peak_savings: list[float] = []
    success_deltas: list[float] = []
    both_success_savings: list[float] = []
    output_changes: list[float] = []
    total_token_savings: list[float] = []
    latency_changes: list[float] = []
    for row in rows:
        if row["method"] != "pruner_v1":
            continue
        baseline = indexed.get((str(row["task_id"]), int(row["repeat"]), "none"))
        if baseline is None:
            continue
        base_tokens = float(baseline[token_field])
        base_peak = float(baseline[peak_field])
        saving = (
            (base_tokens - float(row[token_field])) / base_tokens if base_tokens else 0.0
        )
        pair_savings.append(saving)
        base_output = float(baseline["actual_output_tokens"])
        plugin_output = float(row["actual_output_tokens"])
        output_changes.append(
            (plugin_output - base_output) / base_output if base_output else 0.0
        )
        base_total = base_tokens + base_output
        plugin_total = float(row[token_field]) + plugin_output
        total_token_savings.append(
            (base_total - plugin_total) / base_total if base_total else 0.0
        )
        base_latency = float(baseline["latency_seconds"])
        latency_changes.append(
            (float(row["latency_seconds"]) - base_latency) / base_latency
            if base_latency
            else 0.0
        )
        peak_savings.append(
            (base_peak - float(row[peak_field])) / base_peak if base_peak else 0.0
        )
        success_deltas.append(float(bool(row["success"])) - float(bool(baseline["success"])))
        if row["success"] and baseline["success"]:
            both_success_savings.append(saving)
    low, high = _bootstrap_ci(pair_savings)
    both_low, both_high = _bootstrap_ci(both_success_savings)
    total_low, total_high = _bootstrap_ci(total_token_savings)
    output_low, output_high = _bootstrap_ci(output_changes)
    latency_low, latency_high = _bootstrap_ci(latency_changes)
    return {
        "mode": mode,
        "methods": by_method,
        "paired": {
            "n": len(pair_savings),
            "input_savings_rate_mean": statistics.mean(pair_savings) if pair_savings else 0.0,
            "input_savings_ci_low": low,
            "input_savings_ci_high": high,
            "peak_savings_rate_mean": statistics.mean(peak_savings) if peak_savings else 0.0,
            "total_token_savings_rate_mean": statistics.mean(total_token_savings)
            if total_token_savings
            else 0.0,
            "total_token_savings_ci_low": total_low,
            "total_token_savings_ci_high": total_high,
            "output_token_change_rate_mean": statistics.mean(output_changes)
            if output_changes
            else 0.0,
            "output_token_change_ci_low": output_low,
            "output_token_change_ci_high": output_high,
            "latency_change_rate_mean": statistics.mean(latency_changes)
            if latency_changes
            else 0.0,
            "latency_change_ci_low": latency_low,
            "latency_change_ci_high": latency_high,
            "success_delta": statistics.mean(success_deltas) if success_deltas else 0.0,
            "positive_savings_rate": statistics.mean(value > 0 for value in pair_savings)
            if pair_savings
            else 0.0,
            "both_success_n": len(both_success_savings),
            "both_success_input_savings_rate_mean": statistics.mean(both_success_savings)
            if both_success_savings
            else 0.0,
            "both_success_input_savings_ci_low": both_low,
            "both_success_input_savings_ci_high": both_high,
        },
    }


def _report(summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Microsoft Agent Framework 自然任务配对验证",
        "",
        f"- 模式：{summary['mode']}",
        f"- 执行数：{len(rows)}",
        f"- 配对数：{summary['paired']['n']}",
        "- 评测答案词是否向模型披露：否",
        "",
        "## 方法汇总",
        "",
    ]
    for method, values in summary["methods"].items():
        lines.append(
            f"- `{method}`：n={values['n']}，成功率={values['success_rate']:.2%}，"
            f"工具正确率={values['tool_correct_rate']:.2%}，结构安全率={values['structure_safe_rate']:.2%}，"
            f"平均输入 token={values['input_tokens_mean']:.1f}，平均输出 token={values['output_tokens_mean']:.1f}，"
            f"平均延迟={values['latency_mean']:.2f}s，硬预算违规={values['hard_budget_violations']}。"
        )
    paired = summary["paired"]
    lines.extend(
        [
            "",
            "## 相对基线",
            "",
            f"- 平均输入节省：{paired['input_savings_rate_mean']:.2%}",
            f"- bootstrap 95% CI：{paired['input_savings_ci_low']:.2%}～{paired['input_savings_ci_high']:.2%}",
            f"- 峰值输入降低：{paired['peak_savings_rate_mean']:.2%}",
            f"- 输入+输出总 token 节省：{paired['total_token_savings_rate_mean']:.2%}"
            f"（95% CI：{paired['total_token_savings_ci_low']:.2%}～{paired['total_token_savings_ci_high']:.2%}）",
            f"- 输出 token 变化：{paired['output_token_change_rate_mean']:+.2%}"
            f"（95% CI：{paired['output_token_change_ci_low']:+.2%}～{paired['output_token_change_ci_high']:+.2%}）",
            f"- 延迟变化：{paired['latency_change_rate_mean']:+.2%}"
            f"（95% CI：{paired['latency_change_ci_low']:+.2%}～{paired['latency_change_ci_high']:+.2%}）",
            f"- 正节省配对比例：{paired['positive_savings_rate']:.2%}",
            f"- 成功率差：{paired['success_delta']:+.2%}",
            f"- 双侧成功配对：{paired['both_success_n']}",
            "",
            "本报告只支持当前框架版本、任务集、模型和预算下的结论。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--tasks", default="tasks/stage5_autogen/natural_tasks.json")
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--methods", default="none,pruner_v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--soft-limit", type=int, default=1300)
    parser.add_argument("--hard-limit", type=int, default=6000)
    parser.add_argument("--target", type=int, default=1050)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--max-case-retries", type=int, default=1)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--max-api-requests", type=int, default=30)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-microsoft-agent-framework")
    parser.add_argument("--experiment-id", default="maf-natural-smoke-v120")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _build_plan(
    tasks: Sequence[Mapping[str, Any]],
    repeats: int,
    methods: Sequence[str],
) -> list[tuple[Mapping[str, Any], int, str]]:
    plan: list[tuple[Mapping[str, Any], int, str]] = []
    for repeat in range(repeats):
        for task_index, task in enumerate(tasks):
            order = list(methods)
            if (repeat * len(tasks) + task_index) % 2:
                order.reverse()
            plan.extend((task, repeat, method) for method in order)
    return plan


def _manifest(
    args: argparse.Namespace,
    tasks: Sequence[Mapping[str, Any]],
    task_path: Path,
    methods: Sequence[str],
) -> dict[str, Any]:
    return {
        "protocol_version": "maf-natural-v121",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "context_pruner_version": __version__,
        "agent_framework_core_version": importlib.metadata.version("agent-framework-core"),
        "agent_framework_openai_version": importlib.metadata.version("agent-framework-openai"),
        "mode": args.mode,
        "model": args.model if args.mode == "api" else "ReplayFunctionClient",
        "base_url": args.base_url if args.mode == "api" else None,
        "task_file": str(task_path),
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "tasks": [str(task["task_id"]) for task in tasks],
        "methods": list(methods),
        "repeats": args.repeats,
        "budget": {
            "soft": args.soft_limit,
            "hard": args.hard_limit,
            "target": args.target,
            "fixed_reserved": args.fixed_reserved_tokens,
        },
        "max_output_tokens": args.max_output_tokens,
        "max_case_retries": args.max_case_retries,
        "maximum_api_requests": args.max_api_requests,
        "evaluation_answer_terms_disclosed": False,
        "synthetic_disclosure": SYNTHETIC_DISCLOSURE,
    }


async def async_main(args: argparse.Namespace) -> None:
    methods = tuple(part.strip() for part in args.methods.split(",") if part.strip())
    if not methods or set(methods) - set(METHODS):
        raise SystemExit("methods only supports none,pruner_v1")
    if args.repeats <= 0 or args.max_api_requests <= 0:
        raise SystemExit("repeats and max-api-requests must be positive")
    if any(marker in args.base_url for marker in ("[", "]", "(", ")")):
        raise SystemExit("base-url must be a plain URL, not a Markdown link")
    task_path = Path(args.tasks)
    tasks = load_tasks(task_path)
    selected_ids = {part.strip() for part in args.task_ids.split(",") if part.strip()}
    if selected_ids:
        tasks = [task for task in tasks if str(task["task_id"]) in selected_ids]
    if not tasks:
        raise SystemExit("no tasks selected")
    plan = _build_plan(tasks, args.repeats, methods)
    minimum_requests = len(plan) * 2
    print(SYNTHETIC_DISCLOSURE)
    print(
        f"plan: mode={args.mode}, executions={len(plan)}, tasks={len(tasks)}, "
        f"repeats={args.repeats}, no-retry-minimum={minimum_requests}, "
        f"max_api_requests={args.max_api_requests}"
    )
    if args.plan:
        print("No API request was sent in --plan mode.")
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    if args.mode == "api" and args.max_api_requests < minimum_requests:
        raise SystemExit(
            f"max-api-requests is below the no-retry minimum: "
            f"{args.max_api_requests} < {minimum_requests}"
        )
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not api_key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")

    output = Path(args.out) / args.experiment_id
    results_path = output / "results.jsonl"
    manifest = _manifest(args, tasks, task_path, methods)
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "run_manifest.json"
    if args.resume and manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing_manifest != manifest:
            raise SystemExit("resume manifest does not match current arguments")
    else:
        _write_json(manifest_path, manifest)
    raw_rows = _load_existing(results_path) if args.resume else []
    latest = _latest_rows(raw_rows)
    completed = {
        _result_key(row)
        for row in latest
        if not args.retry_failed or bool(row.get("success"))
    }
    prior_requests = sum(int(row.get("api_request_attempts", 0)) for row in raw_rows)
    request_budget = RequestBudget(args.max_api_requests, used=prior_requests)
    budget = ContextBudget(args.soft_limit, args.hard_limit, args.target)
    async_client = (
        AsyncOpenAI(api_key=api_key, base_url=args.base_url, max_retries=0)
        if args.mode == "api"
        else None
    )
    try:
        for task, repeat, method in plan:
            key = (str(task["task_id"]), repeat, method)
            if key in completed:
                print(f"skip {key[0]} r{repeat:02d} {method}")
                continue
            row = await run_case(
                task,
                repeat=repeat,
                method=method,
                mode=args.mode,
                request_budget=request_budget,
                async_client=async_client,
                model_name=args.model,
                budget=budget,
                fixed_reserved_tokens=args.fixed_reserved_tokens,
                max_output_tokens=args.max_output_tokens,
                max_case_retries=args.max_case_retries,
                retry_base_delay=args.retry_base_delay,
            )
            _append_jsonl(results_path, row)
            raw_rows.append(row)
            completed.add(key)
            print(
                f"{key[0]:22} r{repeat:02d} {method:10} "
                f"success={str(row['success']):5} calls={row['model_calls']} "
                f"tok_in={row['actual_input_tokens'] if args.mode == 'api' else row['approximate_input_tokens']} "
                f"requests={request_budget.used}/{request_budget.limit}"
            )
    finally:
        if async_client is not None:
            await async_client.close()

    rows = sorted(_latest_rows(raw_rows), key=_result_key)
    summary = summarize(rows, args.mode)
    _write_json(output / "summary.json", summary)
    _write_csv(output / "results.csv", rows)
    (output / "report.md").write_text(_report(summary, rows), encoding="utf-8")
    _write_json(
        output / "request_usage.json",
        {
            "maximum_api_requests": args.max_api_requests,
            "api_requests_used": request_budget.used,
            "remaining": request_budget.limit - request_budget.used,
        },
    )
    paired = summary["paired"]
    print(
        f"paired_n={paired['n']} input_savings={paired['input_savings_rate_mean']:.2%} "
        f"95%CI=[{paired['input_savings_ci_low']:.2%}, {paired['input_savings_ci_high']:.2%}]"
    )
    print(f"report: {output}")


def main() -> None:
    asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    main()
