"""CrewAI r8 two-role multi-task batch: new tasks, strict + semantic quality.

Structure (unchanged from the frozen r4 multi-task runner so the arms stay
comparable):

  * three arms -- ``none``, ``native_summary``, ``pruner_v1``;
  * a balanced plan that rotates arm order per task/repeat without changing any
    payload, so a repeat always sends exactly the same prompt and tool data;
  * per-sample persistence: every row is written and flushed before the next
    sample starts, and a sample failure is recorded rather than retried away;
  * one global API request cap covering agent calls, native summaries and
    handoff-recovery calls alike (a summary request is charged to its own arm
    *and* to the global ledger);
  * thinking explicitly disabled on every request.

New in r8:

  * a new frozen task file ``tasks/stage5_autogen/natural_tasks_r8.json`` with
    task ids, facts, tools, regions and version identifiers that never took part
    in r3/r4;
  * both quality gates are recorded per sample: the strict contract and the
    prospective semantic-equivalence judge
    (``experiments/runners/crewai_semantic_equivalence_v8.py``).

The v4 modules are imported for the provider plumbing only.  Their frozen
scoring is not used and no old batch is re-scored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import AsyncOpenAI, OpenAI

from context_pruner import ContextBudget, ContextPluginConfig
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as plumbing


METHODS = ("none", "pruner_v1", "native_summary")
TASK_FILE = judge.TASK_FILE
PROTOCOL = "crewai-two-role-r8-handoff-semantic-quality"
FIRST_ROLE = "Evidence investigator"
SECOND_ROLE = "Operations decision maker"


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return judge.load_tasks(path or TASK_FILE)


def _tool_result(task: dict[str, Any], name: str) -> dict[str, Any]:
    for tool in task["tools"]:
        if tool["name"] == name:
            return dict(tool["result"])
    raise KeyError(f"unknown tool {name} for {task['task_id']}")


def build_tools(task: dict[str, Any], selected: list[str], trace: list[str]) -> list[Any]:
    """Build exactly the task's tools; every tool returns the frozen fixture."""
    from crewai.tools import tool as crewai_tool

    def make(name: str) -> Any:
        payload = _tool_result(task, name)

        def call(**kwargs: Any) -> str:
            trace.append(name)
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)

        call.__name__ = name
        call.__doc__ = f"Return the frozen current facts of {name} for this synthetic task."
        return crewai_tool(name)(call)

    registry = {tool["name"]: make(tool["name"]) for tool in task["tools"]}
    unknown = [name for name in selected if name not in registry]
    if unknown:
        raise ValueError(f"unknown tools for {task['task_id']}: {unknown}")
    return [registry[name] for name in selected]


def latest_task_prompt(task: dict[str, Any]) -> str:
    """The r5 decision contract, sent as the last user message of both roles.

    The decision alternatives and the required fact literals come from the frozen
    task file, so both quality gates check exactly what the role was asked for.
    """
    return (
        f"{task['task']} Call every available tool exactly once before answering. "
        "Never invent tool results. Return exactly one ASCII line of at most 450 characters: "
        f"RESULT task={task['task_id']} decision=<{task['decision']}> "
        "evidence=<the key facts and values returned by the tools>. "
        "Do not output angle brackets. Keep every identifier, count, region, version, "
        "time window and status word exactly as the current tool evidence wrote it, and "
        "state the effective result of every failed check in the form 0 failed."
    )


def fixed_history(task: dict[str, Any], repeat: int) -> list[dict[str, str]]:
    """The investigator's prompt; a repeat never changes prompt, history or data."""
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages: list[dict[str, str]] = [
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
    tools = "、".join(str(tool["name"]) for tool in task["tools"])
    messages.append(
        {
            "role": "user",
            "content": (
                f"调查阶段：{task['title']}。按顺序对每个可用工具各调用一次（{tools}），"
                "记录工具返回的当前事实。最终只输出一行以 HANDOFF 开头的交接内容，"
                "包含固定标识和工具关键数值；不要给出最终 decision。"
            ),
        }
    )
    return messages


def _observations_from_content(content: str) -> list[str]:
    """Every JSON tool observation inside one recorded message, oldest first."""
    found: list[str] = []
    position = content.find("Observation:")
    while position >= 0:
        payload = content[position + len("Observation:"):].lstrip()
        try:
            value, end = json.JSONDecoder().raw_decode(payload)
        except json.JSONDecodeError:
            position = content.find("Observation:", position + 1)
            continue
        if isinstance(value, dict):
            found.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        position = content.find("Observation:", position + len("Observation:") + end)
    return found


def _all_observations(inputs: list[list[dict[str, Any]]]) -> list[str]:
    """All tool observations the role already saw, in call order.

    The r5 recovery prompt used only the newest observation, so a handoff that
    dropped an earlier tool's fact could not be repaired.  Every observation is
    now available to the recovery step, and nothing is re-regenerated.
    """
    observations: list[str] = []
    for call in inputs:
        for message in call:
            observations.extend(
                _observations_from_content(str(message.get("content", "")))
            )
    return observations


def _expected_answer(task: dict[str, Any]) -> str:
    """The mock answer: the decider's own tool result, in the natural value form.

    The live model writes ``pending_records=12400`` rather than the JSON
    ``"pending_records": 12400``, so the mock (and therefore the zero-API gate)
    writes the same natural form.
    """
    evidence = (
        judge.tool_evidence(task)[-1]
        .replace('": ', '=')
        .replace('"', "")
    )
    return f"RESULT task={task['task_id']} decision={task['decision']} evidence={evidence}"


def _mock_stage_responses(
    task: dict[str, Any], stage: int, trace: list[str], *, missing_fact: bool = False
) -> list[str]:
    if stage == 0:
        responses: list[str] = []
        for tool in task["tools"]:
            responses.append(
                "Thought: I must verify current evidence with the next required tool.\n"
                f"Action: {tool['name']}\n"
                f"Action Input: {json.dumps(tool['call'], ensure_ascii=False)}"
            )
        if missing_fact:
            responses.append("Thought: I forgot the observation.\nFinal Answer: HANDOFF")
        else:
            responses.append(
                "Thought: the tools returned the current evidence.\nFinal Answer: HANDOFF "
                + " ".join(str(fact) for fact in task["handle_facts"])
            )
        return responses
    if stage == 2:
        return [
            "Thought: restate the already observed facts.\nFinal Answer: HANDOFF "
            + " ".join(str(fact) for fact in task["handle_facts"])
        ]
    # The decider exercises its own tool once before answering.
    tool = task["tools"][-1]
    return [
        "Thought: I must verify the current fact with my own tool.\n"
        f"Action: {tool['name']}\n"
        f"Action Input: {json.dumps(tool['call'], ensure_ascii=False)}",
        "Thought: I have all required current evidence.\nFinal Answer: "
        + _expected_answer(task),
    ]


def _make_llm(method: str, mode: str, task: dict[str, Any], stage: int,
              client: Any, request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: ContextBudget, trace: list[str], *, missing_fact: bool = False
              ) -> base.RecordingCrewAILLM:
    if mode == "mock":
        return base.ReplayCrewAILLM(
            _mock_stage_responses(task, stage, trace, missing_fact=missing_fact)
        )
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


def run_case(task: dict[str, Any], method: str, repeat: int, mode: str,
             client: Any, request_budget: base.RequestBudget,
             args: argparse.Namespace, budget: ContextBudget,
             *, force_missing_handoff: bool = False) -> dict[str, Any]:
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
    return {
        "task_id": task["task_id"], "repeat": repeat, "method": method,
        "success": strict_success,
        "strict_success": strict_success,
        "semantic_success": semantic_success,
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
        "compression_events": sum(int(m.get("compression_count", 0)) for m in
                                  [*role_metrics, recovery_metrics]),
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
    parser.add_argument("--experiment-id", default="crewai-two-role-r8-multitask-01")
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
    print(f"r8 multi-task plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeats, {args.max_api_requests} global request slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"arms: {list(methods)}; model {args.model}; mode {args.mode}")
    print(f"budget provider soft/hard/target: {calibration.soft_provider}/"
          f"{calibration.hard_provider}/{calibration.target_provider}; "
          f"estimated equivalents: {calibration.as_manifest()['estimated_tokens']}")
    print(f"minimum agent requests: {2 * len(plan)}; "
          f"summary cap: {args.max_summary_calls} per role; "
          f"global hard cap: {args.max_api_requests}")
    if args.plan:
        print("No API request was sent in --plan mode.", flush=True)
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
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
        "quality_gates": ["strict", "semantic"],
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
                      row["semantic_success"], row["all_arm_total_tokens"],
                      row["error"], flush=True)
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
