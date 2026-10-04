"""CrewAI r9 two-role multi-task batch: pinned required evidence (mechanism v9).

Relationship to the frozen r8 mechanism
--------------------------------------
r9 is a **mechanism ablation**: it reuses the r8 judge, the r8 task file
(``tasks/stage5_autogen/natural_tasks_r8.json``), the r8 three arms, the r8
budget, the r8 sampling matrix and the same three tasks on purpose, so the
mechanism below is the only variable between the two batches.  No r8 file,
batch directory, freeze or stored answer is modified or re-scored, and r8's
stored answers are never re-judged here.

The single change: the plugin arm's model view is now built by
``experiments/runners/crewai_pinned_evidence_v9.py``, which wraps the frozen
middleware and deterministically pins (a) every history/tool message that
carries a required ``handle_facts`` literal, (b) the first-role task statement
that carries the frozen ``HANDOFF`` output contract, and (c) falls back to the
whole uncompressed prefix -- counted -- if a required literal would still be
missing.  Everything else, including the metered no-tool remedy path, is
unchanged: the remedy is still available and still charged, so a first-pass
regression cannot hide inside a cheaper-looking arm.

New first-class gate metric
---------------------------
``first_pass_complete`` is recorded per sample and re-derived independently by
``experiments/audits/audit_crewai_handoff_v9.py`` from the recorded raw
first-role output.  Its frozen definition (protocol
``integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R9_MULTITASK_01.md``):

    a sample is first-pass complete when the first-role HANDOFF line emitted
    **before any remedy call** carries every literal of the frozen
    ``handle_facts`` list of its task under the frozen semantic judge, and the
    line is single-line and within the 450-character contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import AsyncOpenAI, OpenAI

from context_pruner import ContextPluginConfig
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as plumbing
from experiments.runners import run_crewai_handoff_v8 as v8

METHODS = v8.METHODS
TASK_FILE = judge.TASK_FILE
PROTOCOL = "crewai-two-role-r9-pinned-required-evidence"
FIRST_ROLE = v8.FIRST_ROLE
SECOND_ROLE = v8.SECOND_ROLE
QUALITY_GATES = ["strict", "semantic", "first_pass_facts"]

load_tasks = v8.load_tasks
build_tools = v8.build_tools
fixed_history = v8.fixed_history
latest_task_prompt = v8.latest_task_prompt
_all_observations = v8._all_observations
_make_llm = v8._make_llm


def _pinned_adapter(
    task: dict[str, Any],
    method: str,
    role: str,
    args: argparse.Namespace,
    budget: Any,
) -> Any:
    """The r8 adapter with the v9 pinning middleware installed.

    Only the ``pruner_v1`` arm is enabled (as in r8); the pinning middleware is
    attached in every arm so that ``none`` and ``native_summary`` stay exactly
    as they were -- their plugin is inactive and the pin is a no-op because the
    required facts are already served verbatim.
    """
    config = ContextPluginConfig(
        enabled=method == "pruner_v1",
        method="pruner_v1" if method == "pruner_v1" else "none",
        budget=budget,
    )
    return pinned.create_pinned_adapter(
        config,
        rule=pinned.pinned_rule(task),
        budget=budget,
        task_state=str(task["history_constraint"]),
        fixed_reserved_tokens=args.fixed_reserved_tokens,
        agent_roles=[role],
    )


def _pin_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "pinned_events",
        "pinned_message_total",
        "pinned_characters_total",
        "required_fact_whole_prefix_fallbacks",
        "required_facts_missing_after_pin_total",
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


def run_case(task: dict[str, Any], method: str, repeat: int, mode: str,
             client: Any, request_budget: base.RequestBudget,
             args: argparse.Namespace, budget: Any,
             *, force_missing_handoff: bool = False) -> dict[str, Any]:
    """The r8 case flow with the pinned middleware and the new gate metric.

    The flow itself is copied verbatim from the frozen r8 runner
    (``run_crewai_handoff_v8.run_case``) except for the adapter factory and the
    added pin/first-pass bookkeeping, so r9 cannot differ from r8 by accident in
    call order, remedy policy, budgeting or scoring.
    """
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
    before = request_budget.used
    handle_rule = judge.handle_rule_for([task], str(task["task_id"]))
    for stage, role in enumerate((FIRST_ROLE, SECOND_ROLE)):
        trace: list[str] = []
        mock_missing = (
            force_missing_handoff
            or (mode == "mock" and stage == 0
                and task["task_id"] == "queue_backlog_replay" and repeat == 2
                and method == "pruner_v1")
        )
        llm = _make_llm(method, mode, task, stage, client, request_budget, args,
                        budget, trace, missing_fact=mock_missing)
        stage_tools = (
            [str(tool["name"]) for tool in task["tools"]]
            if stage == 0
            else [str(task["tools"][-1]["name"])]
        )
        adapter = _pinned_adapter(task, method, role, args, budget)
        agent = Agent(
            role=role,
            goal=("Use every supplied current tool, then pass a factual HANDOFF "
                  "without deciding." if stage == 0 else
                  "Use the supplied current tool and handoff, then return the exact "
                  "RESULT contract."),
            backstory=("You are a careful synthetic operations specialist. Current tool "
                       "facts take priority over historical discussion."),
            llm=llm, tools=build_tools(task, stage_tools, trace),
            allow_delegation=False, max_iter=6, verbose=False,
            respect_context_window=False,
        )
        if stage == 0:
            prompt = fixed_history(task, repeat)
        else:
            prompt = [
                {"role": "user", "content": str(task["history_constraint"])},
                {"role": "user", "content": "Investigator handoff: " + handoff_for_decider},
                {"role": "user", "content": latest_task_prompt(task)},
            ]
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
            if isinstance(llm, base.NativeSummaryCrewAILLM):
                metrics = llm.metrics_dict()
                summary_attempts += int(metrics["native_summary_attempts"])
                summary_inputs += int(metrics["native_summary_input_tokens"])
                summary_outputs += int(metrics["native_summary_output_tokens"])
                summary_failures += int(metrics["native_summary_failures"])
        if error:
            break
        if stage == 0:
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
                recovery_llm = _make_llm(method, mode, task, 2, client,
                                         request_budget, args, budget, [])
                recovery_adapter = _pinned_adapter(task, method, FIRST_ROLE, args, budget)
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
                    if method == "pruner_v1":
                        with recovery_adapter.attached():
                            recovery_raw = str(recovery_agent.kickoff(recovery_prompt))
                    else:
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
    role_safe = all(int(metrics.get(key, 0)) == 0
                    for metrics in [*role_metrics, recovery_metrics]
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
    answer_verdict = judge.judge_answer(
        judge.answer_rule_for([task], str(task["task_id"])), final
    )
    first_pass_verdict = judge.judge_handoff(handle_rule, role_raw_outputs[0]) if role_raw_outputs else None
    first_pass_complete = bool(
        first_pass_verdict is not None
        and not first_pass_verdict.semantic["missing_facts"]
        and first_pass_verdict.semantic["one_line"]
    )
    strict_success = bool(
        not error and len(role_outputs) == 2 and tool_evidence_ok and role_safe
        and bool(handoff_for_decider)
        and answer_verdict.strict_pass
    )
    semantic_success = bool(
        not error and len(role_outputs) == 2 and tool_evidence_ok and role_safe
        and bool(handoff_for_decider)
        and answer_verdict.semantic_pass
    )
    input_tokens = sum(int(record.get("input_tokens", 0)) for record in records)
    output_tokens = sum(int(record.get("output_tokens", 0)) for record in records)
    all_metrics = [*role_metrics, recovery_metrics]
    pins = _pin_metrics(all_metrics)
    return {
        "task_id": task["task_id"], "repeat": repeat, "method": method,
        "success": strict_success,
        "strict_success": strict_success,
        "semantic_success": semantic_success,
        "first_pass_complete": first_pass_complete,
        "first_pass_missing_facts": list(
            (first_pass_verdict.semantic["missing_facts"] if first_pass_verdict else [])
        ),
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
        "api_request_attempts": request_budget.used - before,
        "agent_attempt_records": attempts,
        "model_input_traces": [base._input_trace(items) for items in inputs],
        "compression_events": sum(int(m.get("compression_count", 0)) for m in all_metrics),
        **pins,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument(
        "--task-ids",
        default="queue_backlog_replay,region_failover,schema_migration",
    )
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--repeats", type=int, default=3)
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
    parser.add_argument("--max-api-requests", type=int, default=200)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-two-role-r9-multitask-01")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted batch in the same directory")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
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
    plan = plumbing.balanced_plan(tasks, methods, args.repeats)
    budget, calibration = base.resolve_budget(args)
    print(f"r9 pinned-evidence plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeats, {args.max_api_requests} global request slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"arms: {list(methods)}; model {args.model}; mode {args.mode}")
    print(f"budget provider soft/hard/target: {calibration.soft_provider}/"
          f"{calibration.hard_provider}/{calibration.target_provider}; "
          f"estimated equivalents: {calibration.as_manifest()['estimated_tokens']}")
    print("pinned rule per task: "
          + "; ".join(f"{rule.task_id}={list(rule.required_literals)}"
                      for rule in (pinned.pinned_rule(task) for task in tasks)))
    print(f"minimum agent requests: {2 * len(plan)}; "
          f"summary cap: {args.max_summary_calls} per role; "
          f"global hard cap: {args.max_api_requests}")
    print("quality gates: strict, semantic, first_pass_facts "
          "(first_pass_facts is frozen before this batch)")
    if args.plan:
        print("No API request was sent in --plan mode.", flush=True)
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    key = base.os.getenv("OPENAI_API_KEY") or base.os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")
    output = Path(args.out) / args.experiment_id
    resume = bool(args.resume)
    if output.exists() and not resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    done: set[tuple[str, int, str]] = set()
    mode_file = "a" if resume else "w"
    if resume:
        existing = output / "results.jsonl"
        if not existing.is_file():
            raise SystemExit(f"--resume needs an existing results.jsonl: {existing}")
        for line in existing.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line from the interrupted run is re-run
            done.add((str(row["task_id"]), int(row["repeat"]), str(row["method"])))
        print(f"resume: {len(done)} completed samples already recorded", flush=True)
    else:
        output.mkdir(parents=True)
    manifest = {
        "protocol": PROTOCOL,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "judge_sha256": hashlib.sha256(Path(judge.__file__).read_bytes()).hexdigest(),
        "mechanism_sha256": hashlib.sha256(Path(pinned.__file__).read_bytes()).hexdigest(),
        "tasks": [task["task_id"] for task in tasks],
        "methods": list(methods), "repeats": args.repeats,
        "model": args.model, "mode": args.mode, "base_url": args.base_url,
        "budget": calibration.as_manifest(),
        "limits": {
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
            "max_output_tokens": args.max_output_tokens,
            "max_summary_tokens": args.max_summary_tokens,
            "max_summary_calls": args.max_summary_calls,
        },
        "max_api_requests": args.max_api_requests,
        "quality_gates": QUALITY_GATES,
        "failure_policy": ("preserve sample failure and continue unless provider "
                           "unavailable or request cap"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    request_budget = base.RequestBudget(args.max_api_requests)
    client = (OpenAI(api_key=key, base_url=args.base_url, max_retries=0)
              if args.mode == "api" else None)
    try:
        with (output / "results.jsonl").open(mode_file, encoding="utf-8") as handle:
            for task, repeat, method in plan:
                if (task["task_id"], repeat, method) in done:
                    print(f"skip {task['task_id']} {repeat} {method} (already recorded)",
                          flush=True)
                    continue
                row = run_case(task, method, repeat, args.mode, client,
                               request_budget, args, budget)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(task["task_id"], repeat, method, row["strict_success"],
                      row["semantic_success"], row["first_pass_complete"],
                      row["pinned_events"],
                      row["required_fact_whole_prefix_fallbacks"],
                      row["all_arm_total_tokens"], row["error"], flush=True)
                if request_budget.used >= request_budget.limit:
                    break
                error = row["error"].lower()
                if any(term in error for term in (
                    "apiconnectionerror", "apitimeouterror", "connection error",
                    "insufficient balance", "rate limit", "authenticationerror",
                )):
                    break
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
