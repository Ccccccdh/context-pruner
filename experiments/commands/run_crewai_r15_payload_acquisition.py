"""CrewAI r15 payload acquisition: one uncompressed arm, to obtain real host payloads.

Why this batch exists
---------------------
The r15 candidate gate stopped at gate 1 because the four new tasks had **no real
CrewAI host payload**: without it, the "quality-safe candidate" and ">=3% upper
bound" gates cannot be computed on anything but mock data, and mock data cannot be
used for an upper bound.

So this batch does exactly one thing: run the four new tasks **with the uncompressed
arm only** (``--methods none``) and record, per model call, the real host payload --
the exact message frames the model received, their hashes, their sizes and the
provider token counts.  Nothing else:

  * **no plugin arm, no native-summary arm** -- there is no compression here, so this
    batch can never be cited as a saving, and it is not a quality-equivalence test;
  * no scoring change: the frozen r8 judge rules are used unchanged, only to label
    each sample with the strict / semantic / first-pass verdicts so the payload is
    self-describing;
  * no r9-r14 file, freeze or stored answer is touched.

What is recorded per sample (this is the payload the gate replays)
----------------------------------------------------------------
  * ``first_role_payloads`` / ``decider_payloads``: for every model call, the ordered
    message roles plus, for each message, its sha256 and character count, and the
    full text of the **first** call of each role (the frame that must be compressed);
  * ``frame_deltas``: the per-call new-message bytes and their provider input tokens,
    which is what makes the gate's upper-bound arithmetic possible;
  * the tool trace, the handoff line, the final answer and the judge verdicts.

Stop line: the global request cap, an empty model response, or a connection-class
error.  Failures and cap-limited samples are recorded and counted, never dropped.
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
from experiments.runners import run_crewai_handoff_v8 as v8

METHODS = v8.METHODS
ARM = "none"
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks_r15.json")
PROTOCOL = "crewai-r15-payload-acquisition-uncompressed"
FIRST_ROLE = v8.FIRST_ROLE
SECOND_ROLE = v8.SECOND_ROLE
TASK_IDS = (
    "service_release_gate",
    "schema_migration_gate",
    "flag_promotion_gate",
    "cache_promotion_gate",
)
REPEATS = 3
FILLER_PAIRS = 14
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
#: r13's pre-registered contract sentence, kept verbatim so the acquisition payload
#: matches the wording the plugin candidate would use.
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
_all_observations = v8._all_observations
_make_llm = v8._make_llm
expected_traces_for = lambda task: (
    [str(tool["name"]) for tool in task["tools"]],
    [str(task["tools"][-1]["name"])],
)


def fixed_history(task: dict[str, Any], repeat: int) -> list[dict[str, str]]:
    """The r8 investigator prompt shape, with a natural history and no "Nth handoff"."""
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    constraint = str(task["history_constraint"])
    messages: list[dict[str, str]] = [
        {"role": "user", "content": constraint},
        {"role": "assistant", "content": f"已记录当前约束，并会在后续判断中保留 {anchor}。"},
    ]
    for index in range(FILLER_PAIRS + repeat):
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


def _message_fingerprint(message: Any, *, index: int) -> dict[str, Any]:
    content = str(message.get("content", "") if isinstance(message, dict)
                  else getattr(message, "content", ""))
    role = str(message.get("role", "") if isinstance(message, dict)
               else getattr(message, "role", ""))
    return {
        "index": index,
        "role": role,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "characters": len(content),
    }


def _payload_replay(call: list[dict[str, Any]]) -> dict[str, Any]:
    """One recorded model call: message fingerprints plus the full first-call text."""
    return {
        "message_count": len(call),
        "message_fingerprints": [
            _message_fingerprint(message, index=index)
            for index, message in enumerate(call)
        ],
        "frame_character_total": sum(
            len(str(message.get("content", ""))) for message in call
        ),
    }


def _first_call_text(call: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The verbatim messages of one call (bounded: only the first call of a role)."""
    return [
        {
            "role": str(message.get("role", "")),
            "content": str(message.get("content", ""))[:20000],
        }
        for message in call
    ]


def run_case(task: dict[str, Any], repeat: int, mode: str, client: Any,
             request_budget: base.RequestBudget, args: argparse.Namespace,
             budget: Any, *, force_missing_handoff: bool = False) -> dict[str, Any]:
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
        llm = _make_llm(ARM, mode, task, stage, client, request_budget, args,
                        budget, trace, missing_fact=force_missing_handoff)
        stage_tools = (
            [str(tool["name"]) for tool in task["tools"]]
            if stage == 0 else [str(task["tools"][-1]["name"])]
        )
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
            llm=llm, tools=build_tools(task, stage_tools, trace),
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
        # Per-call new-message bytes and provider input tokens: the frame the plugin
        # would have to compress is the first call of the role.
        for index, record in enumerate(llm.response_records):
            call = llm.inputs[index] if index < len(llm.inputs) else []
            frame_deltas[key].append(
                {
                    "call_index": index,
                    "message_count": len(call),
                    "frame_characters": sum(
                        len(str(message.get("content", ""))) for message in call
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
    expected_traces = expected_traces_for(task)
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
    first_role_trace = list(role_traces[0]) if role_traces else []
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
        "first_role_trace": first_role_trace,
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
        # ---- the payload the gate replays -------------------------------
        "payload": {
            "arm": ARM,
            "compression_enabled": False,
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
    parser.add_argument("--max-api-requests", type=int, default=120)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", default="runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-r15-payload-none-01")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.methods != ARM:
        raise SystemExit(
            "the r15 payload acquisition run is uncompressed-arm only; "
            f"refusing methods={args.methods!r}"
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
    minimum_requests = 2 * len(plan)
    print(f"r15 payload acquisition plan: {len(plan)} samples, {len(tasks)} tasks, "
          f"{args.repeats} repeats, arm={ARM}, {args.max_api_requests} global slots")
    print(f"tasks: {[task['task_id'] for task in tasks]}")
    print(f"model {args.model}; mode {args.mode}; filler history pairs {FILLER_PAIRS}")
    print(f"minimum agent requests: {minimum_requests}; no summary requests in this arm")
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
        "tasks": [task["task_id"] for task in tasks],
        "methods": [ARM], "repeats": args.repeats,
        "filler_history_pairs": FILLER_PAIRS,
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
