"""Paired AutoGen team validation with scoped context and state recovery.

Mock mode is fully local. API mode sends only the synthetic task file content,
requires an explicit confirmation flag, and enforces a global request-attempt cap.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import random
import re
import statistics
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import ContextBudget, ContextPluginConfig, __version__
from context_pruner.adapters import AutoGenTeamContextCoordinator
from context_pruner.types import estimate_tokens
from experiments.runners.run_autogen_natural_experiment import (
    RecordingClient,
    RequestBudget,
    SYNTHETIC_DISCLOSURE,
)

try:
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core.models import (
        AssistantMessage,
        ChatCompletionClient,
        ModelFamily,
        UserMessage,
    )
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_ext.models.replay import ReplayChatCompletionClient
except ImportError as error:  # pragma: no cover - optional dependency path
    raise SystemExit(
        'AutoGen is required. Install: pip install -e ".[autogen]"'
    ) from error


METHODS = ("none", "pruner_v1")
PROMPT_PROTOCOL = "task_facts_plus_field_schema_no_expected_terms"


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("team task file must contain a non-empty list")
    required = {
        "task_id",
        "title",
        "shared_context",
        "planner_private",
        "executor_private",
        "planner_task",
        "executor_task",
        "planner_output_schema",
        "executor_output_schema",
        "mock_planner_output",
        "mock_executor_output",
        "expected_handoff_terms",
        "expected_final_terms",
        "planner_private_canary",
        "executor_private_canary",
        "history_topic",
    }
    seen: set[str] = set()
    for task in tasks:
        missing = sorted(required - set(task))
        if missing:
            raise ValueError(f"team task missing fields: {missing}")
        task_id = str(task["task_id"])
        if task_id in seen:
            raise ValueError(f"duplicate team task ID: {task_id}")
        seen.add(task_id)
        if not task["expected_handoff_terms"] or not task["expected_final_terms"]:
            raise ValueError(f"team task {task_id} requires expected terms")
        for role, prefix in (("planner", "HANDOFF"), ("executor", "RESULT")):
            schema = str(task[f"{role}_output_schema"])
            if not schema.startswith(f"{prefix} ") or "\n" in schema:
                raise ValueError(
                    f"team task {task_id} has invalid {role} output schema"
                )
    return tasks


def _output_contract(schema: Any, *, max_chars: int) -> str:
    return (
        f"Output exactly one ASCII line of at most {max_chars} characters. "
        f"Use this exact prefix and field order: {schema}. "
        "Replace every angle-bracket placeholder with a value. Do not add prose, "
        "Markdown, explanations, private-note markers, or historical background."
    )


def build_private_history(
    task: Mapping[str, Any],
    *,
    agent_role: str,
    repeat: int,
) -> list[Any]:
    topic = str(task["history_topic"])
    messages: list[Any] = [
        UserMessage(
            content=(
                f"This is the private {agent_role} work log for {task['task_id']}. "
                "Old entries are background and never override current scoped events."
            ),
            source="user",
        )
    ]
    for index in range(10 + repeat):
        messages.extend(
            [
                AssistantMessage(
                    content=(
                        f"{agent_role} archive {index + 1}: reviewed {topic}; "
                        "the observations were provisional and may now be obsolete. "
                        + "background detail " * 12
                    ),
                    source=agent_role,
                ),
                UserMessage(
                    content=(
                        f"Historical update {index + 1} for {agent_role}: retain provenance, "
                        "but use only the latest shared constraint and explicit handoff. "
                        + "routine note " * 10
                    ),
                    source="user",
                ),
            ]
        )
    return messages


def _model_client(
    *,
    mode: str,
    response: str,
    model_name: str,
    base_url: str,
    api_key: str,
    max_output_tokens: int,
    thinking_mode: str,
) -> ChatCompletionClient:
    if mode == "mock":
        return ReplayChatCompletionClient(
            [response],
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "family": ModelFamily.UNKNOWN,
                "structured_output": False,
            },
        )
    client_args: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": 0,
        "max_tokens": max_output_tokens,
        "max_retries": 0,
        "model_info": {
            "vision": False,
            "function_calling": True,
            "json_output": False,
            "family": "unknown",
            "structured_output": False,
            "multiple_system_messages": False,
        },
    }
    if thinking_mode == "disabled":
        client_args["extra_body"] = {"thinking": {"type": "disabled"}}
    return OpenAIChatCompletionClient(**client_args)


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
    max_visible_output_chars: int,
    thinking_mode: str,
    max_api_retries: int,
    retry_base_delay: float,
) -> dict[str, Any]:
    requests_before = request_budget.used
    coordinator = AutoGenTeamContextCoordinator(
        ContextPluginConfig(method=method, budget=budget),
        team_id=f"team-{task['task_id']}-r{repeat:02d}",
        task_state=(
            f"{task['shared_context']} Route private notes only to their owner and "
            "handoffs only to their target."
        ),
        fixed_reserved_tokens=fixed_reserved_tokens,
    )
    planner_context = coordinator.register_agent(
        "planner",
        initial_messages=build_private_history(
            task, agent_role="planner", repeat=repeat
        ),
    )
    coordinator.register_agent(
        "executor",
        initial_messages=build_private_history(
            task, agent_role="executor", repeat=repeat
        ),
    )
    await coordinator.publish_private("planner", str(task["planner_private"]))
    await coordinator.publish_private("executor", str(task["executor_private"]))
    await coordinator.publish_shared("planner", str(task["shared_context"]))

    planner_canary = str(task["planner_private_canary"])
    executor_canary = str(task["executor_private_canary"])
    planner_before = _serialize_messages(planner_context._messages)
    executor_before = _serialize_messages(
        coordinator.get_context("executor")._messages
    )
    pre_handoff_routing_isolation = (
        planner_canary in planner_before
        and executor_canary not in planner_before
        and executor_canary in executor_before
        and planner_canary not in executor_before
    )

    planner_client = RecordingClient(
        _model_client(
            mode=mode,
            response=str(task["mock_planner_output"]),
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            max_output_tokens=max_output_tokens,
            thinking_mode=thinking_mode,
        ),
        request_budget,
        api_mode=mode == "api",
        max_retries=max_api_retries,
        retry_base_delay=retry_base_delay,
    )
    planner = AssistantAgent(
        "planner",
        planner_client,
        model_context=planner_context,
        system_message=(
            "You are the planning member of a two-Agent team. Use the latest shared "
            "constraint and current task. Include every operational fact requested by "
            "the task. "
            + _output_contract(
                task["planner_output_schema"], max_chars=max_visible_output_chars
            )
        ),
    )

    started = time.perf_counter()
    error_type = ""
    error_message = ""
    planner_output = ""
    executor_output = ""
    executor_client: RecordingClient | None = None
    state_roundtrip_ok = False
    restored = coordinator
    handoff_route_marker = ""
    handoff_target_only = False
    planner_latency = 0.0
    coordination_latency = 0.0
    executor_latency = 0.0
    try:
        planner_started = time.perf_counter()
        try:
            planner_result = await planner.run(task=str(task["planner_task"]))
        finally:
            planner_latency = time.perf_counter() - planner_started
        if planner_result.messages:
            planner_output = str(
                getattr(planner_result.messages[-1], "content", "") or ""
            )

        coordination_started = time.perf_counter()
        try:
            saved_state = json.loads(json.dumps(await coordinator.save_state()))
            expected_lengths = {
                agent_id: len(coordinator.get_context(agent_id)._messages)
                for agent_id in coordinator.agent_ids
            }
            restored = await AutoGenTeamContextCoordinator.from_state(
                saved_state,
                config=ContextPluginConfig(method=method, budget=budget),
            )
            state_roundtrip_ok = expected_lengths == {
                agent_id: len(restored.get_context(agent_id)._messages)
                for agent_id in restored.agent_ids
            }
            handoff_event = await restored.publish_handoff(
                "planner", "executor", planner_output
            )
            handoff_route_marker = f"event={handoff_event.event_id}"
            planner_after_handoff = _serialize_messages(
                restored.get_context("planner")._messages
            )
            executor_after_handoff = _serialize_messages(
                restored.get_context("executor")._messages
            )
            handoff_target_only = (
                handoff_route_marker not in planner_after_handoff
                and handoff_route_marker in executor_after_handoff
            )
        finally:
            coordination_latency = time.perf_counter() - coordination_started

        executor_client = RecordingClient(
            _model_client(
                mode=mode,
                response=str(task["mock_executor_output"]),
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                max_output_tokens=max_output_tokens,
                thinking_mode=thinking_mode,
            ),
            request_budget,
            api_mode=mode == "api",
            max_retries=max_api_retries,
            retry_base_delay=retry_base_delay,
        )
        executor = AssistantAgent(
            "executor",
            executor_client,
            model_context=restored.get_context("executor"),
            system_message=(
                "You are the execution member of a two-Agent team. Use only the shared "
                "constraint and explicit HANDOFF as cross-Agent evidence. Include every "
                "field requested by the task. "
                + _output_contract(
                    task["executor_output_schema"],
                    max_chars=max_visible_output_chars,
                )
            ),
        )
        executor_started = time.perf_counter()
        try:
            executor_result = await executor.run(task=str(task["executor_task"]))
        finally:
            executor_latency = time.perf_counter() - executor_started
        if executor_result.messages:
            executor_output = str(
                getattr(executor_result.messages[-1], "content", "") or ""
            )
    except Exception as error:  # Persist failures for deterministic resume/retry.
        error_type = type(error).__name__
        error_message = str(error)[:500]
    latency = time.perf_counter() - started

    planner_inputs = planner_client.inputs
    executor_inputs = executor_client.inputs if executor_client is not None else []
    planner_serialized = "\n".join(_serialize_messages(value) for value in planner_inputs)
    executor_serialized = "\n".join(_serialize_messages(value) for value in executor_inputs)
    shared = str(task["shared_context"])
    shared_visible = (
        bool(planner_inputs)
        and bool(executor_inputs)
        and shared.lower() in planner_serialized.lower()
        and shared.lower() in executor_serialized.lower()
    )
    handoff_preserved = bool(executor_inputs) and _contains_terms(
        executor_serialized,
        task["expected_handoff_terms"],
    )
    planner_private_isolated = planner_canary.lower() not in executor_serialized.lower()
    executor_private_isolated = executor_canary.lower() not in planner_serialized.lower()
    model_input_privacy = planner_private_isolated and executor_private_isolated
    handoff_correct = _contains_terms(
        planner_output,
        task["expected_handoff_terms"],
    )
    final_correct = _contains_terms(
        executor_output,
        task["expected_final_terms"],
    )
    format_correct = _valid_prefixed_line(planner_output, "HANDOFF") and _valid_prefixed_line(
        executor_output, "RESULT"
    )
    output_length_compliant = (
        len(planner_output) <= max_visible_output_chars
        and len(executor_output) <= max_visible_output_chars
    )
    ascii_output = planner_output.isascii() and executor_output.isascii()
    output_contract_correct = (
        format_correct and output_length_compliant and ascii_output
    )

    clients = [planner_client] + ([executor_client] if executor_client else [])
    responses = [response for client in clients for response in client.responses]
    all_inputs = [messages for client in clients for messages in client.inputs]
    provider_inputs = [int(response.usage.prompt_tokens) for response in responses]
    provider_outputs = [int(response.usage.completion_tokens) for response in responses]
    approximate_inputs = [_messages_tokens(messages) for messages in all_inputs]
    context_metrics = [
        restored.get_context(agent_id).metrics_dict()
        for agent_id in restored.agent_ids
    ]
    hard_budget_violations = sum(
        int(metrics.get("model_input_budget_violation_count", 0))
        for metrics in context_metrics
    )
    resync_count = sum(
        int(metrics.get("resync_count", 0)) for metrics in context_metrics
    )
    restore_failures = sum(
        int(metrics.get("autogen_group_restore_failure_count", 0))
        for metrics in context_metrics
    )
    success = (
        not error_type
        and pre_handoff_routing_isolation
        and state_roundtrip_ok
        and shared_visible
        and handoff_correct
        and handoff_preserved
        and handoff_target_only
        and model_input_privacy
        and final_correct
        and output_contract_correct
        and restore_failures == 0
        and resync_count == 0
    )
    retries = sum(client.retry_count for client in clients)
    empty_responses = sum(client.empty_response_count for client in clients)
    planner_usage = _role_usage(planner_client, planner_output)
    executor_usage = _role_usage(executor_client, executor_output)
    execution_complete = not error_type and len(responses) == 2
    for client in clients:
        await client.close()
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "repeat": repeat,
        "method": method,
        "mode": mode,
        "prompt_protocol": PROMPT_PROTOCOL,
        "evaluation_answer_terms_disclosed": False,
        "thinking_mode": thinking_mode,
        "success": success,
        "execution_complete": execution_complete,
        "pre_handoff_routing_isolation": pre_handoff_routing_isolation,
        "state_roundtrip_ok": state_roundtrip_ok,
        "shared_visible": shared_visible,
        "handoff_correct": handoff_correct,
        "handoff_preserved": handoff_preserved,
        "handoff_target_only": handoff_target_only,
        "handoff_route_marker": handoff_route_marker,
        "model_input_privacy": model_input_privacy,
        "planner_private_isolated": planner_private_isolated,
        "executor_private_isolated": executor_private_isolated,
        "final_correct": final_correct,
        "format_correct": format_correct,
        "output_length_compliant": output_length_compliant,
        "ascii_output": ascii_output,
        "output_contract_correct": output_contract_correct,
        "model_calls": len(responses),
        "actual_input_tokens": sum(provider_inputs),
        "actual_output_tokens": sum(provider_outputs),
        "actual_peak_input_tokens": max(provider_inputs, default=0),
        "approximate_input_tokens": sum(approximate_inputs),
        "approximate_peak_input_tokens": max(approximate_inputs, default=0),
        "latency_seconds": latency,
        "planner_latency_seconds": planner_latency,
        "coordination_latency_seconds": coordination_latency,
        "executor_latency_seconds": executor_latency,
        "unattributed_latency_seconds": max(
            0.0,
            latency - planner_latency - coordination_latency - executor_latency,
        ),
        "planner_usage": planner_usage,
        "executor_usage": executor_usage,
        "api_attempt_records": {
            "planner": planner_client.attempt_records,
            "executor": executor_client.attempt_records
            if executor_client is not None
            else [],
        },
        "api_retry_count": retries,
        "empty_response_count": empty_responses,
        "api_request_attempts": request_budget.used - requests_before,
        "hard_budget_violation_count": hard_budget_violations,
        "resync_count": resync_count,
        "restore_failure_count": restore_failures,
        "team_metrics": restored.metrics_dict(),
        "planner_output": planner_output,
        "final_output": executor_output,
        "error_type": error_type,
        "error_message": error_message,
    }


def _role_usage(
    client: RecordingClient | None,
    visible_output: str,
) -> dict[str, Any]:
    responses = client.responses if client is not None else []
    attempts = client.attempt_records if client is not None else []
    inputs = [int(response.usage.prompt_tokens) for response in responses]
    outputs = [int(response.usage.completion_tokens) for response in responses]
    thoughts = [str(response.thought or "") for response in responses]
    visible_tokens = estimate_tokens(visible_output)
    provider_output_tokens = sum(outputs)
    return {
        "response_count": len(responses),
        "attempt_count": len(attempts),
        "empty_attempt_count": sum(
            record.get("status") == "empty" for record in attempts
        ),
        "attempt_prompt_tokens": sum(
            int(record.get("prompt_tokens", 0)) for record in attempts
        ),
        "attempt_completion_tokens": sum(
            int(record.get("completion_tokens", 0)) for record in attempts
        ),
        "wasted_completion_tokens": sum(
            int(record.get("completion_tokens", 0))
            for record in attempts
            if record.get("status") != "success"
        ),
        "provider_input_tokens": sum(inputs),
        "provider_output_tokens": provider_output_tokens,
        "provider_peak_input_tokens": max(inputs, default=0),
        "visible_output_chars": len(visible_output),
        "visible_output_tokens_estimate": visible_tokens,
        "reasoning_chars": sum(len(value) for value in thoughts),
        "reasoning_tokens_estimate": sum(estimate_tokens(value) for value in thoughts),
        "non_visible_completion_tokens_estimate": max(
            0, provider_output_tokens - visible_tokens
        ),
        "finish_reasons": [str(response.finish_reason) for response in responses],
    }


def summarize(rows: Sequence[Mapping[str, Any]], mode: str) -> dict[str, Any]:
    latest = _latest_rows(rows)
    thinking_modes = {
        str(row.get("thinking_mode", "default")) for row in latest
    }
    thinking_mode = (
        next(iter(thinking_modes)) if len(thinking_modes) == 1 else "mixed"
    )
    token_field = "actual_input_tokens" if mode == "api" else "approximate_input_tokens"
    peak_field = (
        "actual_peak_input_tokens" if mode == "api" else "approximate_peak_input_tokens"
    )
    methods: dict[str, Any] = {}
    boolean_metrics = (
        "execution_complete",
        "success",
        "pre_handoff_routing_isolation",
        "state_roundtrip_ok",
        "shared_visible",
        "handoff_preserved",
        "handoff_target_only",
        "model_input_privacy",
        "handoff_correct",
        "final_correct",
        "output_contract_correct",
    )
    for method in METHODS:
        selected = [row for row in latest if row["method"] == method]
        if not selected:
            continue
        completed = [row for row in selected if _execution_complete(row)]

        def completed_mean(values: Sequence[float]) -> float:
            return statistics.fmean(values) if values else 0.0

        methods[method] = {
            "n": len(selected),
            "completed_n": len(completed),
            **{
                f"{key}_rate": statistics.fmean(
                    _boolean_metric(row, key) for row in selected
                )
                for key in boolean_metrics
            },
            "input_tokens_mean": completed_mean(
                [float(row[token_field]) for row in completed]
            ),
            "peak_input_mean": completed_mean(
                [float(row[peak_field]) for row in completed]
            ),
            "output_tokens_mean": completed_mean(
                [float(row["actual_output_tokens"]) for row in completed]
            ),
            "total_tokens_mean": completed_mean(
                [
                    float(row[token_field]) + float(row["actual_output_tokens"])
                    for row in completed
                ]
            ),
            "latency_mean": completed_mean(
                [float(row["latency_seconds"]) for row in completed]
            ),
            "planner_latency_mean": completed_mean(
                [
                    float(row.get("planner_latency_seconds", 0.0))
                    for row in completed
                ]
            ),
            "coordination_latency_mean": completed_mean(
                [
                    float(row.get("coordination_latency_seconds", 0.0))
                    for row in completed
                ]
            ),
            "executor_latency_mean": completed_mean(
                [
                    float(row.get("executor_latency_seconds", 0.0))
                    for row in completed
                ]
            ),
            "planner_input_tokens_mean": completed_mean(
                [
                    _role_number(row, "planner_usage", "provider_input_tokens")
                    for row in completed
                ]
            ),
            "executor_input_tokens_mean": completed_mean(
                [
                    _role_number(row, "executor_usage", "provider_input_tokens")
                    for row in completed
                ]
            ),
            "planner_output_tokens_mean": completed_mean(
                [
                    _role_number(row, "planner_usage", "provider_output_tokens")
                    for row in completed
                ]
            ),
            "executor_output_tokens_mean": completed_mean(
                [
                    _role_number(row, "executor_usage", "provider_output_tokens")
                    for row in completed
                ]
            ),
            "planner_reasoning_tokens_estimate_mean": completed_mean(
                [
                    _role_number(row, "planner_usage", "reasoning_tokens_estimate")
                    for row in completed
                ]
            ),
            "executor_reasoning_tokens_estimate_mean": completed_mean(
                [
                    _role_number(row, "executor_usage", "reasoning_tokens_estimate")
                    for row in completed
                ]
            ),
            "non_visible_completion_tokens_estimate_mean": completed_mean(
                [
                    _role_number(
                        row,
                        "planner_usage",
                        "non_visible_completion_tokens_estimate",
                    )
                    + _role_number(
                        row,
                        "executor_usage",
                        "non_visible_completion_tokens_estimate",
                    )
                    for row in completed
                ]
            ),
            "reasoning_tokens_estimate_mean": completed_mean(
                [
                    _role_number(row, "planner_usage", "reasoning_tokens_estimate")
                    + _role_number(
                        row, "executor_usage", "reasoning_tokens_estimate"
                    )
                    for row in completed
                ]
            ),
            "api_requests": sum(int(row["api_request_attempts"]) for row in selected),
            "api_retries": sum(int(row["api_retry_count"]) for row in selected),
            "hard_budget_violations": sum(
                int(row["hard_budget_violation_count"]) for row in selected
            ),
        }
    indexed = {_result_key(row): row for row in latest}
    savings: list[float] = []
    peak_savings: list[float] = []
    total_savings: list[float] = []
    output_changes: list[float] = []
    latency_changes: list[float] = []
    planner_output_changes: list[float] = []
    executor_output_changes: list[float] = []
    planner_latency_changes: list[float] = []
    executor_latency_changes: list[float] = []
    reasoning_estimate_changes: list[float] = []
    success_deltas: list[float] = []
    matched_pairs = 0
    incomplete_pairs = 0
    for row in latest:
        if row["method"] != "pruner_v1":
            continue
        baseline = indexed.get((str(row["task_id"]), int(row["repeat"]), "none"))
        if baseline is None:
            continue
        matched_pairs += 1
        if not (_execution_complete(row) and _execution_complete(baseline)):
            incomplete_pairs += 1
            continue
        baseline_tokens = float(baseline[token_field])
        baseline_peak = float(baseline[peak_field])
        savings.append(
            (baseline_tokens - float(row[token_field])) / baseline_tokens
            if baseline_tokens
            else 0.0
        )
        peak_savings.append(
            (baseline_peak - float(row[peak_field])) / baseline_peak
            if baseline_peak
            else 0.0
        )
        baseline_total = baseline_tokens + float(baseline["actual_output_tokens"])
        pruned_total = float(row[token_field]) + float(row["actual_output_tokens"])
        total_savings.append(
            (baseline_total - pruned_total) / baseline_total
            if baseline_total
            else 0.0
        )
        baseline_output = float(baseline["actual_output_tokens"])
        output_changes.append(
            (float(row["actual_output_tokens"]) - baseline_output) / baseline_output
            if baseline_output
            else 0.0
        )
        baseline_latency = float(baseline["latency_seconds"])
        latency_changes.append(
            (float(row["latency_seconds"]) - baseline_latency) / baseline_latency
            if baseline_latency
            else 0.0
        )
        for role, output_changes_target, latency_changes_target in (
            ("planner", planner_output_changes, planner_latency_changes),
            ("executor", executor_output_changes, executor_latency_changes),
        ):
            baseline_role_output = _role_number(
                baseline, f"{role}_usage", "provider_output_tokens"
            )
            role_output = _role_number(
                row, f"{role}_usage", "provider_output_tokens"
            )
            output_changes_target.append(
                (role_output - baseline_role_output) / baseline_role_output
                if baseline_role_output
                else 0.0
            )
            baseline_role_latency = float(
                baseline.get(f"{role}_latency_seconds", 0.0)
            )
            role_latency = float(row.get(f"{role}_latency_seconds", 0.0))
            latency_changes_target.append(
                (role_latency - baseline_role_latency) / baseline_role_latency
                if baseline_role_latency
                else 0.0
            )
        baseline_reasoning = sum(
            _role_number(
                baseline, f"{role}_usage", "reasoning_tokens_estimate"
            )
            for role in ("planner", "executor")
        )
        row_reasoning = sum(
            _role_number(row, f"{role}_usage", "reasoning_tokens_estimate")
            for role in ("planner", "executor")
        )
        reasoning_estimate_changes.append(
            (row_reasoning - baseline_reasoning) / baseline_reasoning
            if baseline_reasoning
            else 0.0
        )
        success_deltas.append(float(bool(row["success"])) - float(bool(baseline["success"])))
    low, high = _bootstrap_ci(savings)
    total_low, total_high = _bootstrap_ci(total_savings)
    return {
        "version": __version__,
        "mode": mode,
        "thinking_mode": thinking_mode,
        "prompt_protocol": PROMPT_PROTOCOL,
        "evaluation_answer_terms_disclosed": False,
        "methods": methods,
        "by_task": _summarize_by_task(latest, token_field),
        "paired": {
            "n": len(savings),
            "matched_n": matched_pairs,
            "incomplete_n": incomplete_pairs,
            "input_savings_rate_mean": statistics.fmean(savings) if savings else 0.0,
            "input_savings_ci_low": low,
            "input_savings_ci_high": high,
            "peak_savings_rate_mean": statistics.fmean(peak_savings)
            if peak_savings
            else 0.0,
            "total_token_savings_rate_mean": statistics.fmean(total_savings)
            if total_savings
            else 0.0,
            "total_token_savings_ci_low": total_low,
            "total_token_savings_ci_high": total_high,
            "output_token_change_rate_mean": statistics.fmean(output_changes)
            if output_changes
            else 0.0,
            "latency_change_rate_mean": statistics.fmean(latency_changes)
            if latency_changes
            else 0.0,
            "planner_output_token_change_rate_mean": statistics.fmean(
                planner_output_changes
            )
            if planner_output_changes
            else 0.0,
            "executor_output_token_change_rate_mean": statistics.fmean(
                executor_output_changes
            )
            if executor_output_changes
            else 0.0,
            "planner_latency_change_rate_mean": statistics.fmean(
                planner_latency_changes
            )
            if planner_latency_changes
            else 0.0,
            "executor_latency_change_rate_mean": statistics.fmean(
                executor_latency_changes
            )
            if executor_latency_changes
            else 0.0,
            "reasoning_token_estimate_change_rate_mean": statistics.fmean(
                reasoning_estimate_changes
            )
            if reasoning_estimate_changes
            else 0.0,
            "positive_savings_rate": statistics.fmean(value > 0 for value in savings)
            if savings
            else 0.0,
            "success_delta": statistics.fmean(success_deltas)
            if success_deltas
            else 0.0,
        },
    }


def _summarize_by_task(
    rows: Sequence[Mapping[str, Any]], token_field: str
) -> dict[str, Any]:
    indexed = {_result_key(row): row for row in rows}
    result: dict[str, Any] = {}
    for task_id in sorted({str(row["task_id"]) for row in rows}):
        baseline_rows = [
            row
            for row in rows
            if str(row["task_id"]) == task_id and row["method"] == "none"
        ]
        plugin_rows = [
            row
            for row in rows
            if str(row["task_id"]) == task_id and row["method"] == "pruner_v1"
        ]
        input_savings: list[float] = []
        total_savings: list[float] = []
        output_changes: list[float] = []
        latency_changes: list[float] = []
        baseline_latencies: list[float] = []
        plugin_latencies: list[float] = []
        for row in plugin_rows:
            baseline = indexed.get((task_id, int(row["repeat"]), "none"))
            if baseline is None or not (
                _execution_complete(row) and _execution_complete(baseline)
            ):
                continue
            baseline_input = float(baseline[token_field])
            plugin_input = float(row[token_field])
            input_savings.append(
                (baseline_input - plugin_input) / baseline_input
                if baseline_input
                else 0.0
            )
            baseline_output = float(baseline["actual_output_tokens"])
            plugin_output = float(row["actual_output_tokens"])
            baseline_total = baseline_input + baseline_output
            total_savings.append(
                (baseline_total - plugin_input - plugin_output) / baseline_total
                if baseline_total
                else 0.0
            )
            output_changes.append(
                (plugin_output - baseline_output) / baseline_output
                if baseline_output
                else 0.0
            )
            baseline_latency = float(baseline["latency_seconds"])
            plugin_latency = float(row["latency_seconds"])
            baseline_latencies.append(baseline_latency)
            plugin_latencies.append(plugin_latency)
            latency_changes.append(
                (plugin_latency - baseline_latency) / baseline_latency
                if baseline_latency
                else 0.0
            )
        result[task_id] = {
            "paired_n": len(input_savings),
            "baseline_success_rate": statistics.fmean(
                bool(row["success"]) for row in baseline_rows
            )
            if baseline_rows
            else 0.0,
            "plugin_success_rate": statistics.fmean(
                bool(row["success"]) for row in plugin_rows
            )
            if plugin_rows
            else 0.0,
            "input_savings_rate_mean": statistics.fmean(input_savings)
            if input_savings
            else 0.0,
            "total_token_savings_rate_mean": statistics.fmean(total_savings)
            if total_savings
            else 0.0,
            "output_token_change_rate_mean": statistics.fmean(output_changes)
            if output_changes
            else 0.0,
            "latency_change_rate_mean": statistics.fmean(latency_changes)
            if latency_changes
            else 0.0,
            "latency_change_rate_median": statistics.median(latency_changes)
            if latency_changes
            else 0.0,
            "latency_win_rate": statistics.fmean(value < 0 for value in latency_changes)
            if latency_changes
            else 0.0,
            "baseline_latency_mean": statistics.fmean(baseline_latencies)
            if baseline_latencies
            else 0.0,
            "plugin_latency_mean": statistics.fmean(plugin_latencies)
            if plugin_latencies
            else 0.0,
        }
    return result


def _role_number(row: Mapping[str, Any], role_key: str, metric: str) -> float:
    usage = row.get(role_key)
    if not isinstance(usage, Mapping):
        return 0.0
    return float(usage.get(metric, 0.0))


def _execution_complete(row: Mapping[str, Any]) -> bool:
    if "execution_complete" in row:
        return bool(row["execution_complete"])
    return not row.get("error_type") and int(row.get("model_calls", 0)) == 2


def _boolean_metric(row: Mapping[str, Any], key: str) -> bool:
    if key == "execution_complete":
        return _execution_complete(row)
    return bool(row.get(key))


def _report(summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# AutoGen 多 Agent namespace 配对验证",
        "",
        f"- 模式：{summary['mode']}",
        f"- DeepSeek thinking：{summary.get('thinking_mode', 'default')}",
        f"- 最新样本数：{len(_latest_rows(rows))}",
        f"- 配对数：{summary['paired']['n']}",
        f"- 已匹配/未完成配对：{summary['paired']['matched_n']}/"
        f"{summary['paired']['incomplete_n']}",
        "",
        "## 方法汇总",
        "",
    ]
    for method, values in summary["methods"].items():
        lines.append(
            f"- `{method}`：总样本={values['n']}，完整执行={values['completed_n']}，"
            f"成功率={values['success_rate']:.2%}，"
            f"路由隔离={values['pre_handoff_routing_isolation_rate']:.2%}，"
            f"模型输入隐私={values['model_input_privacy_rate']:.2%}，"
            f"handoff 完整={values['handoff_preserved_rate']:.2%}，"
            f"状态恢复={values['state_roundtrip_ok_rate']:.2%}，"
            f"输出契约={values['output_contract_correct_rate']:.2%}，"
            f"平均输入 token={values['input_tokens_mean']:.1f}，"
            f"规划/执行输入={values['planner_input_tokens_mean']:.1f}/"
            f"{values['executor_input_tokens_mean']:.1f}，"
            f"规划/执行输出={values['planner_output_tokens_mean']:.1f}/"
            f"{values['executor_output_tokens_mean']:.1f}，"
            f"规划/协调/执行耗时={values['planner_latency_mean']:.3f}/"
            f"{values['coordination_latency_mean']:.3f}/"
            f"{values['executor_latency_mean']:.3f} 秒。"
        )
    paired = summary["paired"]
    lines.extend(["", "## 相对基线", ""])
    if paired["n"]:
        lines.extend(
            [
                f"- 平均输入节省：{paired['input_savings_rate_mean']:.2%}",
                f"- bootstrap 95% CI：{paired['input_savings_ci_low']:.2%}～{paired['input_savings_ci_high']:.2%}",
                f"- 峰值输入降低：{paired['peak_savings_rate_mean']:.2%}",
                f"- 输入+输出总 token 节省：{paired['total_token_savings_rate_mean']:.2%}",
                f"- 总 token bootstrap 95% CI：{paired['total_token_savings_ci_low']:.2%}～{paired['total_token_savings_ci_high']:.2%}",
                f"- 输出 token 变化：{paired['output_token_change_rate_mean']:+.2%}",
                f"- 规划 Agent 输出 token 变化：{paired['planner_output_token_change_rate_mean']:+.2%}",
                f"- 执行 Agent 输出 token 变化：{paired['executor_output_token_change_rate_mean']:+.2%}",
                f"- 延迟变化：{paired['latency_change_rate_mean']:+.2%}",
                f"- 规划 Agent 延迟变化：{paired['planner_latency_change_rate_mean']:+.2%}",
                f"- 执行 Agent 延迟变化：{paired['executor_latency_change_rate_mean']:+.2%}",
                f"- 推理 token 估计变化：{paired['reasoning_token_estimate_change_rate_mean']:+.2%}",
                f"- 正节省配对比例：{paired['positive_savings_rate']:.2%}",
                f"- 成功率差：{paired['success_delta']:+.2%}",
            ]
        )
        lines.extend(["", "## 分任务配对", ""])
        for task_id, values in summary.get("by_task", {}).items():
            lines.append(
                f"- `{task_id}`：n={values['paired_n']}，"
                f"输入节省={values['input_savings_rate_mean']:.2%}，"
                f"总 token 节省={values['total_token_savings_rate_mean']:.2%}，"
                f"输出变化={values['output_token_change_rate_mean']:+.2%}，"
                f"延迟变化均值/中位数={values['latency_change_rate_mean']:+.2%}/"
                f"{values['latency_change_rate_median']:+.2%}，"
                f"延迟胜率={values['latency_win_rate']:.2%}。"
            )
    else:
        lines.append(
            "- 无可评估配对：至少一侧没有完成两个 Agent 调用，"
            "不得把缺失样本的 0 token 解释为节省。"
        )
    lines.extend(
        [
            "",
            (
                "本报告是小样本真实 API 结果，必须结合质量、输出 token、延迟和置信区间解读，"
                "不能直接外推到其他模型或团队任务。"
                if summary["mode"] == "api"
                else "本报告的 mock 结果证明团队路由、状态恢复和确定性效率；不代表真实模型团队效果。"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument(
        "--tasks", default="tasks/stage5_autogen_team/team_tasks.json"
    )
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--soft-limit", type=int, default=1500)
    parser.add_argument("--hard-limit", type=int, default=3000)
    parser.add_argument("--target", type=int, default=1150)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--max-visible-output-chars", type=int, default=180)
    parser.add_argument(
        "--thinking-mode", choices=("default", "disabled"), default="default"
    )
    parser.add_argument("--max-api-retries", type=int, default=1)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--max-api-requests", type=int, default=24)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-autogen-team")
    parser.add_argument("--experiment-id", default="autogen-team-3x3-mock-v100")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--protocol", default="")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    _validate_args(args)
    task_path = Path(args.tasks)
    tasks = load_tasks(task_path)
    selected_ids = {part.strip() for part in args.task_ids.split(",") if part.strip()}
    if selected_ids:
        unknown = selected_ids - {str(task["task_id"]) for task in tasks}
        if unknown:
            raise SystemExit(f"unknown task IDs: {', '.join(sorted(unknown))}")
        tasks = [task for task in tasks if str(task["task_id"]) in selected_ids]
    if not tasks:
        raise SystemExit("no tasks selected")
    protocol = _validate_protocol(args, tasks, task_path)
    minimum_api_requests = len(tasks) * args.repeats * len(METHODS) * 2
    print(SYNTHETIC_DISCLOSURE)
    print(
        f"plan: mode={args.mode}, team_cases={len(tasks) * args.repeats * len(METHODS)}, "
        f"agent_calls={minimum_api_requests}, tasks={len(tasks)}, repeats={args.repeats}, "
        f"max_api_requests={args.max_api_requests}"
    )
    if args.plan:
        print("No API request was sent in --plan mode.")
        return 0
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    if args.mode == "api" and args.max_api_requests < minimum_api_requests:
        raise SystemExit(
            "max-api-requests is below the no-retry minimum: "
            f"{args.max_api_requests} < {minimum_api_requests}"
        )
    api_key = ""
    if args.mode == "api":
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        if not api_key:
            raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")

    output = Path(args.out) / args.experiment_id
    results_path = output / "results.jsonl"
    manifest = _manifest(args, tasks, task_path, protocol)
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    if args.resume and (output / "run_manifest.json").exists():
        existing_manifest = json.loads(
            (output / "run_manifest.json").read_text(encoding="utf-8")
        )
        if existing_manifest != manifest:
            raise SystemExit("resume manifest does not match current arguments")
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "run_manifest.json", manifest)
    rows = _load_existing(results_path) if args.resume else []
    latest = {_result_key(row): row for row in _latest_rows(rows)}
    prior_requests = sum(int(row.get("api_request_attempts", 0)) for row in rows)
    request_budget = RequestBudget(args.max_api_requests, used=prior_requests)
    budget = ContextBudget(args.soft_limit, args.hard_limit, args.target)

    plan: list[tuple[Mapping[str, Any], int, str]] = []
    for repeat in range(args.repeats):
        for index, task in enumerate(tasks):
            order = METHODS if (repeat + index) % 2 == 0 else tuple(reversed(METHODS))
            plan.extend((task, repeat, method) for method in order)
    for task, repeat, method in plan:
        key = (str(task["task_id"]), repeat, method)
        existing = latest.get(key)
        if existing is not None and (not args.retry_failed or bool(existing["success"])):
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
            max_visible_output_chars=args.max_visible_output_chars,
            thinking_mode=args.thinking_mode,
            max_api_retries=args.max_api_retries,
            retry_base_delay=args.retry_base_delay,
        )
        _append_jsonl(results_path, row)
        rows.append(row)
        latest[key] = row
        used_tokens = (
            row["actual_input_tokens"]
            if args.mode == "api"
            else row["approximate_input_tokens"]
        )
        print(
            f"{key[0]:22} r{repeat:02d} {method:10} "
            f"success={str(row['success']):5} calls={row['model_calls']} "
            f"tok_in={used_tokens} privacy={row['model_input_privacy']}"
        )

    summary = summarize(rows, args.mode)
    latest_rows = _latest_rows(rows)
    _write_json(output / "summary.json", summary)
    _write_csv(output / "results.csv", latest_rows)
    (output / "report.md").write_text(
        _report(summary, latest_rows), encoding="utf-8"
    )
    _write_json(
        output / "request_usage.json",
        {
            "maximum_api_requests": args.max_api_requests,
            "api_requests_used": request_budget.used,
        },
    )
    paired = summary["paired"]
    print(
        f"paired_n={paired['n']} input_savings={paired['input_savings_rate_mean']:.2%} "
        f"95%CI=[{paired['input_savings_ci_low']:.2%}, {paired['input_savings_ci_high']:.2%}]"
    )
    print(f"report: {output}")
    return 0


def _validate_args(args: argparse.Namespace) -> None:
    if args.repeats <= 0:
        raise SystemExit("repeats must be positive")
    if not (0 < args.target <= args.soft_limit <= args.hard_limit):
        raise SystemExit("budget must satisfy 0 < target <= soft <= hard")
    if args.max_api_requests <= 0:
        raise SystemExit("max-api-requests must be positive")
    if args.max_visible_output_chars <= 0:
        raise SystemExit("max-visible-output-chars must be positive")
    if any(marker in args.base_url for marker in ("[", "]", "(", ")")):
        raise SystemExit("base-url must be a plain URL, not a Markdown link")
    if not args.base_url.startswith(("https://", "http://")):
        raise SystemExit("base-url must be an http(s) URL")


def _manifest(
    args: argparse.Namespace,
    tasks: Sequence[Mapping[str, Any]],
    task_path: Path,
    protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "version": __version__,
        "kind": "autogen_team_namespace_paired",
        "prompt_protocol": PROMPT_PROTOCOL,
        "evaluation_answer_terms_disclosed": False,
        "framework": "autogen-agentchat",
        "mode": args.mode,
        "model": args.model if args.mode == "api" else "ReplayChatCompletionClient",
        "base_url": args.base_url if args.mode == "api" else None,
        "task_ids": [str(task["task_id"]) for task in tasks],
        "task_file_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "methods": list(METHODS),
        "repeats": args.repeats,
        "budget": {
            "soft": args.soft_limit,
            "hard": args.hard_limit,
            "target": args.target,
            "fixed_reserved": args.fixed_reserved_tokens,
        },
        "max_output_tokens": args.max_output_tokens,
        "max_visible_output_chars": args.max_visible_output_chars,
        "thinking_mode": args.thinking_mode,
        "max_api_retries": args.max_api_retries,
        "maximum_api_requests": args.max_api_requests,
        "synthetic_disclosure": SYNTHETIC_DISCLOSURE,
    }
    if protocol is not None:
        protocol_path = Path(args.protocol)
        manifest["protocol"] = {
            "protocol_id": protocol["protocol_id"],
            "path": str(protocol_path),
            "sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            "preregistered": bool(protocol.get("preregistered")),
            "context_pruner_version": protocol.get("context_pruner_version"),
        }
    return manifest


def _validate_protocol(
    args: argparse.Namespace,
    tasks: Sequence[Mapping[str, Any]],
    task_path: Path,
) -> dict[str, Any] | None:
    if not args.protocol:
        return None
    protocol_path = Path(args.protocol)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    expected = {
        "task_file_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "task_ids": [str(task["task_id"]) for task in tasks],
        "methods": list(METHODS),
        "model": args.model,
        "thinking_mode": args.thinking_mode,
        "repeats": args.repeats,
        "maximum_api_requests": args.max_api_requests,
        "max_api_retries": args.max_api_retries,
        "max_output_tokens": args.max_output_tokens,
        "max_visible_output_chars": args.max_visible_output_chars,
        "budget": {
            "soft": args.soft_limit,
            "hard": args.hard_limit,
            "target": args.target,
            "fixed_reserved": args.fixed_reserved_tokens,
        },
    }
    if "context_pruner_version" in protocol:
        expected["context_pruner_version"] = __version__
    mismatches = {
        key: {"protocol": protocol.get(key), "arguments": value}
        for key, value in expected.items()
        if protocol.get(key) != value
    }
    if mismatches:
        raise SystemExit(
            "protocol mismatch: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )
    if not protocol.get("preregistered") or not protocol.get("protocol_id"):
        raise SystemExit("protocol must be preregistered and have protocol_id")
    return protocol


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


def _canonical_text(value: Any) -> str:
    text = str(value).lower().replace("%", " percent ")
    # Structured fields may omit a unit suffix without changing the value
    # (``21-days`` and ``21``), and ``pass``/``passed`` are the same status.
    # Numeric values themselves remain strict.
    text = re.sub(
        r"\b(\d+(?:\.\d+)?)\s*[- ]?(?:days?|hours?|seconds?)\b",
        r"\1",
        text,
    )
    text = re.sub(r"\bpassed\b", "pass", text)
    return "".join(character for character in text if character.isalnum())


def _contains_terms(text: str, terms: Sequence[Any]) -> bool:
    canonical = _canonical_text(text)
    return all(_canonical_text(term) in canonical for term in terms)


def _valid_prefixed_line(text: str, prefix: str) -> bool:
    return bool(
        "\n" not in text
        and re.match(rf"^{re.escape(prefix)}(?:\s|:)", text, flags=re.IGNORECASE)
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
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _bootstrap_ci(values: Sequence[float], seed: int = 42) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(4000)
    )
    return means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def main() -> None:
    raise SystemExit(asyncio.run(async_main(build_parser().parse_args())))


if __name__ == "__main__":
    main()
