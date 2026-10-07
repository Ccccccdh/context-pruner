"""CrewAI r16 payload acquisition: the uncompressed arm only, to obtain real host payloads.

Why this batch exists
---------------------
The r16 preparation round ended with exactly one gap that blocks a paid three-arm pilot:
**the four r16 tasks have no real CrewAI host payload**, so the corrected call-wise bound
(``SUM_calls(uncompressed_input - compressed_input) - protection``) cannot be computed on
anything but a projection -- and a projection must not admit a paid batch.

So this batch does exactly one thing: run the four r16 tasks with the **uncompressed arm
only** (``--methods none``, one repeat) and record, per model call, the real host payload:

  * ``first_role_payloads`` / ``decider_payloads``: for every model call, the ordered
    message roles plus each message's sha256 and character count, and the full text of the
    **first** call of each role;
  * ``first_role_frame_deltas`` / ``decider_frame_deltas``: per-call message count, frame
    characters and provider input tokens;
  * the tool trace, the handoff line, the final answer and the frozen judge's verdicts.

Nothing else:

  * **no plugin arm, no native-summary arm, no controller guard on the measured path** --
    there is no compression here, so this batch can never be cited as a saving, and it is
    not a quality-equivalence test;
  * the frozen r8 judge labels each sample so the payload is self-describing; scoring is
    unchanged;
  * no r9-r15 file, freeze, directory or stored answer is touched.

The guard is deliberately **not** installed on the acquisition path: this batch measures
the *uncompressed* host behaviour, which is what the bound's first term needs.  The guard's
effect belongs to the three-arm pilot, not here.

Stop line: the global request cap, an empty model response, or a connection-class error.
Failures and cap-limited samples are recorded and counted, never dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import OpenAI

from context_pruner import ContextPluginConfig
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as plumbing

ARM = "none"
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks_r16.json")
PROTOCOL = "crewai-r16-payload-acquisition-uncompressed"
TASK_IDS = (
    "artifact_publish_gate",
    "quota_scale_gate",
    "traffic_shift_gate",
    "failover_switch_gate",
)
REPEATS = 1
FILLER_PAIRS = 14
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
DEFAULT_MAX_API_REQUESTS = 120


def _source() -> Any:
    """The r16 runner, imported for its frozen task loader and prompt shapes."""
    from experiments.runners import run_crewai_handoff_v16 as v16

    return v16


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return judge.load_tasks(path or TASK_FILE)


def fixed_history(task: dict[str, Any], repeat: int) -> list[dict[str, str]]:
    """The r16 investigator prompt shape, verbatim from the r16 runner."""
    return _source().fixed_history(task, repeat)


def decider_role_prompt(task: dict[str, Any]) -> str:
    return _source().decider_role_prompt(task)


def _message_fingerprint(message: Any, *, index: int) -> dict[str, Any]:
    if isinstance(message, dict):
        content = str(message.get("content", "") or "")
        role = str(message.get("role", "") or "")
    else:
        content = str(getattr(message, "content", "") or "")
        role = str(getattr(message, "role", "") or "")
    return {
        "index": index,
        "role": role,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "characters": len(content),
    }


def _payload_replay(call: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "message_count": len(call),
        "message_fingerprints": [
            _message_fingerprint(message, index=index)
            for index, message in enumerate(call)
        ],
        "frame_character_total": sum(
            len(str(message.get("content", "") or "")) for message in call
        ),
    }


def _first_call_text(call: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The verbatim messages of one call (bounded: only the first call of a role)."""
    return [
        {
            "role": str(message.get("role", "") or ""),
            "content": str(message.get("content", "") or "")[:20000],
        }
        for message in call
    ]


def _tools_for(task: dict[str, Any], stage: int) -> list[str]:
    return (
        [str(tool["name"]) for tool in task["tools"]]
        if stage == 0 else [str(task["tools"][-1]["name"])]
    )


def run_case(task: dict[str, Any], repeat: int, mode: str, client: Any,
             request_budget: base.RequestBudget, args: argparse.Namespace,
             budget: Any) -> dict[str, Any]:
    source = _source()
    inputs: list[list[dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    role_outputs: list[str] = []
    role_raw_outputs: list[str] = []
    role_traces: list[list[str]] = []
    handoff_first_pass: dict[str, Any] | None = None
    handoff_for_decider = ""
    error = ""
    before = request_budget.used
    handle_rule = judge.handle_rule_for([task], str(task["task_id"]))
    payloads: dict[str, list[dict[str, Any]]] = {"first_role": [], "decider": []}
    frame_deltas: dict[str, list[dict[str, Any]]] = {"first_role": [], "decider": []}
    for stage, role in enumerate((source.FIRST_ROLE, source.SECOND_ROLE)):
        trace: list[str] = []
        llm = _make_llm(mode, task, stage, client, request_budget, args, budget, trace)
        stage_tools = _tools_for(task, stage)
        adapter = base.TriggeredCrewAIContextAdapter(
            ContextPluginConfig(enabled=False, method="none", budget=budget),
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            agent_roles=[role],
            trigger_soft_limit_tokens=budget.soft_limit_tokens,
        )
        agent = Agent(
            role=role,
            goal=("Use every supplied current evidence source, then pass a factual "
                  "HANDOFF without deciding." if stage == 0 else
                  "Use the supplied current tool and handoff, then return the exact "
                  "RESULT contract."),
            backstory=("You are a careful synthetic release engineer. Current evidence "
                       "takes priority over historical discussion."),
            llm=llm, tools=source.build_tools(task, stage_tools, trace),
            allow_delegation=False, max_iter=8, verbose=False,
            respect_context_window=False,
        )
        prompt = (
            fixed_history(task, repeat) if stage == 0 else
            [
                {"role": "user", "content": str(task["history_constraint"])},
                {"role": "user", "content": "Investigator handoff: " + handoff_for_decider},
                {"role": "user", "content": decider_role_prompt(task)},
            ]
        )
        try:
            raw_output = str(agent.kickoff(prompt))
            output = raw_output.strip()
            if not output:
                raise base.EmptyModelResponseError("empty role output")
            role_outputs.append(output)
            role_raw_outputs.append(raw_output)
        except Exception as caught:
            error = f"{type(caught).__name__}: {str(caught)[:300]}"
        finally:
            inputs.extend(llm.inputs)
            records.extend(llm.response_records)
            attempts.extend(llm.attempt_records)
            role_traces.append(trace)
        key = "first_role" if stage == 0 else "decider"
        for index, call in enumerate(llm.inputs):
            payloads[key].append(_payload_replay(call))
            if index == 0:
                payloads[key][0]["messages"] = _first_call_text(call)
        for index, record in enumerate(llm.response_records):
            call = llm.inputs[index] if index < len(llm.inputs) else []
            frame_deltas[key].append(
                {
                    "call_index": index,
                    "message_count": len(call),
                    "frame_characters": sum(
                        len(str(message.get("content", "") or "")) for message in call
                    ),
                    "input_tokens": int(record.get("input_tokens", 0)),
                    "output_tokens": int(record.get("output_tokens", 0)),
                }
            )
        if error:
            break
        if stage == 0:
            verdict = judge.judge_handoff(handle_rule, role_raw_outputs[0])
            handoff_first_pass = verdict.as_record()
            handoff_for_decider = str(verdict.semantic["canonical"] or "")
    final = role_outputs[-1] if len(role_outputs) == 2 else ""
    expected_traces = ([str(tool["name"]) for tool in task["tools"]], stage_tools_of(task))
    tool_evidence_ok = (
        len(role_traces) == 2
        and all(role_traces[index] == expected_traces[index] for index in (0, 1))
    )
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
        not error and len(role_outputs) == 2 and tool_evidence_ok
        and bool(handoff_for_decider) and answer_verdict.strict_pass
    )
    semantic_success = bool(
        not error and len(role_outputs) == 2 and tool_evidence_ok
        and bool(handoff_for_decider) and answer_verdict.semantic_pass
    )
    input_tokens = sum(int(record.get("input_tokens", 0)) for record in records)
    output_tokens = sum(int(record.get("output_tokens", 0)) for record in records)
    return {
        "task_id": task["task_id"], "repeat": repeat, "method": ARM,
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
        "first_role_trace": list(role_traces[0]) if role_traces else [],
        "tool_evidence_consistent": tool_evidence_ok,
        "answer_verdict": answer_verdict.as_record(),
        "role_outputs": role_outputs, "role_raw_outputs": role_raw_outputs,
        "role_tool_traces": role_traces,
        "error": error,
        "handoff_first_pass": handoff_first_pass,
        "handoff_for_decider": handoff_for_decider,
        "agent_input_tokens": input_tokens, "agent_output_tokens": output_tokens,
        "summary_input_tokens": 0, "summary_output_tokens": 0,
        "summary_attempts": 0, "summary_failures": 0, "summary_recorded_calls": 0,
        "all_arm_total_tokens": input_tokens + output_tokens,
        "api_request_attempts": request_budget.used - before,
        "agent_attempt_records": attempts,
        "payload": {
            "arm": ARM,
            "compression_enabled": False,
            "guard_installed": False,
            "first_role_payloads": payloads["first_role"],
            "decider_payloads": payloads["decider"],
            "first_role_frame_deltas": frame_deltas["first_role"],
            "decider_frame_deltas": frame_deltas["decider"],
            "first_role_call_count": len(payloads["first_role"]),
            "decider_call_count": len(payloads["decider"]),
            "payload_sha256": hashlib.sha256(
                json.dumps(
                    [payloads["first_role"], payloads["decider"]],
                    ensure_ascii=False, sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        },
    }


def stage_tools_of(task: dict[str, Any]) -> list[str]:
    return [str(task["tools"][-1]["name"])]


def _make_llm(mode: str, task: dict[str, Any], stage: int, client: Any,
              request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: Any, trace: list[str]) -> Any:
    """The provider, without the controller guard: this batch measures the host as-is."""
    if mode == "mock":
        return base.ReplayCrewAILLM(
            _mock_responses(task, stage, trace)
        )
    return base.OpenAICompatCrewAILLM(
        client=client, model=args.model, request_budget=request_budget,
        max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
    )


def _mock_responses(task: dict[str, Any], stage: int,
                    trace: list[str]) -> list[str]:
    """A scripted complete run, so --mode mock verifies the batch shape with zero API."""
    names = ([str(tool["name"]) for tool in task["tools"]]
             if stage == 0 else stage_tools_of(task))
    responses = [
        f"Thought: read the next source.\nAction: {name}\nAction Input: {{}}"
        for name in names
    ]
    if stage == 0:
        responses.append(
            "Thought: all sources read.\nFinal Answer: HANDOFF "
            + " ".join(str(fact) for fact in task["handle_facts"])
        )
    else:
        last = task["tools"][-1]
        evidence = " ".join(
            str(part) for part in (
                last.get("evidence_parts")
                or [f"{key} {value}" for key, value in sorted(last["result"].items())]
            )
        )
        responses.append(
            f"Thought: decide.\nFinal Answer: RESULT task={task['task_id']} "
            f"decision={task['decision']} evidence={evidence}"
        )
    return responses


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--task-ids", default=",".join(TASK_IDS))
    parser.add_argument("--methods", default=ARM)
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
    parser.add_argument("--max-api-requests", type=int, default=DEFAULT_MAX_API_REQUESTS)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-r16-payload-none-01")
    parser.add_argument("--freeze", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.methods != ARM:
        raise SystemExit(
            "the r16 payload acquisition run is uncompressed-arm only; "
            f"refusing methods={args.methods!r}"
        )
    if args.repeats != REPEATS:
        raise SystemExit(
            "the r16 payload acquisition run is fixed at one repeat per task; "
            f"refusing repeats={args.repeats}"
        )
    if args.repeats < 1 or args.max_api_requests < 1:
        raise SystemExit("repeats and max-api-requests must be positive")
    known = load_tasks()
    selected = tuple(part.strip() for part in args.task_ids.split(",") if part.strip())
    tasks = [task for task in known if task["task_id"] in selected]
    if not tasks or set(selected) != {task["task_id"] for task in tasks}:
        raise SystemExit("unknown or missing task")
    plan = plumbing.balanced_plan(tasks, (ARM,), args.repeats)
    budget, calibration = base.resolve_budget(args)
    minimum_requests = 7 * len(plan)
    print(f"r16 payload acquisition plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeat, arm={ARM}, {args.max_api_requests} global slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"model {args.model}; mode {args.mode}; filler history pairs {FILLER_PAIRS}")
    print(f"expected agent requests at 7 calls per sample: {minimum_requests}; "
          f"no summary requests in this arm; no controller guard on this path")
    print(f"budget provider soft/hard/target: {calibration.soft_provider}/"
          f"{calibration.hard_provider}/{calibration.target_provider}")
    print("quality gates: " + ", ".join(QUALITY_GATES))
    print("purpose: obtain real uncompressed host payloads ONLY -- this batch is "
          "never a saving or quality-equivalence result")
    if args.plan:
        print("No API request was sent in --plan mode.", flush=True)
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    key = base.os.getenv("OPENAI_API_KEY") or base.os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not key:
        raise SystemExit("OPENAI_API_KEY or DEEPSEEK_API_KEY is not set")
    freeze_path = Path(args.freeze) if args.freeze else None
    freeze_file_sha256 = ""
    freeze_declared_sha256 = ""
    if freeze_path is not None:
        freeze_file_sha256 = hashlib.sha256(freeze_path.read_bytes()).hexdigest()
        freeze_declared_sha256 = str(
            json.loads(freeze_path.read_text(encoding="utf-8")).get("freeze_sha256") or ""
        )
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
    # The manifest is written ONCE, before the first request, with every provenance field
    # in place: purpose, both non-citable flags, the freeze hashes and the budget.
    manifest = {
        "protocol": PROTOCOL,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "judge_sha256": hashlib.sha256(Path(judge.__file__).read_bytes()).hexdigest(),
        "freeze_path": str(freeze_path).replace("\\", "/") if freeze_path else "",
        "freeze_sha256": freeze_file_sha256,
        "freeze_declared_self_sha256": freeze_declared_sha256,
        "tasks": [task["task_id"] for task in tasks],
        "methods": [ARM], "repeats": args.repeats,
        "filler_history_pairs": FILLER_PAIRS,
        "controller_guard_installed": False,
        "model": args.model, "mode": args.mode, "base_url": args.base_url,
        "budget": calibration.as_manifest(),
        "limits": {
            "fixed_reserved_tokens": args.fixed_reserved_tokens,
            "max_output_tokens": args.max_output_tokens,
        },
        "max_api_requests": args.max_api_requests,
        "quality_gates": QUALITY_GATES,
        "purpose": "payload_acquisition_only",
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "citable_note": ("this batch measures the uncompressed host payload only; it is "
                         "never a saving and never a quality-equivalence result"),
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
                    print(f"skip {task['task_id']} {repeat} (already recorded)", flush=True)
                    continue
                row = run_case(task, repeat, args.mode, client, request_budget,
                               args, budget)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                print(task["task_id"], repeat, row["strict_success"],
                      row["semantic_success"], row["first_pass_complete"],
                      row["tool_sequence_consistent"],
                      row["payload"]["first_role_call_count"],
                      row["payload"]["decider_call_count"],
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


if __name__ == "__main__":
    main()
