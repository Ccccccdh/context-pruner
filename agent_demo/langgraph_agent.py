"""A real LangGraph ReAct loop using Context-Pruner as an optional plugin."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Annotated, Mapping, TypedDict, get_type_hints

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import LangGraphContextAdapter
from context_pruner.plugin import message_to_turn

from .agent import (
    DEFAULT_SYSTEM_PROMPT,
    FINALIZATION_PROMPT,
    _candidate_set_is_exhaustive,
    _constraint_adherence,
    _evaluate,
    _missing_requested_answer_terms,
    _parse_output,
    _requested_answer_terms,
    _partial_score,
    _redundant_action_reason,
    _repeat_guard_observation,
    _sanitize_model_reply,
    _tool_ledger_message,
    _tool_correctness,
)
from .env import KnowledgeBase, Task
from .llm import LLMClient
from .utils import count_tokens


@dataclass
class LangGraphTurn:
    step: int
    tokens_in: int
    full_tokens_in: int
    tokens_out: int
    latency: float
    tool_ledger_tokens: int = 0
    ephemeral_context_tokens: int = 0


@dataclass
class LangGraphRunResult:
    task: Task
    method: str
    success: bool
    final_answer: str
    num_turns: int
    tokens_in_total: int
    full_context_tokens_total: int
    tokens_out_total: int
    peak_context_tokens: int
    peak_full_context_tokens: int
    total_latency: float
    partial_score: float
    constraint_adherence: float
    tool_correctness: float
    parse_error_count: int
    tool_error_count: int
    repeated_action_count: int
    post_completion_tool_call_count: int
    max_turn_failure: bool
    budget_pressure_count: int
    budget_violation_count: int
    checkpoint_count: int
    archive_count: int
    recovery_count: int
    api_retry_count: int
    completion_rejection_count: int
    context_hard_limit_tokens: int
    plugin_metrics: dict[str, Any] = field(default_factory=dict)
    plugin_state: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)
    turns: list[LangGraphTurn] = field(default_factory=list)
    environment_metrics: dict[str, Any] = field(default_factory=dict)
    environment_validation: dict[str, Any] = field(default_factory=dict)

    @property
    def gross_input_tokens_saved(self) -> int:
        return self.full_context_tokens_total - self.tokens_in_total

    @property
    def net_input_tokens_saved(self) -> int:
        # v0.4.0 uses only local deterministic compression and makes no model call.
        return self.gross_input_tokens_saved

    @property
    def net_input_savings_rate(self) -> float:
        return self.net_input_tokens_saved / max(1, self.full_context_tokens_total)

    def summary(self, experiment_id: str, run_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "framework": "langgraph",
            "experiment_id": experiment_id,
            "task_id": self.task.task_id,
            "scenario": self.task.scenario,
            "method": self.method,
            "run_id": run_id,
            "success": self.success,
            "partial_score": self.partial_score,
            "final_kirr": self.partial_score,
            "constraint_adherence": self.constraint_adherence,
            "tool_correctness": self.tool_correctness,
            "num_turns": self.num_turns,
            "tokens_in_total": self.tokens_in_total,
            "tokens_out_total": self.tokens_out_total,
            "full_context_tokens_total": self.full_context_tokens_total,
            "compression_overhead_tokens": 0,
            "gross_input_tokens_saved": self.gross_input_tokens_saved,
            "net_input_tokens_saved": self.net_input_tokens_saved,
            "net_input_savings_rate": self.net_input_savings_rate,
            "peak_context_tokens": self.peak_context_tokens,
            "peak_full_context_tokens": self.peak_full_context_tokens,
            "total_latency": self.total_latency,
            "p95_turn_latency": _percentile(
                [turn.latency for turn in self.turns], 0.95
            ),
            "estimated_total_cost": 0.0,
            "recovery_count": self.recovery_count,
            "recovered_tokens": 0,
            "recovery_precision": 0.0,
            "recovery_recall": 0.0,
            "recovery_amplification": 0.0,
            "parse_error_count": self.parse_error_count,
            "tool_error_count": self.tool_error_count,
            "repeated_action_count": self.repeated_action_count,
            "post_sufficiency_tool_call_count": self.post_completion_tool_call_count,
            "post_completion_tool_call_count": self.post_completion_tool_call_count,
            "stale_evidence_filtered_count": int(
                self.plugin_metrics.get("stale_evidence_filtered_count", 0)
            ),
            "workspace_mutation_epoch": int(
                max(
                    self.plugin_metrics.get("workspace_mutation_epoch", 0),
                    self.environment_metrics.get("workspace_mutation_epoch", 0),
                )
            ),
            "post_mutation_read_count": int(
                self.environment_metrics.get("post_mutation_read_count", 0)
            ),
            "intermediate_test_count": int(
                self.environment_metrics.get("intermediate_test_count", 0)
            ),
            "max_turn_failure": self.max_turn_failure,
            "budget_pressure_count": self.budget_pressure_count,
            "budget_violation_count": self.budget_violation_count,
            "compression_budget_violation_count": self.budget_violation_count,
            "model_input_budget_violation_count": sum(
                turn.tokens_in > self.context_hard_limit_tokens for turn in self.turns
            ),
            "context_hard_limit_tokens": self.context_hard_limit_tokens,
            "ephemeral_context_tokens_total": sum(
                turn.ephemeral_context_tokens for turn in self.turns
            ),
            "peak_ephemeral_context_tokens": max(
                (turn.ephemeral_context_tokens for turn in self.turns), default=0
            ),
            "checkpoint_count": self.checkpoint_count,
            "archive_count": self.archive_count,
            "sanitized_output_count": 0,
            "sanitized_output_tokens": 0,
            "tool_ledger_tokens_total": sum(
                turn.tool_ledger_tokens for turn in self.turns
            ),
            "api_retry_count": self.api_retry_count,
            "completion_rejection_count": self.completion_rejection_count,
            "final_answer": self.final_answer,
            "plugin_metrics": self.plugin_metrics,
            "environment_metrics": self.environment_metrics,
            "environment_validation": self.environment_validation,
        }


class _State(TypedDict, total=False):
    messages: list[Any]
    task: str
    step: int
    final_answer: str
    last_action: dict[str, Any] | None
    actions: list[dict[str, Any]]
    last_search_candidates: list[str]
    parse_error_count: int
    tool_error_count: int
    repeated_action_count: int
    post_completion_tool_call_count: int
    total_latency: float
    tokens_out_total: int
    turn_metrics: list[dict[str, Any]]
    context_pruner_state: dict[str, Any]
    context_pruner_metrics: dict[str, Any]
    context_messages: list[Any]
    context_pruner_error: str
    completion_error: str
    completion_feedback: str
    completion_rejection_count: int


class LangGraphReActAgent:
    """Minimal real-framework Agent used for phase-3 plugin A/B validation."""

    def __init__(
        self,
        client: LLMClient,
        env: KnowledgeBase,
        *,
        max_turns: int = 20,
        method: str = "pruner_v1",
        temperature: float = 0.0,
        context_budget: ContextBudget | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if method not in {"none", "pruner_v1"}:
            raise ValueError("LangGraph 阶段三当前只支持 none 与 pruner_v1")
        if max_turns <= 0:
            raise ValueError("max_turns 必须大于 0")
        self.client = client
        self.env = env
        self.max_turns = max_turns
        self.method = method
        self.temperature = temperature
        self.context_budget = context_budget or ContextBudget(800, 1_000, 650)
        self.system_prompt = system_prompt + f"\n可用工具：{env.tool_schema()}"
        self.adapter = LangGraphContextAdapter(
            ContextPluginConfig(
                enabled=method != "none",
                method=method,
                budget=self.context_budget,
            ),
            token_counter=count_tokens,
            budget_resolver=lambda state: self._ephemeral_context(state)[2],
        )

    def _ephemeral_context(
        self,
        state: Mapping[str, Any],
    ) -> tuple[list[dict[str, str]], int, int]:
        """Build per-call control messages and their budget reservation.

        These messages are intentionally not persisted in graph history.  Their
        tokens must nevertheless be reserved before history compression so the
        configured hard limit describes the final model-facing input.
        """
        rows: list[dict[str, str]] = []
        executed_actions = list(state.get("actions") or [])
        search_candidates = list(state.get("last_search_candidates") or [])
        if hasattr(self.env, "tool_ledger"):
            tool_ledger = self.env.tool_ledger(executed_actions, search_candidates)
        else:
            tool_ledger = _tool_ledger_message(
                executed_actions,
                search_candidates,
                all_doc_ids=self.env.resource_ids(),
            )
        tool_ledger_tokens = count_tokens(tool_ledger) if tool_ledger else 0
        if tool_ledger:
            rows.append({"role": "system", "content": tool_ledger})

        completion_feedback = str(state.get("completion_feedback", ""))
        if completion_feedback:
            rows.append({"role": "system", "content": completion_feedback})

        requested_terms = _requested_answer_terms(str(state.get("task", "")))
        if requested_terms:
            rows.append(
                {
                    "role": "system",
                    "content": (
                        "回答完整性清单（仅来自用户原问题）："
                        + "、".join(requested_terms)
                        + "。提交 final_answer 前必须逐项明确覆盖；尚无证据时继续检索。"
                    ),
                }
            )
        if int(state.get("step", 0)) >= self.max_turns - 1:
            rows.append({"role": "system", "content": FINALIZATION_PROMPT})
        total_tokens = sum(count_tokens(row["content"]) for row in rows)
        return rows, tool_ledger_tokens, total_tokens

    def run(self, task: Task) -> LangGraphRunResult:
        StateGraph, START, END, add_messages = _langgraph_imports()
        if hasattr(self.client, "reset"):
            self.client.reset()

        # Functional TypedDict keeps the runtime reducer object in __annotations__.
        # A nested class under ``from __future__ import annotations`` would store
        # ``add_messages`` as an unresolved string that LangGraph cannot inspect.
        GraphState = TypedDict(
            "GraphState",
            {
                **get_type_hints(_State),
                "messages": Annotated[list[Any], add_messages],
            },
            total=False,
        )

        def model_node(state: Mapping[str, Any]) -> dict[str, Any]:
            step = int(state.get("step", 0))
            messages = [message_to_turn(message) for message in state.get("messages", [])]
            call_metrics = dict(state.get("context_pruner_metrics") or {})
            full_tokens_in = int(call_metrics.get("last_full_context_tokens", 0))
            ephemeral, tool_ledger_tokens, ephemeral_tokens = self._ephemeral_context(state)
            messages.extend(ephemeral)
            full_tokens_in += ephemeral_tokens
            tokens_in = sum(count_tokens(row["content"]) for row in messages)
            started = time.perf_counter()
            reply = self.client.complete(messages, temperature=self.temperature)
            latency = time.perf_counter() - started
            parsed = _parse_output(reply)
            context_reply, _, _ = _sanitize_model_reply(reply, parsed)
            final_answer = ""
            action = None
            parse_errors = int(state.get("parse_error_count", 0))
            error = ""
            completion_error = ""
            completion_rejections = int(
                state.get("completion_rejection_count", 0)
            )
            if parsed is not None and "final_answer" in parsed:
                proposed_answer = str(parsed["final_answer"]).strip()
                missing_terms = _missing_requested_answer_terms(
                    str(state.get("task", "")), proposed_answer
                )
                if missing_terms and step < self.max_turns - 1:
                    completion_rejections += 1
                    completion_error = (
                        "最终答案缺少用户问题中明确要求的项目："
                        + "、".join(missing_terms)
                        + "。请继续收集缺失证据，完整覆盖后再 final_answer。"
                    )
                else:
                    final_answer = proposed_answer
            elif parsed is not None and isinstance(parsed.get("action"), dict):
                action = dict(parsed["action"])
            else:
                parse_errors += 1
                error = "模型输出不是合法的 action 或 final_answer JSON"
            return {
                "messages": [{"role": "assistant", "content": context_reply}],
                "step": step + 1,
                "last_action": action,
                "final_answer": final_answer,
                "parse_error_count": parse_errors,
                "context_pruner_error": error,
                "completion_error": completion_error,
                "completion_feedback": "",
                "completion_rejection_count": completion_rejections,
                "total_latency": float(state.get("total_latency", 0.0)) + latency,
                "tokens_out_total": int(state.get("tokens_out_total", 0))
                + count_tokens(reply),
                "turn_metrics": [
                    *list(state.get("turn_metrics") or []),
                    {
                        "step": step,
                        "tokens_in": tokens_in,
                        "full_tokens_in": full_tokens_in,
                        "tokens_out": count_tokens(reply),
                        "latency": latency,
                        "tool_ledger_tokens": tool_ledger_tokens,
                        "ephemeral_context_tokens": ephemeral_tokens,
                    },
                ],
            }

        def tool_node(state: Mapping[str, Any]) -> dict[str, Any]:
            action = dict(state.get("last_action") or {})
            actions = list(state.get("actions") or [])
            candidates = list(state.get("last_search_candidates") or [])
            workspace_complete = bool(
                hasattr(self.env, "is_complete") and self.env.is_complete()
            )
            if workspace_complete:
                reason = "workspace_complete"
            elif hasattr(self.env, "redundant_action_reason"):
                reason = self.env.redundant_action_reason(action, actions, candidates)
            else:
                reason = _redundant_action_reason(
                    action,
                    actions,
                    candidate_ids=candidates,
                    all_doc_ids=self.env.resource_ids(),
                )
            repeated = int(state.get("repeated_action_count", 0))
            post_completion = int(state.get("post_completion_tool_call_count", 0))
            tool_errors = int(state.get("tool_error_count", 0))
            refined_candidates: list[str] | None = None
            if reason == "search_exhaustive_candidates":
                keyword = str((action.get("args") or {}).get("keyword", ""))
                proposed = [str(item) for item in self.env.search(keyword)]
                # An exhaustive result means a new query cannot discover a new
                # document, but it may still rank/filter the known set.  Permit
                # that refinement only when it produces a strict non-empty subset.
                if proposed and set(proposed) < set(candidates):
                    reason = None
                    refined_candidates = proposed
            if reason is not None:
                repeated += 1
                if reason == "workspace_complete":
                    post_completion += 1
                if hasattr(self.env, "repeat_guard_observation"):
                    observation = self.env.repeat_guard_observation(
                        action, actions, reason, candidates
                    )
                else:
                    observation = _repeat_guard_observation(
                        action,
                        actions,
                        self.env,
                        reason=reason,
                        candidate_ids=candidates,
                    )
            else:
                actions.append(action)
                name = str(action.get("name", "")).strip()
                args = dict(action.get("args") or {})
                execution = self.env.execute(name, args)
                if refined_candidates is not None:
                    candidates = refined_candidates
                elif execution.candidates is not None:
                    candidates = [str(item) for item in execution.candidates]
                observation = execution.output
                if execution.is_error:
                    tool_errors += 1
            return {
                "messages": [{"role": "user", "content": f"观察：{observation}"}],
                "actions": actions,
                "last_search_candidates": candidates,
                "tool_error_count": tool_errors,
                "repeated_action_count": repeated,
                "post_completion_tool_call_count": post_completion,
                "last_action": None,
            }

        def error_node(state: Mapping[str, Any]) -> dict[str, Any]:
            # A malformed JSON reply needs a concise formatting correction, not
            # retrieval.  Recovering archived evidence here previously expanded
            # prompts and encouraged the model to revisit already-finished work.
            update = self.adapter.on_error_node(state, recover=False)
            update["messages"] = [
                {
                    "role": "user",
                    "content": "观察：格式错误：输出必须是合法 JSON（action 或 final_answer）。",
                }
            ]
            return update

        def incomplete_node(state: Mapping[str, Any]) -> dict[str, Any]:
            feedback = str(state.get("completion_error", ""))
            update = self.adapter.after_tool_node(state)
            update["messages"] = [
                {
                    "role": "user",
                    "content": "观察：" + feedback,
                }
            ]
            update["completion_error"] = ""
            # Compression may archive the corrective observation immediately.
            # Keep one protected copy for exactly the next model call.
            update["completion_feedback"] = feedback
            return update

        def route_after_model(state: Mapping[str, Any]) -> str:
            if state.get("final_answer") or int(state.get("step", 0)) >= self.max_turns:
                return "finalize"
            if state.get("completion_error"):
                return "incomplete"
            if isinstance(state.get("last_action"), Mapping):
                return "tools"
            return "error"

        builder = StateGraph(GraphState)
        builder.add_node("model", self.adapter.wrap_model_node(model_node))
        builder.add_node("tools", self.adapter.wrap_tool_node(tool_node))
        builder.add_node("error", error_node)
        builder.add_node("incomplete", incomplete_node)
        builder.add_node("finalize", self.adapter.finalize_node)
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model",
            route_after_model,
            {
                "tools": "tools",
                "error": "error",
                "incomplete": "incomplete",
                "finalize": "finalize",
            },
        )
        builder.add_edge("tools", "model")
        builder.add_edge("error", "model")
        builder.add_edge("incomplete", "model")
        builder.add_edge("finalize", END)
        graph = builder.compile()
        initial: GraphState = {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"当前任务：{task.question}"},
            ],
            "task": task.question,
            "step": 0,
            "final_answer": "",
            "last_action": None,
            "actions": [],
            "last_search_candidates": [],
            "parse_error_count": 0,
            "tool_error_count": 0,
            "repeated_action_count": 0,
            "post_completion_tool_call_count": 0,
            "completion_error": "",
            "completion_feedback": "",
            "completion_rejection_count": 0,
            "total_latency": 0.0,
            "tokens_out_total": 0,
            "turn_metrics": [],
        }
        state = graph.invoke(
            initial,
            config={"recursion_limit": max(30, self.max_turns * 3 + 5)},
        )
        metrics = dict(state.get("context_pruner_metrics") or {})
        plugin_state = dict(state.get("context_pruner_state") or {})
        turn_rows = [
            LangGraphTurn(
                step=int(row.get("step", 0)),
                tokens_in=int(row.get("tokens_in", 0)),
                full_tokens_in=int(row.get("full_tokens_in", row.get("tokens_in", 0))),
                tokens_out=int(row.get("tokens_out", 0)),
                latency=float(row.get("latency", 0.0)),
                tool_ledger_tokens=int(row.get("tool_ledger_tokens", 0)),
                ephemeral_context_tokens=int(
                    row.get("ephemeral_context_tokens", row.get("tool_ledger_tokens", 0))
                ),
            )
            for row in state.get("turn_metrics", [])
        ]
        final_answer = str(state.get("final_answer") or "")
        actions = list(state.get("actions") or [])
        environment_validation = (
            self.env.validate(task, final_answer)
            if hasattr(self.env, "validate")
            else {"success": True, "checks": []}
        )
        environment_metrics = (
            self.env.metrics() if hasattr(self.env, "metrics") else {}
        )
        return LangGraphRunResult(
            task=task,
            method=self.method,
            success=_evaluate(task, final_answer)
            and bool(environment_validation.get("success", True)),
            final_answer=final_answer,
            num_turns=int(state.get("step", 0)),
            tokens_in_total=sum(turn.tokens_in for turn in turn_rows),
            full_context_tokens_total=sum(turn.full_tokens_in for turn in turn_rows),
            tokens_out_total=int(state.get("tokens_out_total", 0)),
            peak_context_tokens=max((turn.tokens_in for turn in turn_rows), default=0),
            peak_full_context_tokens=max(
                (turn.full_tokens_in for turn in turn_rows), default=0
            ),
            total_latency=float(state.get("total_latency", 0.0)),
            partial_score=_partial_score(task, final_answer),
            constraint_adherence=_constraint_adherence(task, final_answer),
            tool_correctness=_tool_correctness(
                task,
                actions,
                valid_tools=self.env.tool_names(),
            ),
            parse_error_count=int(state.get("parse_error_count", 0)),
            tool_error_count=int(state.get("tool_error_count", 0)),
            repeated_action_count=int(state.get("repeated_action_count", 0)),
            post_completion_tool_call_count=int(
                state.get("post_completion_tool_call_count", 0)
            ),
            max_turn_failure=not bool(final_answer),
            budget_pressure_count=int(metrics.get("budget_pressure_count", 0)),
            budget_violation_count=int(metrics.get("budget_violation_count", 0)),
            checkpoint_count=int(metrics.get("checkpoint_count", 0)),
            archive_count=int(metrics.get("archive_count", 0)),
            recovery_count=int(metrics.get("lifecycle_recovery_count", 0)),
            api_retry_count=int(getattr(self.client, "retry_count", 0)),
            completion_rejection_count=int(
                state.get("completion_rejection_count", 0)
            ),
            context_hard_limit_tokens=self.context_budget.hard_limit_tokens,
            plugin_metrics=metrics,
            plugin_state=plugin_state,
            actions=actions,
            messages=[message_to_turn(message) for message in state.get("messages", [])],
            turns=turn_rows,
            environment_metrics=environment_metrics,
            environment_validation=environment_validation,
        )


def _langgraph_imports():
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.graph.message import add_messages
    except ImportError as error:
        raise RuntimeError(
            "缺少阶段三可选依赖，请执行：python -m pip install -r requirements-langgraph.txt"
        ) from error
    return StateGraph, START, END, add_messages


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
