"""Prospective r4 two-role experiment with an explicit handoff quality gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import AsyncOpenAI, OpenAI

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import CrewAIContextAdapter
from experiments.runners import run_crewai_experiment as base
from experiments.runners.crewai_handoff_quality_v4 import assess_handoff


METHODS = ("none", "native_summary", "pruner_v1")
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks.json")
FIRST_ROLE = "Evidence investigator"
SECOND_ROLE = "Operations decision maker"
HANDOFF_FACT = {
    "incident_triage": ("degraded", "3.8"),
    "release_readiness": ("184", "0"),
    "customer_migration": ("eu-west", "02:00 UTC"),
}


def balanced_plan(tasks: list[dict[str, Any]], methods: tuple[str, ...], repeats: int
                  ) -> list[tuple[dict[str, Any], int, str]]:
    """Cycle arm order while preserving each task's exact input per repeat."""
    plan: list[tuple[dict[str, Any], int, str]] = []
    for task_index, task in enumerate(tasks):
        for repeat in range(repeats):
            offset = (task_index + repeat) % len(methods)
            rotated = methods[offset:] + methods[:offset]
            plan.extend((task, repeat, method) for method in rotated)
    return plan


def fixed_history(task: dict[str, Any]) -> list[dict[str, str]]:
    """A repeat never changes the prompt, history, or synthetic tool data."""
    history = base.build_history(task, 0)
    history[-1] = {
        "role": "user",
        "content": (
            f"调查阶段：{task['title']}。只调用当前唯一可用的工具一次，"
            "记录工具返回的当前事实。最终只输出一行以 HANDOFF 开头的交接内容，"
            "包含固定标识和工具关键数值；不要给出 RESULT 或最终 decision。"
        ),
    }
    return history


def _mock_stage_responses(task: dict[str, Any], stage: int, *, missing_fact: bool = False) -> list[str]:
    scripted = base._mock_responses(task)
    if stage == 0:
        if missing_fact:
            return [scripted[0], "Thought: I forgot the observation.\nFinal Answer: HANDOFF"]
        return [scripted[0], "Thought: the first tool returned current evidence.\nFinal Answer: HANDOFF " +
                str(task["expected_terms"][0]) + " " + " ".join(HANDOFF_FACT[str(task["task_id"])])]
    if stage == 2:
        return ["Thought: recover the already observed facts.\nFinal Answer: HANDOFF " +
                str(task["expected_terms"][0]) + " " + " ".join(HANDOFF_FACT[str(task["task_id"])])]
    return [scripted[1], scripted[-1]]


def _make_llm(method: str, mode: str, task: dict[str, Any], stage: int,
              client: Any, request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: ContextBudget, *, missing_fact: bool = False) -> base.RecordingCrewAILLM:
    if mode == "mock":
        return base.ReplayCrewAILLM(_mock_stage_responses(task, stage, missing_fact=missing_fact))
    if method == "native_summary":
        key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        return base.NativeSummaryCrewAILLM(
            client=client, model=args.model, request_budget=request_budget,
            max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
            summary_client_factory=lambda: AsyncOpenAI(
                api_key=key, base_url=args.base_url, timeout=60.0, max_retries=0,
            ),
            summary_model=args.model, soft_limit_tokens=budget.soft_limit_tokens,
            hard_limit_tokens=budget.hard_limit_tokens,
            target_tokens=budget.target_tokens or 1,
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            max_summary_tokens=args.max_summary_tokens,
            max_summary_calls=args.max_summary_calls,
        )
    return base.OpenAICompatCrewAILLM(
        client=client, model=args.model, request_budget=request_budget,
        max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
    )


def _latest_observation(inputs: list[list[dict[str, Any]]]) -> str:
    """Recover exact JSON already seen by the role; do not regenerate tool facts."""
    for call in reversed(inputs):
        for message in reversed(call):
            content = str(message.get("content", ""))
            position = content.rfind("Observation:")
            if position < 0:
                continue
            payload = content[position + len("Observation:"):].lstrip()
            try:
                value, _ = json.JSONDecoder().raw_decode(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return ""


def run_case(task: dict[str, Any], method: str, repeat: int, mode: str,
             client: Any, request_budget: base.RequestBudget,
             args: argparse.Namespace, budget: ContextBudget) -> dict[str, Any]:
    inputs: list[list[dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    role_outputs: list[str] = []
    role_metrics: list[dict[str, Any]] = []
    role_traces: list[list[str]] = []
    handoff_first_pass: dict[str, Any] | None = None
    handoff_recovery: dict[str, Any] | None = None
    handoff_for_decider = ""
    recovery_observation = ""
    recovery_invocations = 0
    recovery_api_attempts = 0
    recovery_metrics: dict[str, Any] = {}
    summary_attempts = summary_inputs = summary_outputs = summary_failures = 0
    error = ""
    before = request_budget.used
    for stage, role in enumerate((FIRST_ROLE, SECOND_ROLE)):
        trace: list[str] = []
        mock_missing = (mode == "mock" and stage == 0 and
                        task["task_id"] == "incident_triage" and repeat == 2 and
                        method == "pruner_v1")
        llm = _make_llm(method, mode, task, stage, client, request_budget, args,
                        budget, missing_fact=mock_missing)
        stage_tool = [str(task["tools"][stage])]
        adapter = base.TriggeredCrewAIContextAdapter(
            ContextPluginConfig(enabled=method == "pruner_v1",
                                method="pruner_v1" if method == "pruner_v1" else "none",
                                budget=budget),
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            agent_roles=[role], trigger_soft_limit_tokens=budget.soft_limit_tokens,
        )
        agent = Agent(
            role=role,
            goal=("Use the supplied current tool, then pass a factual HANDOFF without deciding."
                  if stage == 0 else
                  "Use the supplied current tool and handoff, then return the exact RESULT contract."),
            backstory="You are a careful synthetic operations specialist. Current tool facts take priority.",
            llm=llm, tools=base._build_tools(stage_tool, trace),
            allow_delegation=False, max_iter=4, verbose=False,
            respect_context_window=False,
        )
        if stage == 0:
            prompt = fixed_history(task)
        else:
            prompt = [
                {"role": "user", "content": str(task["history_constraint"])},
                {"role": "user", "content": "Investigator handoff: " + handoff_for_decider},
                {"role": "user", "content": base._latest_task_prompt(task)},
            ]
        try:
            if method == "pruner_v1":
                with adapter.attached():
                    output = str(agent.kickoff(prompt)).strip()
            else:
                output = str(agent.kickoff(prompt)).strip()
            if not output:
                raise base.EmptyModelResponseError("empty role output")
            role_outputs.append(output)
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
            if isinstance(llm, base.NativeSummaryCrewAILLM):
                metrics = llm.metrics_dict()
                summary_attempts += int(metrics["native_summary_attempts"])
                summary_inputs += int(metrics["native_summary_input_tokens"])
                summary_outputs += int(metrics["native_summary_output_tokens"])
                summary_failures += int(metrics["native_summary_failures"])
        if error:
            break
        if stage == 0:
            assessment = assess_handoff(str(task["task_id"]), role_outputs[0])
            handoff_first_pass = assessment.as_record()
            handoff_for_decider = assessment.canonical or ""
            if assessment.needs_recovery:
                recovery_observation = _latest_observation(llm.inputs)
                if not recovery_observation:
                    error = "HandoffRecoveryError: recorded tool observation missing"
                    break
                recovery_invocations = 1
                recovery_before = request_budget.used
                recovery_llm = _make_llm(method, mode, task, 2, client,
                                         request_budget, args, budget)
                recovery_adapter = base.TriggeredCrewAIContextAdapter(
                    ContextPluginConfig(
                        enabled=method == "pruner_v1",
                        method="pruner_v1" if method == "pruner_v1" else "none",
                        budget=budget,
                    ),
                    task_state=str(task["history_constraint"]),
                    fixed_reserved_tokens=args.fixed_reserved_tokens,
                    agent_roles=[FIRST_ROLE],
                    trigger_soft_limit_tokens=budget.soft_limit_tokens,
                )
                recovery_agent = Agent(
                    role=FIRST_ROLE,
                    goal="Restate only the already observed tool facts as a single factual HANDOFF line.",
                    backstory="You are correcting a handoff from a recorded current observation.",
                    llm=recovery_llm, tools=[], allow_delegation=False,
                    max_iter=1, verbose=False, respect_context_window=False,
                )
                recovery_prompt = [{"role": "user", "content": (
                    "Your first handoff omitted required evidence. The tool has already run; "
                    "do not call any tool. Return one line beginning HANDOFF with the "
                    "current observation's required facts. No decision. "
                    f"Fixed task identifiers: {task['history_constraint']}\n"
                    f"Previous output: {role_outputs[0]}\n"
                    f"Recorded current observation: {recovery_observation}"
                )}]
                try:
                    if method == "pruner_v1":
                        with recovery_adapter.attached():
                            recovery_raw = str(recovery_agent.kickoff(recovery_prompt)).strip()
                    else:
                        recovery_raw = str(recovery_agent.kickoff(recovery_prompt)).strip()
                    handoff_recovery = assess_handoff(str(task["task_id"]), recovery_raw).as_record()
                    handoff_for_decider = handoff_recovery["canonical"] or ""
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
                    recovery_metrics = recovery_adapter.metrics_dict()
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
    correct = (not error and len(role_outputs) == 2
               and handoff_for_decider.startswith("HANDOFF ")
               and all(term.lower() in handoff_for_decider.lower()
                       for term in HANDOFF_FACT[str(task["task_id"])])
               and all(Counter(trace) == Counter([str(task["tools"][i])])
                       for i, trace in enumerate(role_traces))
               and base._answer_correct(str(task["task_id"]), final.lower(),
                                        task["expected_terms"])
               and final.startswith(f"RESULT task={task['task_id']} ")
               and "\n" not in final)
    role_safe = all(int(metrics.get(key, 0)) == 0
                    for metrics in [*role_metrics, recovery_metrics]
                    for key in ("crewai_group_restore_failure_count",
                                "crewai_unmatched_call_count",
                                "crewai_active_pending_view_count"))
    input_tokens = sum(int(record.get("input_tokens", 0)) for record in records)
    output_tokens = sum(int(record.get("output_tokens", 0)) for record in records)
    return {
        "task_id": task["task_id"], "repeat": repeat, "method": method,
        "success": bool(correct and role_safe), "role_safe": role_safe,
        "role_outputs": role_outputs, "role_tool_traces": role_traces,
        "role_metrics": role_metrics, "error": error,
        "handoff_first_pass": handoff_first_pass,
        "handoff_recovery": handoff_recovery,
        "handoff_for_decider": handoff_for_decider,
        "recovery_observation": recovery_observation,
        "recovery_invocations": recovery_invocations,
        "recovery_api_attempts": recovery_api_attempts,
        "recovery_metrics": recovery_metrics,
        "agent_input_tokens": input_tokens, "agent_output_tokens": output_tokens,
        "summary_input_tokens": summary_inputs, "summary_output_tokens": summary_outputs,
        "summary_attempts": summary_attempts, "summary_failures": summary_failures,
        "summary_recorded_calls": summary_attempts - summary_failures,
        "all_arm_total_tokens": input_tokens + output_tokens + summary_inputs + summary_outputs,
        "api_request_attempts": request_budget.used - before,
        "agent_attempt_records": attempts,
        "model_input_traces": [base._input_trace(items) for items in inputs],
        "compression_events": sum(int(m.get("compression_count", 0)) for m in
                                  [*role_metrics, recovery_metrics]),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--repeats", type=int, default=1)
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
    parser.add_argument("--max-api-requests", type=int, default=24)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-two-role-r4-mock-gate")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.max_api_requests < 1:
        raise SystemExit("repeats and max-api-requests must be positive")
    tasks = base.load_tasks(TASK_FILE)
    selected = set(filter(None, (part.strip() for part in args.task_ids.split(","))))
    if selected:
        tasks = [task for task in tasks if task["task_id"] in selected]
    if not tasks:
        raise SystemExit("no tasks selected")
    methods = tuple(method for method in METHODS if method in args.methods.split(","))
    if not methods or set(args.methods.split(",")) - set(METHODS):
        raise SystemExit("unknown method")
    # Balance arm order across fixed-input repeats without changing any task
    # payload.  The r2 single-repeat batch remains reproducible from its frozen
    # runner snapshot in that batch directory.
    plan = balanced_plan(tasks, methods, args.repeats)
    print(f"Two-role fixed-input plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeats, {args.max_api_requests} global request slots")
    if args.plan:
        print("No API request was sent in --plan mode.")
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")
    output = Path(args.out) / args.experiment_id
    if output.exists():
        raise SystemExit(f"experiment directory already exists: {output}")
    budget, calibration = base.resolve_budget(args)
    output.mkdir(parents=True)
    manifest = {
        "protocol": "crewai-two-role-r4-handoff-quality",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "tasks": [task["task_id"] for task in tasks], "methods": methods,
        "repeats": args.repeats, "model": args.model, "mode": args.mode,
        "base_url": args.base_url,
        "budget": calibration.as_manifest(),
        "limits": {
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
            "max_output_tokens": args.max_output_tokens,
            "max_summary_tokens": args.max_summary_tokens,
            "max_summary_calls": args.max_summary_calls,
        },
        "max_api_requests": args.max_api_requests,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    request_budget = base.RequestBudget(args.max_api_requests)
    client = OpenAI(api_key=key, base_url=args.base_url, max_retries=0) if args.mode == "api" else None
    try:
        with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
            for task, repeat, method in plan:
                row = run_case(task, method, repeat, args.mode, client, request_budget, args, budget)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(task["task_id"], repeat, method, row["success"], row["all_arm_total_tokens"])
                if row["error"] or request_budget.used >= request_budget.limit:
                    break
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
