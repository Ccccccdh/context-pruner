from __future__ import annotations

"""CrewAI r17 confirmation payload acquisition: uncompressed arm only.

Built from ``run_crewai_r17_payload_acquisition.py`` (which is pinned by the pilot's
acquisition freeze) with only the task file, task ids, protocol and default experiment id
changed, so no frozen source had to be edited.  It measures the uncompressed host on the
four **confirmation** tasks -- the ones written after the guard was frozen -- so their bound
can be computed before the confirmation pilot.
"""

REUSED_FROM_R17_ACQUISITION = {
    "source_file": "experiments/commands/run_crewai_r17_payload_acquisition.py",
    "source_sha256_at_build": "924190bcce3bcf754dda1edd9b2450fe3cd142038d2a1e380675d54c7ab2efc7",
    "pinned_sha256": "924190bcce3bcf754dda1edd9b2450fe3cd142038d2a1e380675d54c7ab2efc7",
    "reused_changes": [
        "TASK_FILE points at tasks/stage5_autogen/natural_tasks_r17_confirmation.json",
        "PROTOCOL and the default experiment id are the confirmation ones",
        "TASK_IDS are the four confirmation tasks",
        "everything else is copied verbatim"
    ]
}
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from crewai import Agent
from openai import OpenAI

from context_pruner import ContextPluginConfig
from experiments.runners import crewai_handoff_v17_tasks as r17_tasks
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v4 as plumbing

ARM = "none"
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks_r17_confirmation.json")
PROTOCOL = "crewai-r17-confirmation-payload-acquisition-uncompressed"
TASK_IDS = (
    "watermark_publish_gate",
    "autoscale_policy_gate",
    "key_rotation_gate",
    "index_rebuild_gate",
)
REPEATS = 1
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
DEFAULT_MAX_API_REQUESTS = 120
FIRST_ROLE = "Evidence investigator"
SECOND_ROLE = "Operations decision maker"


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return judge.load_tasks(path or TASK_FILE)


def fixed_history(task: dict[str, Any], repeat: int) -> list[dict[str, str]]:
    return r17_tasks.fixed_history(task, repeat)


def decider_role_prompt(task: dict[str, Any]) -> str:
    """The r16 word-for-word decider contract, reused verbatim on the r17 tasks."""
    from experiments.runners import run_crewai_handoff_v16 as v16

    return v16.decider_role_prompt(task)


def build_tools(task: dict[str, Any], names: list[str], trace: list[str]) -> Any:
    from experiments.runners import run_crewai_handoff_v16 as v16

    return v16.build_tools(task, names, trace)


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
    return [
        {
            "role": str(message.get("role", "") or ""),
            "content": str(message.get("content", "") or "")[:20000],
        }
        for message in call
    ]


def _stage_tools(task: dict[str, Any], stage: int) -> list[str]:
    return (
        [str(tool["name"]) for tool in task["tools"]]
        if stage == 0 else [str(task["tools"][-1]["name"])]
    )


def _make_llm(mode: str, task: dict[str, Any], stage: int, client: Any,
              request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: Any, trace: list[str]) -> Any:
    """The provider, without the controller guard: this batch measures the host as-is."""
    if mode == "mock":
        return base.ReplayCrewAILLM(_mock_responses(task, stage))
    return base.OpenAICompatCrewAILLM(
        client=client, model=args.model, request_budget=request_budget,
        max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
    )


def _mock_responses(task: dict[str, Any], stage: int) -> list[str]:
    names = _stage_tools(task, stage)
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


def run_case(task: dict[str, Any], repeat: int, mode: str, client: Any,
             request_budget: base.RequestBudget, args: argparse.Namespace,
             budget: Any) -> dict[str, Any]:
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
    for stage, role in enumerate((FIRST_ROLE, SECOND_ROLE)):
        trace: list[str] = []
        llm = _make_llm(mode, task, stage, client, request_budget, args, budget, trace)
        names = _stage_tools(task, stage)
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
            llm=llm, tools=build_tools(task, names, trace),
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
    parser.add_argument("--experiment-id", default="crewai-r17-confirmation-payload-none-01")
    parser.add_argument("--freeze", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.methods != ARM:
        raise SystemExit(
            "the r17 payload acquisition run is uncompressed-arm only; "
            f"refusing methods={args.methods!r}"
        )
    if args.repeats != REPEATS:
        raise SystemExit(
            "the r17 payload acquisition run is fixed at one repeat per task; "
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
    print(f"r17 payload acquisition plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeat, arm={ARM}, {args.max_api_requests} global slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"model {args.model}; mode {args.mode}; "
          f"filler pairs {r17_tasks.FILLER_PAIRS}")
    print(f"expected agent requests at 7 calls per sample: {7 * len(plan)}; "
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
    # The manifest is written ONCE, before the first request, with every provenance field in
    # place: purpose, both non-citable flags, the guard flag, the freeze hashes, the budget.
    manifest = {
        "protocol": PROTOCOL,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "judge_sha256": hashlib.sha256(Path(judge.__file__).read_bytes()).hexdigest(),
        "guard_sha256": hashlib.sha256(
            Path(r17_tasks.__file__).read_bytes()
        ).hexdigest(),
        "freeze_path": str(freeze_path).replace("\\", "/") if freeze_path else "",
        "freeze_sha256": freeze_file_sha256,
        "freeze_declared_self_sha256": freeze_declared_sha256,
        "tasks": [task["task_id"] for task in tasks],
        "methods": [ARM], "repeats": args.repeats,
        "filler_history_pairs": r17_tasks.FILLER_PAIRS,
        "controller_guard_installed": False,
        "guard_scope": "not_applicable_acquisition",
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
        with (output / "results.jsonl").open(
            "a" if resume else "w", encoding="utf-8"
        ) as handle:
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