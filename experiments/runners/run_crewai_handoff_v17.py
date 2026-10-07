from __future__ import annotations

"""CrewAI r17 three-arm batch: the r16 case flow with the v17 controller guard.

Why a new file
--------------
``experiments/runners/run_crewai_handoff_v16.py`` is pinned by the r16 three-arm freeze,
and r17 forbids editing a pinned source.  This file is therefore generated from it with
exactly the changes listed in ``REUSED_FROM_V16``, and the generator refuses to run when
the r16 runner hashes to a digest no amendment registers.

The guard is installed on **every arm** by default (``--guard-scope all_arms``), so the
plugin arm differs from the baseline only by the compression mechanism; the batch then
asserts, per unit, that the guard stayed inert on the non-plugin arms.
"""

REUSED_FROM_V16 = {
    "source_file": "experiments/runners/run_crewai_handoff_v16.py",
    "source_sha256_at_build": "6bcbe39e537de96caea1fc419849fff2d1eebb8176247e6db6e749289384927d",
    "frozen_sha256": "441390dcf06af8b42967b197cb623835414843ef36f18c8c807a860769716aab",
    "amendment_registered_sha256": "6bcbe39e537de96caea1fc419849fff2d1eebb8176247e6db6e749289384927d",
    "reused_changes": [
        "the guard import is repointed to crewai_loop_guard_v17",
        "crewai_handoff_v17_tasks is imported for the task set and the prompt shape",
        "TASK_FILE points at tasks/stage5_autogen/natural_tasks_r17.json",
        "PROTOCOL, the default experiment id, the task ids and the remedy probe are the r17 ones",
        "everything else (the case flow, the adapter wiring, the budget handling, the manifest fields and the accounting) is copied verbatim"
    ]
}

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import AsyncOpenAI, OpenAI

from context_pruner import ContextPluginConfig
from experiments.runners import crewai_handoff_v17_tasks as tasks_module
from experiments.runners import crewai_loop_guard_v17 as guard_module
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import crewai_toolrounds_contract_v13 as mechanism
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as plumbing
from experiments.runners import run_crewai_handoff_v8 as v8

METHODS = v8.METHODS
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks_r17.json")
PROTOCOL = "crewai-two-role-r17-guarded"
FIRST_ROLE = v8.FIRST_ROLE
SECOND_ROLE = v8.SECOND_ROLE
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
FAILURE_CLASSES = ("none", "tool_sequence", "contract_placeholder_echo", "guard_exhausted", "other")
TASK_IDS = (
    "audit_trail_retention_gate",
    "zone_drain_gate",
    "compaction_window_gate",
    "rollback_point_gate",
)
REPEATS = 3
#: Default batch purpose.  A payload-acquisition batch would declare
#: ``payload_acquisition_only`` instead; this runner always writes the value it is given,
#: so the manifest is complete before the first request.
DEFAULT_PURPOSE = "three_arm_pilot"
#: r17 constraint: install the controller guard on **every** arm, so the plugin arm differs
#: from the baseline only by the compression mechanism.  Set ``--guard-scope pruner_only``
#: to restore the older arm-scoped layout; a batch that does so must then assert zero guard
#: activity on the other arms (the r16 layout, kept for auditability).
GUARD_ALL_ARMS = True
K_RECENT_TOOL_ROUNDS = mechanism.DEFAULT_K_RECENT_TOOL_ROUNDS
MAX_GUARD_REJECTIONS = guard_module.DEFAULT_MAX_REJECTIONS
REMEDY_PROBE = ("audit_trail_retention_gate", 0)
FIRST_ROLE_STAGE = 0
DECISION_PLACEHOLDER_SENTENCE = (
    "The angle-bracket placeholder above is a template: replace it with the actual "
    "value from the current tool evidence of this run, and never output the "
    "angle-bracket characters as part of the final line. "
    "The example shape is: RESULT task=<task id> decision=<the current decision word> "
    "evidence=<the key facts and values returned by the tools now>, with every "
    "angle-bracket part replaced by its actual value."
)


def decider_role_prompt(task: dict[str, Any]) -> str:
    return f"{v8.latest_task_prompt(task)} {DECISION_PLACEHOLDER_SENTENCE}"


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return v8.load_tasks(path or TASK_FILE)


build_tools = v8.build_tools
latest_task_prompt = v8.latest_task_prompt
_all_observations = v8._all_observations


def fixed_history(task: dict[str, Any], repeat: int) -> list[dict[str, str]]:
    """The r15 investigator prompt shape: 14 filler pairs + the task statement."""
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages: list[dict[str, str]] = [
        {"role": "user", "content": str(task["history_constraint"])},
        {"role": "assistant", "content": f"已记录当前约束，并会在后续判断中保留 {anchor}。"},
    ]
    for index in range(14 + repeat):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"关于{topic}的讨论片段（archive-{index + 1}）：当时提到过容量、排期与"
                        "负责人，也列过几个备选方案；这些内容只是背景，不能替代当前实测数据。"
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        f"archive-{index + 1} 已记录：相关结论时间较早，部分已经关闭或撤销，"
                        "判断时应优先采用当前工具返回的事实。"
                    ),
                },
            ]
        )
    tools = "、".join(str(tool["name"]) for tool in task["tools"])
    messages.append(
        {
            "role": "user",
            "content": (
                f"调查阶段：{task['title']}。请按固定顺序对每个可用证据源各调用一次（{tools}），"
                "记录每次返回的当前事实。最终只输出一行以 HANDOFF 开头的交接内容，"
                "包含固定标识与关键数值；不要给出最终 decision。"
            ),
        }
    )
    return messages


def _make_llm(method: str, mode: str, task: dict[str, Any], stage: int, client: Any,
              request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: Any, trace: list[str], *, missing_fact: bool = False,
              guard_state: guard_module.GuardState | None = None) -> Any:
    if mode == "mock":
        responses = _mock_stage_responses(task, stage, trace, missing_fact=missing_fact)
        if guard_state is not None and guard_state.enabled:
            llm = guard_module.GuardedReplayCrewAILLM(responses)
            guard_module.register_guard_state(llm, guard_state)
            return llm
        return base.ReplayCrewAILLM(responses)
    if method == "native_summary":
        return base.NativeSummaryCrewAILLM(
            client=client, model=args.model, request_budget=request_budget,
            max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
            summary_client_factory=lambda: AsyncOpenAI(
                api_key=(base.os.getenv("OPENAI_API_KEY")
                         or base.os.getenv("DEEPSEEK_API_KEY") or ""),
                base_url=args.base_url, timeout=60.0, max_retries=0,
            ),
            summary_model=args.model, soft_limit_tokens=budget.soft_limit_tokens,
            hard_limit_tokens=budget.hard_limit_tokens,
            target_tokens=budget.target_tokens or 1,
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            max_summary_tokens=args.max_summary_tokens,
            max_summary_calls=args.max_summary_calls,
        )
    llm = guard_module.GuardedCrewAILLM(
        client=client, model=args.model, request_budget=request_budget,
        max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
    )
    if guard_state is not None:
        guard_module.register_guard_state(llm, guard_state)
    return llm


def _mock_stage_responses(task: dict[str, Any], stage: int, trace: list[str],
                          *, missing_fact: bool = False) -> list[str]:
    if stage == FIRST_ROLE_STAGE:
        responses: list[str] = []
        for tool in task["tools"]:
            responses.append(
                "Thought: I must read the next evidence source.\n"
                f"Action: {tool['name']}\n"
                f"Action Input: {json.dumps(tool['call'], ensure_ascii=False)}"
            )
        if missing_fact:
            responses.append("Thought: I forgot the evidence.\nFinal Answer: HANDOFF")
        else:
            responses.append(
                "Thought: every evidence source returned its current facts.\nFinal Answer: HANDOFF "
                + " ".join(str(fact) for fact in task["handle_facts"])
            )
        return responses
    if stage == 2:
        return [
            "Thought: restate the already observed facts.\nFinal Answer: HANDOFF "
            + " ".join(str(fact) for fact in task["handle_facts"])
        ]
    tool = task["tools"][-1]
    evidence = (
        judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
    )
    return [
        "Thought: I must verify the current fact with my own tool.\n"
        f"Action: {tool['name']}\n"
        f"Action Input: {json.dumps(tool['call'], ensure_ascii=False)}",
        "Thought: I have all required current evidence.\nFinal Answer: "
        + f"RESULT task={task['task_id']} decision={task['decision']} evidence={evidence}",
    ]


def classify_failure(row: dict[str, Any], task: dict[str, Any]) -> str:
    """Pre-registered class, with the guard's own failure mode named separately."""
    if bool(row.get("guard_exhausted")):
        return "guard_exhausted"
    if not bool(row.get("tool_sequence_consistent")):
        return "tool_sequence"
    if bool(row.get("strict_success")) and bool(row.get("semantic_success")):
        return "none"
    outputs = list(row.get("role_outputs") or [])
    answer = str(outputs[-1]) if outputs else ""
    verdict = (row.get("answer_verdict") or {}).get("semantic") or {}
    seen = judge.canonical_decision(str(verdict.get("decision_seen") or ""))
    expected = judge.canonical_decision(str(task["decision"]))
    if "<" in answer or ">" in answer or seen != expected:
        return "contract_placeholder_echo"
    return "other"


def _pin_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "pinned_events", "pinned_message_total", "pinned_characters_total",
        "required_fact_whole_prefix_fallbacks", "required_facts_missing_after_pin_total",
    )
    totals = {key: sum(int(entry.get(key, 0)) for entry in metrics) for key in keys}
    tiers: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for entry in metrics:
        for tier, count in dict(entry.get("pin_tier_counts") or {}).items():
            tiers[tier] = tiers.get(tier, 0) + int(count)
        for reason, count in dict(entry.get("pin_fallback_reasons") or {}).items():
            reasons[reason] = reasons.get(reason, 0) + int(count)
    totals["pin_tier_counts"] = tiers
    totals["pin_fallback_reasons"] = reasons
    return totals


def _recency_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "recency_calls", "recency_protected_rounds_total", "recent_rounds_seen_total",
        "recent_rounds_readded_total", "recent_replacement_characters_total",
        "recency_omitted_tool_rounds_total",
    )
    return {key: sum(int(entry.get(key, 0)) for entry in metrics) for key in keys}


def run_case(task: dict[str, Any], method: str, repeat: int, mode: str,
             client: Any, request_budget: base.RequestBudget,
             args: argparse.Namespace, budget: Any,
             *, force_missing_handoff: bool = False,
             force_early_final: bool = False) -> dict[str, Any]:
    """The r15 case flow with the guard installed on the first role."""
    inputs: list[list[dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    role_outputs: list[str] = []
    role_raw_outputs: list[str] = []
    role_metrics: list[dict[str, Any]] = []
    role_traces: list[list[str]] = []
    handoff_first_pass: dict[str, Any] | None = None
    handoff_recovery: dict[str, Any] | None = None
    handoff_for_decider = ""
    recovery_observation = ""
    recovery_observations: list[str] = []
    recovery_raw_output = ""
    recovery_invocations = 0
    recovery_api_attempts = 0
    recovery_metrics: dict[str, Any] = {}
    summary_attempts = summary_inputs = summary_outputs = summary_failures = 0
    error = ""
    guard_records: list[dict[str, Any]] = []
    before = request_budget.used
    handle_rule = judge.handle_rule_for([task], str(task["task_id"]))
    for stage, role in enumerate((FIRST_ROLE, SECOND_ROLE)):
        trace: list[str] = []
        is_first = stage == FIRST_ROLE_STAGE
        mock_missing = (
            force_missing_handoff
            or (mode == "mock" and is_first
                and (task["task_id"], repeat) == REMEDY_PROBE
                and method == "pruner_v1")
        )
        # r17 constraint: the guard is a HOST-SIDE CONTROLLER CONFIGURATION, not part of the
        # plugin mechanism.  Leaving it on one arm only would add a controller to that arm
        # and not to the others -- a confound.  The pre-registered default is therefore
        # "the guard is installed on every arm" (GUARD_ALL_ARMS), and a batch that wants the
        # older arm-scoped layout must say so explicitly and then assert zero guard activity
        # on the other arms.
        guard_scope_first_role = bool(getattr(args, "guard_all_arms", GUARD_ALL_ARMS))
        guard_state = guard_module.create_guard_state(
            [str(tool["name"]) for tool in task["tools"]] if is_first else (),
            enabled=is_first and (guard_scope_first_role or method == "pruner_v1"),
            max_rejections=MAX_GUARD_REJECTIONS,
        )
        llm = _make_llm(method, mode, task, stage, client, request_budget, args,
                        budget, trace, missing_fact=mock_missing,
                        guard_state=guard_state)
        stage_tools = (
            [str(tool["name"]) for tool in task["tools"]]
            if is_first else [str(task["tools"][-1]["name"])]
        )
        adapter = mechanism.create_recency_adapter(
            ContextPluginConfig(
                enabled=method == "pruner_v1",
                method="pruner_v1" if method == "pruner_v1" else "none",
                budget=budget,
            ),
            rule=pinned.pinned_rule(task),
            budget=budget,
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            agent_roles=[role],
            k_recent_tool_rounds=K_RECENT_TOOL_ROUNDS if is_first else 0,
        )
        agent = Agent(
            role=role,
            goal=("Use every supplied current evidence source, then pass a factual "
                  "HANDOFF without deciding." if is_first else
                  "Use the supplied current tool and handoff, then return the exact "
                  "RESULT contract."),
            backstory=("You are a careful synthetic release engineer. Current evidence "
                       "takes priority over historical discussion."),
            llm=llm, tools=build_tools(task, stage_tools, trace),
            allow_delegation=False, max_iter=8, verbose=False,
            respect_context_window=False,
        )
        prompt = (
            fixed_history(task, repeat) if is_first else
            [
                {"role": "user", "content": str(task["history_constraint"])},
                {"role": "user", "content": "Investigator handoff: " + handoff_for_decider},
                {"role": "user", "content": decider_role_prompt(task)},
            ]
        )
        try:
            if method == "pruner_v1":
                with adapter.attached():
                    raw_output = str(agent.kickoff(prompt))
            else:
                raw_output = str(agent.kickoff(prompt))
            output = raw_output.strip()
            if not output:
                raise base.EmptyModelResponseError("empty role output")
            role_outputs.append(output)
            role_raw_outputs.append(raw_output)
        except Exception as caught:
            error = f"{type(caught).__name__}: {str(caught)[:300]}"
        finally:
            closer = getattr(llm, "close_summary_transport", None)
            if callable(closer):
                closer()
            inputs.extend(llm.inputs)
            records.extend(llm.response_records)
            attempts.extend(llm.attempt_records)
            role_metrics.append(adapter.metrics_dict())
            role_traces.append(trace)
            guard_records.append(guard_state.as_record())
            if isinstance(llm, base.NativeSummaryCrewAILLM):
                metrics = llm.metrics_dict()
                summary_attempts += int(metrics["native_summary_attempts"])
                summary_inputs += int(metrics["native_summary_input_tokens"])
                summary_outputs += int(metrics["native_summary_output_tokens"])
                summary_failures += int(metrics["native_summary_failures"])
        if error:
            break
        if is_first:
            verdict = judge.judge_handoff(handle_rule, role_raw_outputs[0])
            handoff_first_pass = verdict.as_record()
            handoff_for_decider = str(verdict.semantic["canonical"] or "")
            if verdict.semantic["needs_recovery"]:
                recovery_observations = _all_observations(llm.inputs)
                recovery_observation = (
                    recovery_observations[-1] if recovery_observations else ""
                )
                if not recovery_observation:
                    error = "HandoffRecoveryError: recorded tool observation missing"
                    break
                recovery_invocations = 1
                recovery_before = request_budget.used
                recovery_guard = guard_module.create_guard_state((), enabled=False)
                recovery_llm = _make_llm(method, mode, task, 2, client,
                                         request_budget, args, budget, [],
                                         guard_state=recovery_guard)
                recovery_agent = Agent(
                    role=FIRST_ROLE,
                    goal="Restate only the already observed tool facts as one factual HANDOFF line.",
                    backstory="You are correcting a handoff from a recorded current observation.",
                    llm=recovery_llm, tools=[], allow_delegation=False,
                    max_iter=1, verbose=False, respect_context_window=False,
                )
                recovery_prompt = [{"role": "user", "content": (
                    "Your first handoff omitted required evidence. The tools have already run; "
                    "do not call any tool. Return one line beginning HANDOFF with the "
                    "current observations' required facts. No decision. "
                    f"Fixed task identifiers: {task['history_constraint']}\n"
                    f"Previous output: {role_outputs[0]}\n"
                    "Recorded current observations:\n"
                    + "\n".join(recovery_observations)
                )}]
                try:
                    recovery_raw = str(recovery_agent.kickoff(recovery_prompt))
                    recovery_verdict = judge.judge_handoff(handle_rule, recovery_raw)
                    recovery_raw_output = recovery_raw
                    handoff_recovery = recovery_verdict.as_record()
                    handoff_for_decider = str(recovery_verdict.semantic["canonical"] or "")
                    if not handoff_for_decider:
                        error = "HandoffRecoveryError: recovery still lacks required facts"
                except Exception as caught:
                    error = f"HandoffRecoveryError: {type(caught).__name__}: {str(caught)[:250]}"
                finally:
                    closer = getattr(recovery_llm, "close_summary_transport", None)
                    if callable(closer):
                        closer()
                    inputs.extend(recovery_llm.inputs)
                    records.extend(recovery_llm.response_records)
                    attempts.extend(recovery_llm.attempt_records)
                    recovery_metrics = recovery_adapter_metrics_safe(recovery_llm)
                    recovery_api_attempts = request_budget.used - recovery_before
                    if isinstance(recovery_llm, base.NativeSummaryCrewAILLM):
                        metrics = recovery_llm.metrics_dict()
                        summary_attempts += int(metrics["native_summary_attempts"])
                        summary_inputs += int(metrics["native_summary_input_tokens"])
                        summary_outputs += int(metrics["native_summary_output_tokens"])
                        summary_failures += int(metrics["native_summary_failures"])
                if error:
                    break
    final = role_outputs[-1] if len(role_outputs) == 2 else ""
    first_guard = guard_records[0] if guard_records else {}
    guard_exhausted = bool(first_guard.get("guard_exhausted"))
    all_metrics = [*role_metrics, recovery_metrics]
    role_safe = all(int(metrics.get(key, 0)) == 0
                    for metrics in all_metrics
                    for key in ("crewai_group_restore_failure_count",
                                "crewai_unmatched_call_count",
                                "crewai_active_pending_view_count"))
    expected_traces = (
        [str(tool["name"]) for tool in task["tools"]],
        [str(task["tools"][-1]["name"])],
    )
    tool_evidence_ok = (
        len(role_traces) == 2
        and all(role_traces[index] == expected_traces[index] for index in (0, 1))
    )
    first_role_trace = list(role_traces[0]) if role_traces else []
    answer_verdict = judge.judge_answer(
        judge.answer_rule_for([task], str(task["task_id"])), final
    )
    first_pass_verdict = (judge.judge_handoff(handle_rule, role_raw_outputs[0])
                          if role_raw_outputs else None)
    first_pass_complete = bool(
        first_pass_verdict is not None
        and not first_pass_verdict.semantic["missing_facts"]
        and first_pass_verdict.semantic["one_line"]
    )
    strict_success = bool(
        not error and not guard_exhausted and len(role_outputs) == 2
        and tool_evidence_ok and role_safe and bool(handoff_for_decider)
        and answer_verdict.strict_pass
    )
    semantic_success = bool(
        not error and not guard_exhausted and len(role_outputs) == 2
        and tool_evidence_ok and role_safe and bool(handoff_for_decider)
        and answer_verdict.semantic_pass
    )
    input_tokens = sum(int(record.get("input_tokens", 0)) for record in records)
    output_tokens = sum(int(record.get("output_tokens", 0)) for record in records)
    pins = _pin_metrics(all_metrics)
    recency = _recency_metrics(all_metrics)
    row = {
        "task_id": task["task_id"], "repeat": repeat, "method": method,
        "success": strict_success,
        "strict_success": strict_success,
        "semantic_success": semantic_success,
        "first_pass_complete": first_pass_complete,
        "first_pass_missing_facts": list(
            (first_pass_verdict.semantic["missing_facts"] if first_pass_verdict else [])
        ),
        "tool_sequence_consistent": bool(
            len(role_traces) == 2 and role_traces[0] == expected_traces[0]
        ),
        "decider_tool_sequence_consistent": bool(
            len(role_traces) == 2 and role_traces[1] == expected_traces[1]
        ),
        "expected_first_role_trace": list(expected_traces[0]),
        "first_role_trace": first_role_trace,
        "role_safe": role_safe,
        "tool_evidence_consistent": tool_evidence_ok,
        "answer_verdict": answer_verdict.as_record(),
        "role_outputs": role_outputs, "role_raw_outputs": role_raw_outputs,
        "role_tool_traces": role_traces,
        "role_metrics": role_metrics, "error": error,
        "handoff_first_pass": handoff_first_pass,
        "handoff_recovery": handoff_recovery,
        "handoff_recovery_raw": recovery_raw_output,
        "handoff_for_decider": handoff_for_decider,
        "recovery_observation": recovery_observation,
        "recovery_observations": recovery_observations,
        "recovery_invocations": recovery_invocations,
        "recovery_api_attempts": recovery_api_attempts,
        "recovery_metrics": recovery_metrics,
        "agent_input_tokens": input_tokens, "agent_output_tokens": output_tokens,
        "summary_input_tokens": summary_inputs, "summary_output_tokens": summary_outputs,
        "summary_attempts": summary_attempts, "summary_failures": summary_failures,
        "summary_recorded_calls": summary_attempts - summary_failures,
        "all_arm_total_tokens": input_tokens + output_tokens + summary_inputs + summary_outputs,
        "api_request_attempts": request_budget.used - before,  # every charged request
        "agent_request_attempts": len(attempts),
        "auxiliary_request_attempts": (request_budget.used - before) - len(attempts),
        "agent_attempt_records": attempts,
        "model_input_traces": [base._input_trace(items) for items in inputs],
        "compression_events": sum(int(m.get("compression_count", 0)) for m in all_metrics),
        "k_recent_tool_rounds": K_RECENT_TOOL_ROUNDS,
        "guard_enabled": bool(first_guard.get("guard_enabled")),
        "guard_required_rounds": int(first_guard.get("guard_required_rounds", 0)),
        "guard_rejections": int(first_guard.get("guard_rejections", 0)),
        "guard_reached_required_rounds": bool(
            first_guard.get("guard_reached_required_rounds")
        ),
        "guard_exhausted": guard_exhausted,
        "guard_rejection_records": list(first_guard.get("guard_rejection_records") or []),
        "decider_guard": guard_records[1] if len(guard_records) > 1 else {},
        **pins,
        **recency,
    }
    row["failure_class"] = classify_failure(row, task)
    return row


def recovery_adapter_metrics_safe(recovery_llm: Any) -> dict[str, Any]:
    """The recovery call runs without an adapter in r16 (the guard covers the first role)."""
    return {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--task-ids", default=",".join(TASK_IDS))
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--provider-soft", type=int, default=1200)
    parser.add_argument("--provider-hard", type=int, default=3000)
    parser.add_argument("--provider-target", type=int, default=900)
    parser.add_argument("--soft-limit", type=int, default=0)
    parser.add_argument("--hard-limit", type=int, default=0)
    parser.add_argument("--target", type=int, default=0)
    parser.add_argument("--fixed-reserved-tokens", type=int, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--max-summary-tokens", type=int, default=1024)
    parser.add_argument("--max-summary-calls", type=int, default=4)
    parser.add_argument("--max-api-requests", type=int, default=280)
    parser.add_argument("--purpose", default=DEFAULT_PURPOSE)
    parser.add_argument("--guard-scope", choices=("all_arms", "pruner_only"),
                        default="all_arms" if GUARD_ALL_ARMS else "pruner_only")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--freeze", default="")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id",
                        default="crewai-two-role-r17-guarded-3arm-01")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    assert_reused_modules_are_pinned()
    if args.repeats < 1 or args.max_api_requests < 1:
        raise SystemExit("repeats and max-api-requests must be positive")
    known = load_tasks()
    selected = tuple(part.strip() for part in args.task_ids.split(",") if part.strip())
    tasks = [task for task in known if task["task_id"] in selected]
    if not tasks or set(selected) != {task["task_id"] for task in tasks}:
        raise SystemExit("unknown or missing task")
    requested = tuple(part.strip() for part in args.methods.split(",") if part.strip())
    if set(requested) != set(METHODS) or len(requested) != len(METHODS):
        raise SystemExit("multi-task batch requires all three arms")
    methods = METHODS
    args.guard_all_arms = args.guard_scope == "all_arms"
    plan = plumbing.balanced_plan(tasks, methods, args.repeats)
    budget, calibration = base.resolve_budget(args)
    units_per_arm = len(tasks) * args.repeats
    print(f"r17 guarded plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeats, {units_per_arm} units per arm, "
          f"{args.max_api_requests} global slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"arms: {list(methods)}; model {args.model}; mode {args.mode}")
    print(f"frozen view mechanism: r13 verbatim (K={K_RECENT_TOOL_ROUNDS}); "
          f"controller guard: required rounds = {len(tasks[0]['tools'])} per task, "
          f"max rejections {MAX_GUARD_REJECTIONS}")
    print(f"minimum agent requests: {2 * len(plan)}; global hard cap {args.max_api_requests}")
    print("acceptance: tool_sequence_consistent = "
          f"{units_per_arm}/{units_per_arm} per arm (guard on) and quality >= baseline")
    print("quality gates: " + ", ".join(QUALITY_GATES))
    if args.plan:
        print("No API request was sent in --plan mode.", flush=True)
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    key = base.os.getenv("OPENAI_API_KEY") or base.os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")
    freeze_path = Path(args.freeze) if args.freeze else None
    # Two distinct values, as the r15 amendment requires: the freeze file's own byte
    # hash, and the self-hash the freeze declares for its content (a file cannot contain
    # its own digest, so a freeze blanks ``freeze_sha256`` before hashing itself).
    freeze_file_sha256 = (
        hashlib.sha256(freeze_path.read_bytes()).hexdigest() if freeze_path else ""
    )
    freeze_declared_sha256 = ""
    if freeze_path:
        declared = json.loads(freeze_path.read_text(encoding="utf-8"))
        freeze_declared_sha256 = str(declared.get("freeze_sha256") or "")
    output = Path(args.out) / args.experiment_id
    resume = bool(args.resume)
    if output.exists() and not resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    done: set[tuple[str, int, str]] = set()
    if resume:
        existing = output / "results.jsonl"
        if not existing.is_file():
            raise SystemExit(f"--resume needs an existing results.jsonl: {existing}")
        for line in existing.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add((str(row["task_id"]), int(row["repeat"]), str(row["method"])))
    else:
        output.mkdir(parents=True)
    manifest = {
        "protocol": PROTOCOL,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "judge_sha256": hashlib.sha256(Path(judge.__file__).read_bytes()).hexdigest(),
        "mechanism_sha256": hashlib.sha256(Path(mechanism.__file__).read_bytes()).hexdigest(),
        "guard_sha256": hashlib.sha256(Path(guard_module.__file__).read_bytes()).hexdigest(),
        "pinned_evidence_sha256": hashlib.sha256(Path(pinned.__file__).read_bytes()).hexdigest(),
        "freeze_path": str(freeze_path).replace("\\", "/") if freeze_path else "",
        "freeze_sha256": freeze_file_sha256,
        "freeze_declared_self_sha256": freeze_declared_sha256,
        "tasks": [task["task_id"] for task in tasks],
        "methods": list(methods), "repeats": args.repeats,
        "units_per_arm": units_per_arm,
        "model": args.model, "mode": args.mode, "base_url": args.base_url,
        "budget": calibration.as_manifest(),
        "limits": {
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
            "max_output_tokens": args.max_output_tokens,
            "max_summary_tokens": args.max_summary_tokens,
            "max_summary_calls": args.max_summary_calls,
            "guard_max_rejections": MAX_GUARD_REJECTIONS,
        },
        "max_api_requests": args.max_api_requests,
        "quality_gates": QUALITY_GATES,
        "purpose": args.purpose,
        "guard_scope": args.guard_scope,
        "guard_installed_arms": (list(METHODS) if args.guard_all_arms
                                 else ["pruner_v1"]),
        "guard_enabled_all_arms": bool(args.guard_all_arms),
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "failure_policy": ("preserve sample failure and continue unless provider "
                           "unavailable or request cap"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    request_budget = base.RequestBudget(args.max_api_requests)
    client = (OpenAI(api_key=key, base_url=args.base_url, max_retries=0)
              if args.mode == "api" else None)
    rows: list[dict[str, Any]] = []
    try:
        with (output / "results.jsonl").open("a" if resume else "w", encoding="utf-8") as handle:
            for task, repeat, method in plan:
                if (task["task_id"], repeat, method) in done:
                    print(f"skip {task['task_id']} {repeat} {method} (already recorded)",
                          flush=True)
                    continue
                row = run_case(task, method, repeat, args.mode, client,
                               request_budget, args, budget)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                print(task["task_id"], repeat, method, row["strict_success"],
                      row["semantic_success"], row["first_pass_complete"],
                      row["tool_sequence_consistent"], row["failure_class"],
                      f"guard_rej={row['guard_rejections']}",
                      f"guard_ok={row['guard_reached_required_rounds']}",
                      row["all_arm_total_tokens"], row["error"], flush=True)
                if request_budget.used >= request_budget.limit:
                    break
                if any(term in row["error"].lower() for term in (
                    "apiconnectionerror", "apitimeouterror", "connection error",
                    "insufficient balance", "rate limit", "authenticationerror",
                )):
                    break
    finally:
        if client is not None:
            client.close()
    print(f"recorded {len(rows)} samples, requests used {request_budget.used}", flush=True)


def assert_reused_modules_are_pinned() -> None:
    """r17 §0.1: every reused module's hash must equal the pinned value, or refuse to run."""
    freeze_path = Path("integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01.json")
    authority: dict[str, str] = {}
    if freeze_path.is_file():
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        authority = dict(freeze.get("source_sha256") or {})
        amendment_path = freeze_path.with_name(
            freeze_path.stem + "_FREEZE_AMENDMENT_20261005.json"
        )
        if amendment_path.is_file():
            amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
            authority.update(amendment.get("pinned_sources_after_amendment") or {})
    problems = []
    for module in (guard_module, tasks_module):
        relative = f"experiments/runners/{Path(module.__file__).name}"
        frozen = authority.get(relative)
        current = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        if frozen is None:
            problems.append(f"{relative}: not pinned by the r17 freeze")
        elif current != frozen:
            problems.append(f"{relative}: {current[:12]} != pinned {frozen[:12]}")
    if problems:
        raise SystemExit(
            "refusing to run: a reused module is not at its pinned revision:\n  "
            + "\n  ".join(problems)
        )

if __name__ == "__main__":
    main()
