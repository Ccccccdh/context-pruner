"""Paired natural-task validation through the real AutoGen AgentChat loop.

All histories and tool outputs are synthetic.  API mode requires an explicit
confirmation flag and enforces one global request-attempt limit.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, AsyncGenerator, Mapping, Sequence

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import AutoGenContextPruner
from context_pruner.types import estimate_tokens

try:
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core import CancellationToken, FunctionCall
    from autogen_core.model_context import UnboundedChatCompletionContext
    from autogen_core.models import (
        AssistantMessage,
        ChatCompletionClient,
        CreateResult,
        FunctionExecutionResultMessage,
        ModelFamily,
        RequestUsage,
        SystemMessage,
        UserMessage,
    )
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_ext.models.replay import ReplayChatCompletionClient
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        'AutoGen is required. Install: pip install -e ".[autogen]"'
    ) from error


SYNTHETIC_DISCLOSURE = (
    "Only synthetic conversations, identifiers, tool arguments, and synthetic "
    "tool results are sent. No workspace files or source code are read."
)


class EmptyModelResponseError(RuntimeError):
    """A successful transport response without usable model content."""


def get_service_health(service: str) -> str:
    """Return the latest monitored health for a named service."""
    return json.dumps(
        {
            "service": service,
            "status": "degraded",
            "error_rate_percent": 3.8,
            "alert_threshold_percent": 2.0,
        }
    )


def get_recent_deployment(service: str) -> str:
    """Return the most recent deployment and safe remediation for a service."""
    return json.dumps(
        {
            "service": service,
            "release": "release-2026.09.17",
            "deployed_at": "09:42 UTC",
            "rollback_safe": True,
            "recommended_action": "rollback",
        }
    )


def read_release_tests(release: str) -> str:
    """Return the latest authoritative test summary for a release candidate."""
    return json.dumps(
        {"release": release, "passed": 184, "failed": 0, "status": "green"}
    )


def read_release_risks(release: str) -> str:
    """Return open production-readiness risks for a release candidate."""
    return json.dumps(
        {
            "release": release,
            "open_blocker": "migration lock unresolved",
            "severity": "blocker",
            "required_decision": "NO-GO",
        }
    )


def lookup_migration_plan(customer: str) -> str:
    """Return the currently approved migration plan for a customer."""
    return json.dumps(
        {
            "customer": customer,
            "plan": "enterprise",
            "target_region": "eu-west",
            "window": "02:00 UTC",
        }
    )


def check_region_capacity(region: str) -> str:
    """Return current capacity and the execution decision for a region."""
    return json.dumps(
        {
            "region": region,
            "capacity": "available",
            "headroom": "28%",
            "required_decision": "PROCEED",
        }
    )


TOOL_REGISTRY = {
    function.__name__: function
    for function in (
        get_service_health,
        get_recent_deployment,
        read_release_tests,
        read_release_risks,
        lookup_migration_plan,
        check_region_capacity,
    )
}


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
            raise RuntimeError(f"global API request limit reached ({self.used}/{self.limit})")
        self.used += 1


class RecordingClient(ChatCompletionClient):
    """Record AutoGen model inputs and apply bounded, observable API retries."""

    def __init__(
        self,
        inner: ChatCompletionClient,
        request_budget: RequestBudget,
        *,
        api_mode: bool,
        max_retries: int,
        retry_base_delay: float,
    ) -> None:
        self.inner = inner
        self.request_budget = request_budget
        self.api_mode = api_mode
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.inputs: list[list[Any]] = []
        self.responses: list[CreateResult] = []
        self.attempt_records: list[dict[str, Any]] = []
        self.retry_count = 0
        self.empty_response_count = 0

    async def create(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] = (),
        tool_choice: Any = "auto",
        json_output: Any = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: CancellationToken | None = None,
    ) -> CreateResult:
        for attempt in range(self.max_retries + 1):
            if self.api_mode:
                self.request_budget.consume()
            attempt_started = time.perf_counter()
            try:
                response = await self.inner.create(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    json_output=json_output,
                    extra_create_args=extra_create_args,
                    cancellation_token=cancellation_token,
                )
            except Exception as error:
                self.attempt_records.append(
                    {
                        "status": "error",
                        "attempt_index": len(self.attempt_records),
                        "latency_seconds": time.perf_counter() - attempt_started,
                        "error_type": type(error).__name__,
                        "error_message": str(error)[:300],
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "finish_reason": "",
                        "content_chars": 0,
                        "thought_chars": 0,
                        "thought_tokens_estimate": 0,
                    }
                )
                if (
                    not self.api_mode
                    or attempt >= self.max_retries
                    or not _retryable_api_error(error)
                ):
                    raise
                self.retry_count += 1
                await asyncio.sleep(self.retry_base_delay * (2**attempt))
                continue

            empty = _empty_model_content(response.content)
            thought = str(response.thought or "")
            self.attempt_records.append(
                {
                    "status": "empty" if empty else "success",
                    "attempt_index": len(self.attempt_records),
                    "latency_seconds": time.perf_counter() - attempt_started,
                    "error_type": "EmptyModelResponseError" if empty else "",
                    "error_message": "empty model response" if empty else "",
                    "prompt_tokens": int(response.usage.prompt_tokens),
                    "completion_tokens": int(response.usage.completion_tokens),
                    "finish_reason": str(response.finish_reason),
                    "content_chars": len(response.content)
                    if isinstance(response.content, str)
                    else 0,
                    "thought_chars": len(thought),
                    "thought_tokens_estimate": estimate_tokens(thought),
                }
            )
            if empty:
                self.empty_response_count += 1
                error = EmptyModelResponseError("empty model response")
                if (
                    not self.api_mode
                    or attempt >= self.max_retries
                    or not _retryable_api_error(error)
                ):
                    raise error
                self.retry_count += 1
                await asyncio.sleep(self.retry_base_delay * (2**attempt))
                continue
            self.inputs.append(list(messages))
            self.responses.append(response)
            return response
        raise AssertionError("unreachable retry loop")

    async def create_stream(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] = (),
        tool_choice: Any = "auto",
        json_output: Any = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncGenerator[str | CreateResult, None]:
        if self.api_mode:
            self.request_budget.consume()
        self.inputs.append(list(messages))
        async for item in self.inner.create_stream(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        ):
            if isinstance(item, CreateResult):
                self.responses.append(item)
            yield item

    async def close(self) -> None:
        await self.inner.close()

    def actual_usage(self) -> RequestUsage:
        return self.inner.actual_usage()

    def total_usage(self) -> RequestUsage:
        return self.inner.total_usage()

    def count_tokens(self, messages: Sequence[Any], *, tools: Sequence[Any] = ()) -> int:
        return self.inner.count_tokens(messages, tools=tools)

    def remaining_tokens(
        self, messages: Sequence[Any], *, tools: Sequence[Any] = ()
    ) -> int:
        return self.inner.remaining_tokens(messages, tools=tools)

    @property
    def capabilities(self):
        return self.inner.capabilities

    @property
    def model_info(self):
        return self.inner.model_info


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("natural task file must contain a non-empty list")
    known = set(TOOL_REGISTRY)
    for task in tasks:
        missing = set(task.get("tools") or []) - known
        if missing:
            raise ValueError(f"unknown tools for {task.get('task_id')}: {sorted(missing)}")
    return tasks


def build_history(task: Mapping[str, Any], repeat: int) -> list[Any]:
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages: list[Any] = [
        UserMessage(content=str(task["history_constraint"]), source="user"),
        AssistantMessage(
            content=f"已记录当前约束，并会在后续判断中保留 {anchor}。",
            source="assistant",
        ),
    ]
    for index in range(8 + repeat):
        messages.extend(
            [
                UserMessage(
                    content=(
                        f"第 {index + 1} 次历史交接涉及{topic}。当时团队还讨论了容量、"
                        "排期、负责人和备选方案，这些内容仅用于理解背景，不能替代最新运行数据。"
                    ),
                    source="user",
                ),
                AssistantMessage(
                    content=(
                        f"历史记录 {index + 1} 已整理：相关讨论存在时间差，部分结论已经关闭或撤销。"
                        "执行当前任务时应优先核对可用工具返回的实时事实，并继续遵守已确认标识。"
                    ),
                    source="assistant",
                ),
            ]
        )
    return messages


def _mock_responses(task: Mapping[str, Any]) -> list[CreateResult | str]:
    arguments = {
        "get_service_health": {"service": "payments-api"},
        "get_recent_deployment": {"service": "payments-api"},
        "read_release_tests": {"release": "Atlas-2.4"},
        "read_release_risks": {"release": "Atlas-2.4"},
        "lookup_migration_plan": {"customer": "Northstar"},
        "check_region_capacity": {"region": "eu-west"},
    }
    calls = [
        FunctionCall(
            id=f"call-{task['task_id']}-{index}",
            name=name,
            arguments=json.dumps(arguments[name]),
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
        CreateResult(
            finish_reason="function_calls",
            content=calls,
            usage=RequestUsage(prompt_tokens=20, completion_tokens=8),
            cached=False,
        ),
        final,
    ]


def _decision_contract(task_id: str) -> str:
    return {
        "incident_triage": "<ROLLBACK|MONITOR>",
        "release_readiness": "<GO|NO-GO>",
        "customer_migration": "<PROCEED|DEFER>",
    }[task_id]


async def run_case(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    request_budget: RequestBudget,
    model_name: str,
    base_url: str,
    api_key: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_output_tokens: int,
    max_api_retries: int,
    retry_base_delay: float,
) -> dict[str, Any]:
    history = build_history(task, repeat)
    requests_before = request_budget.used
    if mode == "mock":
        inner: ChatCompletionClient = ReplayChatCompletionClient(
            _mock_responses(task),
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "family": ModelFamily.UNKNOWN,
                "structured_output": False,
            },
        )
    else:
        inner = OpenAIChatCompletionClient(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            max_tokens=max_output_tokens,
            max_retries=0,
            parallel_tool_calls=True,
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "family": "unknown",
                "structured_output": False,
                "multiple_system_messages": False,
            },
        )
    client = RecordingClient(
        inner,
        request_budget,
        api_mode=mode == "api",
        max_retries=max_api_retries,
        retry_base_delay=retry_base_delay,
    )
    if method == "pruner_v1":
        model_context: Any = AutoGenContextPruner(
            ContextPluginConfig(method="pruner_v1", budget=budget),
            initial_messages=history,
            task_state=f"{task['task']} {task['history_constraint']}",
            fixed_reserved_tokens=fixed_reserved_tokens,
        )
    else:
        model_context = UnboundedChatCompletionContext(initial_messages=history)

    tools = [TOOL_REGISTRY[name] for name in task["tools"]]
    contract = _decision_contract(str(task["task_id"]))
    system_message = (
        "你是负责运行决策的 Agent。历史对话只是背景；在作答前，必须使用可用工具核对形成"
        "决策所需的当前事实。不得编造工具结果，也不要依赖过期记录。最终回答必须恰好一行，"
        f"不超过 300 个字符，格式为：RESULT task={task['task_id']} "
        f"decision={contract} evidence=<简短的工具事实>。尖括号只表示字段要求，不要原样输出。"
        "必须覆盖用户在最新请求和固定约束中要求的每个标识、计数、时间窗口和关键事实，不能因"
        "简洁而遗漏。最终一行只使用英文 ASCII 字符，字段值保持工具返回的原始拼写。"
    )
    agent = AssistantAgent(
        f"natural_{task['task_id']}",
        client,
        tools=tools,
        model_context=model_context,
        reflect_on_tool_use=True,
        max_tool_iterations=3,
        system_message=system_message,
    )

    started = time.perf_counter()
    error_type = ""
    error_message = ""
    final_output = ""
    result_messages: Sequence[Any] = ()
    try:
        result = await agent.run(task=str(task["task"]))
        result_messages = result.messages
        if result_messages:
            final_output = str(getattr(result_messages[-1], "content", "") or "")
    except Exception as error:
        error_type = type(error).__name__
        error_message = str(error)[:500]
    latency = time.perf_counter() - started

    tool_names = _tool_names(result_messages)
    expected_tools = tuple(str(name) for name in task["tools"])
    answer_lower = final_output.lower()
    expected_terms = tuple(str(term) for term in task["expected_terms"])
    answer_correct = _answer_correct(str(task["task_id"]), answer_lower, expected_terms)
    tool_correct = Counter(tool_names) == Counter(expected_tools)
    final_format_correct = (
        final_output.startswith(f"RESULT task={task['task_id']} ")
        and "\n" not in final_output
        and len(final_output) <= 300
    )
    pairing_integrity = all(_unmatched_call_count(messages) == 0 for messages in client.inputs)
    anchor = expected_terms[0]
    constraint_preserved = bool(client.inputs) and all(
        anchor.lower() in _serialize_messages(messages).lower()
        for messages in client.inputs
    )
    metrics = model_context.metrics_dict() if method == "pruner_v1" else {}
    restore_failures = int(metrics.get("autogen_group_restore_failure_count", 0))
    unmatched = int(metrics.get("autogen_unmatched_call_count", 0))
    resync_count = int(metrics.get("resync_count", 0))
    structure_safe = pairing_integrity and restore_failures == 0 and unmatched == 0
    provider_inputs = [int(response.usage.prompt_tokens) for response in client.responses]
    provider_outputs = [int(response.usage.completion_tokens) for response in client.responses]
    approximate_inputs = [_messages_tokens(messages) for messages in client.inputs]
    success = (
        not error_type
        and answer_correct
        and tool_correct
        and final_format_correct
        and constraint_preserved
        and structure_safe
    )
    await client.close()
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
        "model_calls": len(client.responses),
        "tool_calls": len(tool_names),
        "tool_names": list(tool_names),
        "actual_input_tokens": sum(provider_inputs),
        "actual_output_tokens": sum(provider_outputs),
        "actual_peak_input_tokens": max(provider_inputs, default=0),
        "approximate_input_tokens": sum(approximate_inputs),
        "approximate_peak_input_tokens": max(approximate_inputs, default=0),
        "latency_seconds": latency,
        "api_retry_count": client.retry_count,
        "empty_response_count": client.empty_response_count,
        "api_attempt_records": client.attempt_records,
        "api_request_attempts": request_budget.used - requests_before,
        "restore_failure_count": restore_failures,
        "unmatched_call_count": unmatched,
        "resync_count": resync_count,
        "protected_group_count": int(metrics.get("autogen_protected_group_count", 0)),
        "hard_budget_violation_count": int(
            metrics.get("model_input_budget_violation_count", 0)
        ),
        "error_type": error_type,
        "error_message": error_message,
        "final_output": final_output,
    }


def _tool_names(messages: Sequence[Any]) -> tuple[str, ...]:
    names: list[str] = []
    for message in messages:
        if getattr(message, "type", "") != "ToolCallRequestEvent":
            continue
        for call in getattr(message, "content", ()):
            names.append(str(getattr(call, "name", "")))
    return tuple(name for name in names if name)


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


def _unmatched_call_count(messages: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    results: Counter[str] = Counter()
    for message in messages:
        if isinstance(message, AssistantMessage) and isinstance(message.content, list):
            calls.update(str(call.id) for call in message.content)
        elif isinstance(message, FunctionExecutionResultMessage):
            results.update(str(result.call_id) for result in message.content)
    return sum(abs(calls[key] - results[key]) for key in calls.keys() | results.keys())


def _serialize_messages(messages: Sequence[Any]) -> str:
    return json.dumps(
        [
            message.model_dump(exclude_none=True)
            if callable(getattr(message, "model_dump", None))
            else str(message)
            for message in messages
        ],
        ensure_ascii=False,
        default=str,
    )


def _messages_tokens(messages: Sequence[Any]) -> int:
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


def _empty_model_content(content: Any) -> bool:
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return len(content) == 0
    return content is None


def _result_key(row: Mapping[str, Any]) -> tuple[str, int, str]:
    return str(row["task_id"]), int(row["repeat"]), str(row["method"])


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
        }
    indexed = {_result_key(row): row for row in rows}
    pair_savings: list[float] = []
    peak_savings: list[float] = []
    quality_deltas: list[float] = []
    for row in rows:
        if row["method"] != "pruner_v1":
            continue
        baseline = indexed.get((str(row["task_id"]), int(row["repeat"]), "none"))
        if baseline is None:
            continue
        base_tokens = float(baseline[token_field])
        base_peak = float(baseline[peak_field])
        pair_savings.append(
            (base_tokens - float(row[token_field])) / base_tokens if base_tokens else 0.0
        )
        peak_savings.append(
            (base_peak - float(row[peak_field])) / base_peak if base_peak else 0.0
        )
        quality_deltas.append(float(bool(row["success"])) - float(bool(baseline["success"])))
    low, high = _bootstrap_ci(pair_savings)
    return {
        "mode": mode,
        "methods": by_method,
        "paired": {
            "n": len(pair_savings),
            "input_savings_rate_mean": statistics.mean(pair_savings) if pair_savings else 0.0,
            "input_savings_ci_low": low,
            "input_savings_ci_high": high,
            "peak_savings_rate_mean": statistics.mean(peak_savings) if peak_savings else 0.0,
            "success_delta": statistics.mean(quality_deltas) if quality_deltas else 0.0,
            "positive_savings_rate": statistics.mean(value > 0 for value in pair_savings)
            if pair_savings
            else 0.0,
        },
    }


def _report(summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# AutoGen 自然任务配对验证",
        "",
        f"- 模式：{summary['mode']}",
        f"- 执行数：{len(rows)}",
        f"- 配对数：{summary['paired']['n']}",
        "",
        "## 方法汇总",
        "",
    ]
    for method, values in summary["methods"].items():
        lines.append(
            f"- `{method}`：n={values['n']}，成功率={values['success_rate']:.2%}，"
            f"工具正确率={values['tool_correct_rate']:.2%}，结构安全率={values['structure_safe_rate']:.2%}，"
            f"平均输入 token={values['input_tokens_mean']:.1f}，平均延迟={values['latency_mean']:.2f}s。"
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
            f"- 正节省配对比例：{paired['positive_savings_rate']:.2%}",
            f"- 成功率差：{paired['success_delta']:+.2%}",
            "",
            "本报告只支持当前 AutoGen 版本、任务集、模型和预算下的结论。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument(
        "--tasks", default="tasks/stage5_autogen/natural_tasks.json"
    )
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--methods", default="none,pruner_v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--soft-limit", type=int, default=1300)
    parser.add_argument("--hard-limit", type=int, default=2800)
    parser.add_argument("--target", type=int, default=1050)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--max-api-retries", type=int, default=1)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--max-api-requests", type=int, default=36)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-autogen-natural")
    parser.add_argument("--experiment-id", default="autogen-natural-smoke-v080")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> None:
    methods = tuple(part.strip() for part in args.methods.split(",") if part.strip())
    if set(methods) - {"none", "pruner_v1"}:
        raise SystemExit("methods only supports none,pruner_v1")
    if args.repeats <= 0:
        raise SystemExit("repeats must be positive")
    if any(marker in args.base_url for marker in ("[", "]", "(", ")")):
        raise SystemExit("base-url must be a plain URL, not a Markdown link")
    tasks = load_tasks(Path(args.tasks))
    selected_ids = {part.strip() for part in args.task_ids.split(",") if part.strip()}
    if selected_ids:
        tasks = [task for task in tasks if task["task_id"] in selected_ids]
    if not tasks:
        raise SystemExit("no tasks selected")
    plan = [
        (task, repeat, method)
        for repeat in range(args.repeats)
        for task in tasks
        for method in methods
    ]
    print(SYNTHETIC_DISCLOSURE)
    print(
        f"plan: mode={args.mode}, executions={len(plan)}, tasks={len(tasks)}, "
        f"repeats={args.repeats}, max_api_requests={args.max_api_requests}"
    )
    if args.plan:
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not api_key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")

    output = Path(args.out) / args.experiment_id
    jsonl_path = output / "results.jsonl"
    if output.exists() and not args.resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    rows = _load_existing(jsonl_path) if args.resume else []
    completed = {_result_key(row) for row in rows}
    prior_api_requests = sum(int(row.get("api_request_attempts", 0)) for row in rows)
    request_budget = RequestBudget(args.max_api_requests, used=prior_api_requests)
    budget = ContextBudget(args.soft_limit, args.hard_limit, args.target)

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
            model_name=args.model,
            base_url=args.base_url,
            api_key=api_key,
            budget=budget,
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            max_output_tokens=args.max_output_tokens,
            max_api_retries=args.max_api_retries,
            retry_base_delay=args.retry_base_delay,
        )
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows.append(row)
        completed.add(key)
        print(
            f"{key[0]:22} r{repeat:02d} {method:10} "
            f"success={str(row['success']):5} calls={row['model_calls']} "
            f"tok_in={row['actual_input_tokens'] if args.mode == 'api' else row['approximate_input_tokens']}"
        )

    summary = summarize(rows, args.mode)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(output / "results.csv", rows)
    (output / "report.md").write_text(_report(summary, rows), encoding="utf-8")
    manifest = {
        "version": "0.8.0",
        "framework": "autogen-agentchat",
        "mode": args.mode,
        "model": args.model if args.mode == "api" else "ReplayChatCompletionClient",
        "base_url": args.base_url if args.mode == "api" else None,
        "tasks": [task["task_id"] for task in tasks],
        "methods": list(methods),
        "repeats": args.repeats,
        "budget": {
            "soft": args.soft_limit,
            "hard": args.hard_limit,
            "target": args.target,
            "fixed_reserved": args.fixed_reserved_tokens,
        },
        "api_request_limit": args.max_api_requests,
        "api_requests_used": request_budget.used,
        "synthetic_disclosure": SYNTHETIC_DISCLOSURE,
    }
    (output / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
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
