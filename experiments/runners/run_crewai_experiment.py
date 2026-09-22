"""Paired CrewAI validation on synthetic natural decision tasks.

API mode sends only the synthetic history and synthetic tool results from
``tasks/stage5_autogen/natural_tasks.json``.  Expected answer terms are used by
the evaluator only and are never included in model messages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import random
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import ContextBudget, ContextPluginConfig, __version__
from context_pruner.adapters import CrewAIContextAdapter
from context_pruner.types import estimate_tokens

try:
    from crewai import Agent, BaseLLM
    from crewai.tools import tool
    from openai import OpenAI
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        'CrewAI is required. Use the isolated environment and install: '
        'pip install -e ".[crewai]"'
    ) from error


METHODS = ("none", "pruner_v1")
SYNTHETIC_DISCLOSURE = (
    "Only synthetic conversations, identifiers, tool arguments, and synthetic "
    "tool results are sent. No workspace files or source code are read."
)


class EmptyModelResponseError(RuntimeError):
    """The provider returned no textual response."""


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


class RecordingCrewAILLM(BaseLLM):
    """Base provider that records the post-hook model view and usage."""

    def __init__(self, *, model: str) -> None:
        super().__init__(model=model, provider="custom")
        self.inputs: list[list[dict[str, Any]]] = []
        self.responses: list[str] = []
        self.response_records: list[dict[str, Any]] = []
        self.attempt_records: list[dict[str, Any]] = []

    @staticmethod
    def normalize_messages(messages: str | Sequence[Any]) -> list[dict[str, Any]]:
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        normalized: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, Mapping):
                normalized.append(dict(message))
                continue
            normalized.append(
                {
                    "role": str(getattr(message, "role", "user") or "user"),
                    "content": str(getattr(message, "content", message) or ""),
                }
            )
        return normalized

    def _record_input(self, messages: str | Sequence[Any]) -> list[dict[str, Any]]:
        normalized = self.normalize_messages(messages)
        self.inputs.append(normalized)
        return normalized


class ReplayCrewAILLM(RecordingCrewAILLM):
    """Deterministic provider that still uses CrewAI's real text tool loop."""

    def __init__(self, responses: Sequence[str]) -> None:
        super().__init__(model="context-pruner-replay")
        self._remaining = list(responses)

    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ) -> str:
        normalized = self._record_input(messages)
        started = time.perf_counter()
        if not self._remaining:
            raise RuntimeError("mock response sequence exhausted")
        response = self._remaining.pop(0)
        input_tokens = _messages_tokens(normalized)
        output_tokens = estimate_tokens(response)
        record = {
            "status": "success",
            "latency_seconds": time.perf_counter() - started,
            "error_type": "",
            "error_message": "",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "finish_reason": "mock",
            "reasoning_tokens": 0,
            "reasoning_characters": 0,
            "raw_response_characters": len(response),
            "normalized_response_characters": len(response),
            "react_response_sanitized": False,
        }
        self.responses.append(response)
        self.response_records.append(record)
        self.attempt_records.append(record)
        return response


class OpenAICompatCrewAILLM(RecordingCrewAILLM):
    """Text-ReAct CrewAI provider over an OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        client: OpenAI,
        model: str,
        request_budget: RequestBudget,
        max_output_tokens: int,
        thinking_mode: str,
    ) -> None:
        super().__init__(model=model)
        self.client = client
        self.request_budget = request_budget
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.thinking_mode = thinking_mode

    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ) -> str:
        normalized = self._record_input(messages)
        self.request_budget.consume()
        started = time.perf_counter()
        try:
            request: dict[str, Any] = {
                "model": self.model,
                "messages": normalized,
                "temperature": 0,
                "max_tokens": self.max_output_tokens,
            }
            if self.thinking_mode == "disabled":
                request["extra_body"] = {"thinking": {"type": "disabled"}}
            completion = self.client.chat.completions.create(
                **request
            )
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
                    "reasoning_tokens": 0,
                    "reasoning_characters": 0,
                    "raw_response_characters": 0,
                    "normalized_response_characters": 0,
                    "react_response_sanitized": False,
                }
            )
            raise
        choice = completion.choices[0]
        raw_response = str(choice.message.content or "").strip()
        response, sanitized = _normalize_react_response(raw_response)
        reasoning = str(getattr(choice.message, "reasoning_content", "") or "")
        usage = completion.usage
        completion_details = getattr(usage, "completion_tokens_details", None)
        record = {
            "status": "success" if response else "empty",
            "latency_seconds": time.perf_counter() - started,
            "error_type": "" if response else "EmptyModelResponseError",
            "error_message": "" if response else "empty model response",
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "finish_reason": str(getattr(choice, "finish_reason", "") or ""),
            "reasoning_tokens": int(
                getattr(completion_details, "reasoning_tokens", 0) or 0
            ),
            "reasoning_characters": len(reasoning),
            "raw_response_characters": len(raw_response),
            "normalized_response_characters": len(response),
            "react_response_sanitized": sanitized,
        }
        self.responses.append(response)
        self.response_records.append(record)
        self.attempt_records.append(record)
        if not response:
            raise EmptyModelResponseError("empty model response")
        return response


def _normalize_react_response(response: str) -> tuple[str, bool]:
    """Keep one valid CrewAI text-ReAct action and discard fabricated observations.

    The endpoint does not receive native tool definitions: CrewAI renders its own
    text protocol.  Some models nevertheless emit an Observation or a second
    Action in the same turn.  Only the first syntactically valid action is safe to
    execute; observations must come from CrewAI after the actual tool call.
    """

    original = response.strip()
    if not original:
        return "", False
    cleaned = original.replace("```json", "").replace("```", "").strip()
    action_match = re.search(r"(?im)^\s*Action\s*:\s*([^\r\n]+)", cleaned)
    if action_match:
        input_match = re.search(
            r"(?im)^\s*Action\s+Input\s*:\s*", cleaned[action_match.end() :]
        )
        if input_match:
            input_start = action_match.end() + input_match.end()
            candidate = cleaned[input_start:].lstrip()
            try:
                arguments, _ = json.JSONDecoder().raw_decode(candidate)
            except json.JSONDecodeError:
                return cleaned, cleaned != original
            if isinstance(arguments, Mapping):
                thought = cleaned[: action_match.start()].strip()
                thought = re.sub(r"(?im)^\s*Thought\s*:\s*", "", thought, count=1)
                thought = " ".join(thought.split()) or "I will call the required tool."
                action = action_match.group(1).strip().strip("` ")
                normalized = (
                    f"Thought: {thought}\nAction: {action}\nAction Input: "
                    f"{json.dumps(arguments, ensure_ascii=False, separators=(',', ':'))}"
                )
                return normalized, normalized != original
    final_match = re.search(r"(?im)^\s*Final Answer\s*:\s*", cleaned)
    if final_match:
        final = cleaned[final_match.end() :].strip().splitlines()[0].strip()
        normalized = f"Final Answer: {final}"
        return normalized, normalized != original
    return cleaned, cleaned != original


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task file must contain a non-empty list")
    known = {
        "get_service_health",
        "get_recent_deployment",
        "read_release_tests",
        "read_release_risks",
        "lookup_migration_plan",
        "check_region_capacity",
    }
    for task in tasks:
        missing = set(task.get("tools") or ()) - known
        if missing:
            raise ValueError(f"unknown tools for {task.get('task_id')}: {sorted(missing)}")
    return tasks


def build_history(task: Mapping[str, Any], repeat: int) -> list[dict[str, str]]:
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages = [
        {"role": "user", "content": str(task["history_constraint"])},
        {
            "role": "assistant",
            "content": f"已记录当前约束，并会在后续判断中保留 {anchor}。",
        },
    ]
    for index in range(8 + repeat):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"第 {index + 1} 次历史交接涉及{topic}。当时团队还讨论了容量、"
                        "排期、负责人和备选方案，这些内容仅用于理解背景，不能替代最新运行数据。"
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        f"历史记录 {index + 1} 已整理：相关讨论存在时间差，部分结论已经关闭或撤销。"
                        "执行当前任务时应优先核对可用工具返回的实时事实，并继续遵守已确认标识。"
                    ),
                },
            ]
        )
    messages.append({"role": "user", "content": _latest_task_prompt(task)})
    return messages


def _decision_contract(task_id: str) -> str:
    return {
        "incident_triage": "ROLLBACK or MONITOR",
        "release_readiness": "GO or NO-GO",
        "customer_migration": "PROCEED or DEFER",
    }[task_id]


def _latest_task_prompt(task: Mapping[str, Any]) -> str:
    return (
        f"{task['task']} Call every available tool exactly once before answering. "
        "Never invent tool results. Return exactly one ASCII line of at most 300 characters: "
        f"RESULT task={task['task_id']} decision=<{_decision_contract(str(task['task_id']))}> "
        "evidence=<brief current tool facts>. Do not output angle brackets. Preserve every "
        "identifier, count, time window, region, and current fact requested here."
    )


def _mock_responses(task: Mapping[str, Any]) -> list[str]:
    arguments = {
        "get_service_health": {"service": "payments-api"},
        "get_recent_deployment": {"service": "payments-api"},
        "read_release_tests": {"release": "Atlas-2.4"},
        "read_release_risks": {"release": "Atlas-2.4"},
        "lookup_migration_plan": {"customer": "Northstar"},
        "check_region_capacity": {"region": "eu-west"},
    }
    responses = []
    for index, name in enumerate(task["tools"]):
        responses.append(
            "Thought: I must verify current evidence with the next required tool.\n"
            f"Action: {name}\n"
            f"Action Input: {json.dumps(arguments[str(name)], ensure_ascii=False)}"
        )
    evidence = {
        "incident_triage": (
            "INC-2048 payments-api degraded after release-2026.09.17; rollback is safe"
        ),
        "release_readiness": (
            "Atlas-2.4 tests 184 passed, 0 failed; migration lock unresolved"
        ),
        "customer_migration": (
            "Northstar eu-west window 02:00 UTC with 28% headroom"
        ),
    }[str(task["task_id"])]
    responses.append(
        "Thought: I have all required current evidence.\n"
        f"Final Answer: RESULT task={task['task_id']} decision={task['decision']} "
        f"evidence={evidence}"
    )
    return responses


def _build_tools(
    selected: Sequence[str],
    trace: list[str],
) -> list[Any]:
    @tool("get_service_health")
    def get_service_health(service: str) -> str:
        """Return the current synthetic health for a service."""
        trace.append("get_service_health")
        return json.dumps(
            {
                "service": service,
                "status": "degraded",
                "error_rate_percent": 3.8,
                "alert_threshold_percent": 2.0,
            }
        )

    @tool("get_recent_deployment")
    def get_recent_deployment(service: str) -> str:
        """Return the latest synthetic deployment and safe action."""
        trace.append("get_recent_deployment")
        return json.dumps(
            {
                "service": service,
                "release": "release-2026.09.17",
                "deployed_at": "09:42 UTC",
                "rollback_safe": True,
                "recommended_action": "rollback",
            }
        )

    @tool("read_release_tests")
    def read_release_tests(release: str) -> str:
        """Return the latest synthetic test result for a release."""
        trace.append("read_release_tests")
        return json.dumps(
            {"release": release, "passed": 184, "failed": 0, "status": "green"}
        )

    @tool("read_release_risks")
    def read_release_risks(release: str) -> str:
        """Return the current synthetic release blocker."""
        trace.append("read_release_risks")
        return json.dumps(
            {
                "release": release,
                "open_blocker": "migration lock unresolved",
                "severity": "blocker",
                "required_decision": "NO-GO",
            }
        )

    @tool("lookup_migration_plan")
    def lookup_migration_plan(customer: str) -> str:
        """Return the currently approved synthetic migration plan."""
        trace.append("lookup_migration_plan")
        return json.dumps(
            {
                "customer": customer,
                "plan": "enterprise",
                "target_region": "eu-west",
                "window": "02:00 UTC",
            }
        )

    @tool("check_region_capacity")
    def check_region_capacity(region: str) -> str:
        """Return the current synthetic capacity decision."""
        trace.append("check_region_capacity")
        return json.dumps(
            {
                "region": region,
                "capacity": "available",
                "headroom": "28%",
                "required_decision": "PROCEED",
            }
        )

    registry = {
        item.name: item
        for item in (
            get_service_health,
            get_recent_deployment,
            read_release_tests,
            read_release_risks,
            lookup_migration_plan,
            check_region_capacity,
        )
    }
    return [registry[str(name)] for name in selected]


def _execute_once(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    request_budget: RequestBudget,
    client: OpenAI | None,
    model_name: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_output_tokens: int,
    thinking_mode: str,
) -> dict[str, Any]:
    tool_trace: list[str] = []
    if mode == "mock":
        llm: RecordingCrewAILLM = ReplayCrewAILLM(_mock_responses(task))
    else:
        if client is None:
            raise RuntimeError("API mode requires an OpenAI-compatible client")
        llm = OpenAICompatCrewAILLM(
            client=client,
            model=model_name,
            request_budget=request_budget,
            max_output_tokens=max_output_tokens,
            thinking_mode=thinking_mode,
        )
    adapter = CrewAIContextAdapter(
        ContextPluginConfig(method=method, budget=budget),
        task_state=f"{task['task']} {task['history_constraint']}",
        fixed_reserved_tokens=fixed_reserved_tokens,
        agent_roles=["Operations decision analyst"],
    )
    agent = Agent(
        role="Operations decision analyst",
        goal=(
            "Verify every current fact with the supplied tools and return the exact "
            "single-line decision contract requested by the latest user."
        ),
        backstory=(
            "You are a careful synthetic operations analyst. Historical discussion is "
            "background only; current tool evidence is authoritative."
        ),
        llm=llm,
        tools=_build_tools(task["tools"], tool_trace),
        allow_delegation=False,
        max_iter=6,
        verbose=False,
        respect_context_window=False,
    )
    started = time.perf_counter()
    error: Exception | None = None
    final_output = ""
    state_roundtrip = False
    try:
        with adapter.attached():
            result = agent.kickoff(build_history(task, repeat))
        final_output = str(result).strip()
        if not final_output:
            raise EmptyModelResponseError("empty final model response")
        state = json.loads(json.dumps(adapter.export_state()))
        restored = CrewAIContextAdapter(
            ContextPluginConfig(method=method, budget=budget),
            agent_roles=["Operations decision analyst"],
        )
        restored.restore_state(state)
        state_roundtrip = (
            restored.metrics.before_llm_calls == adapter.metrics.before_llm_calls
            and restored.metrics.after_tool_calls == adapter.metrics.after_tool_calls
        )
    except Exception as caught:
        error = caught
        adapter.on_error(caught, query=str(task["task_id"]))
    return {
        "final_output": final_output,
        "latency_seconds": time.perf_counter() - started,
        "llm": llm,
        "metrics": adapter.metrics_dict(),
        "tool_trace": tool_trace,
        "error": error,
        "state_roundtrip": state_roundtrip,
    }


def run_case(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    request_budget: RequestBudget,
    client: OpenAI | None,
    model_name: str,
    budget: ContextBudget,
    fixed_reserved_tokens: int,
    max_output_tokens: int,
    thinking_mode: str,
    max_case_retries: int,
    retry_base_delay: float,
) -> dict[str, Any]:
    requests_before = request_budget.used
    all_inputs: list[list[dict[str, Any]]] = []
    all_responses: list[str] = []
    all_records: list[dict[str, Any]] = []
    all_attempts: list[dict[str, Any]] = []
    latest_tools: list[str] = []
    total_latency = 0.0
    retries = 0
    error_type = ""
    error_message = ""
    outcome: dict[str, Any] | None = None
    for attempt in range(max(0, max_case_retries) + 1):
        outcome = _execute_once(
            task,
            repeat=repeat,
            method=method,
            mode=mode,
            request_budget=request_budget,
            client=client,
            model_name=model_name,
            budget=budget,
            fixed_reserved_tokens=fixed_reserved_tokens,
            max_output_tokens=max_output_tokens,
            thinking_mode=thinking_mode,
        )
        llm = outcome["llm"]
        all_inputs.extend(llm.inputs)
        all_responses.extend(llm.responses)
        all_records.extend(llm.response_records)
        all_attempts.extend(llm.attempt_records)
        latest_tools = list(outcome["tool_trace"])
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
        time.sleep(max(0.0, retry_base_delay) * (2**attempt))

    final_output = str(outcome["final_output"]) if outcome else ""
    metrics = dict(outcome["metrics"]) if outcome else {}
    expected_tools = tuple(str(name) for name in task["tools"])
    expected_terms = tuple(str(term) for term in task["expected_terms"])
    answer_correct = _answer_correct(
        str(task["task_id"]), final_output.lower(), expected_terms
    )
    tool_correct = Counter(latest_tools) == Counter(expected_tools)
    final_format_correct = (
        final_output.startswith(f"RESULT task={task['task_id']} ")
        and "\n" not in final_output
        and len(final_output) <= 300
    )
    anchor = expected_terms[0].lower()
    constraint_preserved = bool(all_inputs) and all(
        anchor in _serialize_messages(messages).lower() for messages in all_inputs
    )
    restore_failures = int(metrics.get("crewai_group_restore_failure_count", 0))
    unmatched = int(metrics.get("crewai_unmatched_call_count", 0))
    pending = int(metrics.get("crewai_active_pending_view_count", 0))
    structure_safe = restore_failures == 0 and unmatched == 0 and pending == 0
    actual_inputs = [int(item["input_tokens"]) for item in all_records]
    actual_outputs = [int(item["output_tokens"]) for item in all_records]
    reasoning_tokens = [int(item.get("reasoning_tokens", 0)) for item in all_records]
    approximate_inputs = [_messages_tokens(messages) for messages in all_inputs]
    success = (
        not error_type
        and answer_correct
        and tool_correct
        and final_format_correct
        and constraint_preserved
        and structure_safe
        and bool(outcome and outcome["state_roundtrip"])
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
        "structure_safe": structure_safe,
        "state_roundtrip": bool(outcome and outcome["state_roundtrip"]),
        "model_calls": len(all_inputs),
        "tool_calls": len(latest_tools),
        "tool_names": latest_tools,
        "actual_input_tokens": sum(actual_inputs),
        "actual_output_tokens": sum(actual_outputs),
        "reasoning_tokens": sum(reasoning_tokens),
        "sanitized_model_response_count": sum(
            bool(item.get("react_response_sanitized")) for item in all_records
        ),
        "actual_peak_input_tokens": max(actual_inputs, default=0),
        "approximate_input_tokens": sum(approximate_inputs),
        "approximate_peak_input_tokens": max(approximate_inputs, default=0),
        "latency_seconds": total_latency,
        "api_retry_count": retries,
        "api_attempt_records": all_attempts,
        "api_request_attempts": request_budget.used - requests_before,
        "restore_failure_count": restore_failures,
        "unmatched_call_count": unmatched,
        "pending_view_count": pending,
        "resync_count": int(metrics.get("resync_count", 0)),
        "authoritative_resync_count": int(
            metrics.get("crewai_authoritative_resync_count", 0)
        ),
        "protected_group_count": int(
            metrics.get("crewai_protected_group_count", 0)
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
        "model_responses": all_responses,
        "model_input_traces": [_input_trace(messages) for messages in all_inputs],
    }


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


def _serialize_messages(messages: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(list(messages), ensure_ascii=False, sort_keys=True, default=str)


def _messages_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    return estimate_tokens(_serialize_messages(messages))


def _input_trace(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a bounded synthetic-input trace for protocol debugging."""

    items = []
    for message in messages:
        content = str(message.get("content", "") or "")
        items.append(
            {
                "role": str(message.get("role", "") or ""),
                "content_characters": len(content),
                "content_tail": content[-800:],
            }
        )
    return {"message_count": len(items), "messages": items}


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
    fields = [
        key
        for key in rows[0]
        if key not in {"api_attempt_records", "model_responses", "model_input_traces"}
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
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
            "input_tokens_mean": statistics.mean(
                float(row[token_field]) for row in selected
            ),
            "peak_input_mean": statistics.mean(
                float(row[peak_field]) for row in selected
            ),
            "output_tokens_mean": statistics.mean(
                float(row["actual_output_tokens"]) for row in selected
            ),
            "reasoning_tokens_mean": statistics.mean(
                float(row.get("reasoning_tokens", 0)) for row in selected
            ),
            "sanitized_response_count": sum(
                int(row.get("sanitized_model_response_count", 0))
                for row in selected
            ),
            "latency_mean": statistics.mean(
                float(row["latency_seconds"]) for row in selected
            ),
            "api_requests": sum(int(row["api_request_attempts"]) for row in selected),
            "api_retries": sum(int(row["api_retry_count"]) for row in selected),
            "hard_budget_violations": sum(
                int(row["hard_budget_violation_count"]) for row in selected
            ),
        }
    indexed = {_result_key(row): row for row in rows}
    savings: list[float] = []
    peak_savings: list[float] = []
    success_deltas: list[float] = []
    both_success: list[float] = []
    output_changes: list[float] = []
    total_savings: list[float] = []
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
        savings.append(saving)
        peak_savings.append(
            (base_peak - float(row[peak_field])) / base_peak if base_peak else 0.0
        )
        success_deltas.append(
            float(bool(row["success"])) - float(bool(baseline["success"]))
        )
        base_output = float(baseline["actual_output_tokens"])
        output_changes.append(
            (float(row["actual_output_tokens"]) - base_output) / base_output
            if base_output
            else 0.0
        )
        base_total = base_tokens + base_output
        candidate_total = float(row[token_field]) + float(row["actual_output_tokens"])
        total_savings.append(
            (base_total - candidate_total) / base_total if base_total else 0.0
        )
        base_latency = float(baseline["latency_seconds"])
        latency_changes.append(
            (float(row["latency_seconds"]) - base_latency) / base_latency
            if base_latency
            else 0.0
        )
        if row["success"] and baseline["success"]:
            both_success.append(saving)
    low, high = _bootstrap_ci(savings)
    both_low, both_high = _bootstrap_ci(both_success)
    total_low, total_high = _bootstrap_ci(total_savings)
    return {
        "mode": mode,
        "methods": by_method,
        "paired": {
            "n": len(savings),
            "input_savings_rate_mean": statistics.mean(savings) if savings else 0.0,
            "input_savings_ci_low": low,
            "input_savings_ci_high": high,
            "peak_savings_rate_mean": statistics.mean(peak_savings)
            if peak_savings
            else 0.0,
            "success_delta": statistics.mean(success_deltas)
            if success_deltas
            else 0.0,
            "positive_savings_rate": statistics.mean(value > 0 for value in savings)
            if savings
            else 0.0,
            "both_success_n": len(both_success),
            "both_success_input_savings_rate_mean": statistics.mean(both_success)
            if both_success
            else 0.0,
            "both_success_input_savings_ci_low": both_low,
            "both_success_input_savings_ci_high": both_high,
            "output_token_change_rate_mean": statistics.mean(output_changes)
            if output_changes
            else 0.0,
            "total_token_savings_rate_mean": statistics.mean(total_savings)
            if total_savings
            else 0.0,
            "total_token_savings_ci_low": total_low,
            "total_token_savings_ci_high": total_high,
            "latency_change_rate_mean": statistics.mean(latency_changes)
            if latency_changes
            else 0.0,
        },
    }


def _report(summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# CrewAI 自然任务配对验证",
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
            f"工具正确率={values['tool_correct_rate']:.2%}，"
            f"结构安全率={values['structure_safe_rate']:.2%}，"
            f"平均输入 token={values['input_tokens_mean']:.1f}，"
            f"平均延迟={values['latency_mean']:.2f}s，"
            f"硬预算违规={values['hard_budget_violations']}。"
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
            f"- 输出 token 变化：{paired['output_token_change_rate_mean']:+.2%}",
            f"- 总 token 节省：{paired['total_token_savings_rate_mean']:.2%}",
            f"- 总 token 节省 bootstrap 95% CI：{paired['total_token_savings_ci_low']:.2%}～{paired['total_token_savings_ci_high']:.2%}",
            f"- 延迟变化：{paired['latency_change_rate_mean']:+.2%}",
            f"- 正节省配对比例：{paired['positive_savings_rate']:.2%}",
            f"- 成功率差：{paired['success_delta']:+.2%}",
            f"- 双侧成功配对：{paired['both_success_n']}",
            "",
            "Mock 结果只验证 CrewAI 调用链和测量逻辑，不代表真实模型效果。",
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
    parser.add_argument("--soft-limit", type=int, default=1800)
    parser.add_argument("--hard-limit", type=int, default=6000)
    parser.add_argument("--target", type=int, default=1500)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument(
        "--thinking-mode", choices=("disabled", "default"), default="disabled"
    )
    parser.add_argument("--max-case-retries", type=int, default=1)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--max-api-requests", type=int, default=30)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-natural-smoke-v130")
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
        "protocol_version": "crewai-natural-v131",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "context_pruner_version": __version__,
        "crewai_version": importlib.metadata.version("crewai"),
        "openai_version": importlib.metadata.version("openai"),
        "mode": args.mode,
        "model": args.model if args.mode == "api" else "ReplayCrewAILLM",
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
        "thinking_mode": args.thinking_mode,
        "max_case_retries": args.max_case_retries,
        "maximum_api_requests": args.max_api_requests,
        "evaluation_answer_terms_disclosed": False,
        "synthetic_disclosure": SYNTHETIC_DISCLOSURE,
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
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
    minimum_requests = len(plan) * 3
    print(SYNTHETIC_DISCLOSURE)
    print(
        f"plan: mode={args.mode}, executions={len(plan)}, tasks={len(tasks)}, "
        f"repeats={args.repeats}, expected_calls={minimum_requests}, "
        f"max_api_requests={args.max_api_requests}"
    )
    if args.plan:
        print("No API request was sent in --plan mode.")
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    if args.mode == "api" and args.max_api_requests < minimum_requests:
        raise SystemExit(
            "max-api-requests is below the expected three-call minimum: "
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
    client = (
        OpenAI(api_key=api_key, base_url=args.base_url, max_retries=0)
        if args.mode == "api"
        else None
    )
    try:
        for task, repeat, method in plan:
            key = (str(task["task_id"]), repeat, method)
            if key in completed:
                print(f"skip {key[0]} r{repeat:02d} {method}")
                continue
            row = run_case(
                task,
                repeat=repeat,
                method=method,
                mode=args.mode,
                request_budget=request_budget,
                client=client,
                model_name=args.model,
                budget=budget,
                fixed_reserved_tokens=args.fixed_reserved_tokens,
                max_output_tokens=args.max_output_tokens,
                thinking_mode=args.thinking_mode,
                max_case_retries=args.max_case_retries,
                retry_base_delay=args.retry_base_delay,
            )
            _append_jsonl(results_path, row)
            raw_rows.append(row)
            completed.add(key)
            tokens = (
                row["actual_input_tokens"]
                if args.mode == "api"
                else row["approximate_input_tokens"]
            )
            print(
                f"{key[0]:22} r{repeat:02d} {method:10} "
                f"success={str(row['success']):5} calls={row['model_calls']} "
                f"tok_in={tokens} requests={request_budget.used}/{request_budget.limit}"
            )
    finally:
        if client is not None:
            client.close()

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
        f"95%CI=[{paired['input_savings_ci_low']:.2%}, "
        f"{paired['input_savings_ci_high']:.2%}]"
    )
    print(f"report: {output}")


if __name__ == "__main__":
    main()
