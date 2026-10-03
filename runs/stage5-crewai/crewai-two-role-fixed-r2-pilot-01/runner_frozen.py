"""Two-role CrewAI handoff pilot with fixed-input paired repeats.

This is a separate experiment from the older single-agent synthetic batch.
The same task payload is used for every repeat; only the arm changes.
"""

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


METHODS = ("none", "native_summary", "pruner_v1")
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks.json")
FIRST_ROLE = "Evidence investigator"
SECOND_ROLE = "Operations decision maker"
HANDOFF_FACT = {
    "incident_triage": ("degraded", "3.8"),
    "release_readiness": ("184", "0"),
    "customer_migration": ("eu-west", "02:00 UTC"),
}


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


def _mock_stage_responses(task: dict[str, Any], stage: int) -> list[str]:
    scripted = base._mock_responses(task)
    if stage == 0:
        return [scripted[0], "Thought: the first tool returned current evidence.\nFinal Answer: HANDOFF " +
                str(task["expected_terms"][0]) + " " + " ".join(HANDOFF_FACT[str(task["task_id"])])]
    return [scripted[1], scripted[-1]]


def _make_llm(method: str, mode: str, task: dict[str, Any], stage: int,
              client: Any, request_budget: base.RequestBudget, args: argparse.Namespace,
              budget: ContextBudget) -> base.RecordingCrewAILLM:
    if mode == "mock":
        return base.ReplayCrewAILLM(_mock_stage_responses(task, stage))
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
             args: argparse.Namespace, budget: ContextBudget) -> dict[str, Any]:
    inputs: list[list[dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    role_outputs: list[str] = []
    role_metrics: list[dict[str, Any]] = []
    role_traces: list[list[str]] = []
    summary_attempts = summary_inputs = summary_outputs = summary_failures = 0
    error = ""
    before = request_budget.used
    for stage, role in enumerate((FIRST_ROLE, SECOND_ROLE)):
        trace: list[str] = []
        llm = _make_llm(method, mode, task, stage, client, request_budget, args, budget)
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
                {"role": "user", "content": "Investigator handoff: " + role_outputs[0]},
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
    final = role_outputs[-1] if len(role_outputs) == 2 else ""
    correct = (not error and len(role_outputs) == 2
               and role_outputs[0].startswith("HANDOFF ")
               and all(term.lower() in role_outputs[0].lower()
                       for term in HANDOFF_FACT[str(task["task_id"])])
               and all(Counter(trace) == Counter([str(task["tools"][i])])
                       for i, trace in enumerate(role_traces))
               and base._answer_correct(str(task["task_id"]), final.lower(),
                                        task["expected_terms"])
               and final.startswith(f"RESULT task={task['task_id']} ")
               and "\n" not in final)
    role_safe = all(int(metrics.get(key, 0)) == 0
                    for metrics in role_metrics
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
        "agent_input_tokens": input_tokens, "agent_output_tokens": output_tokens,
        "summary_input_tokens": summary_inputs, "summary_output_tokens": summary_outputs,
        "summary_attempts": summary_attempts, "summary_failures": summary_failures,
        "summary_recorded_calls": summary_attempts - summary_failures,
        "all_arm_total_tokens": input_tokens + output_tokens + summary_inputs + summary_outputs,
        "api_request_attempts": request_budget.used - before,
        "agent_attempt_records": attempts,
        "model_input_traces": [base._input_trace(items) for items in inputs],
        "compression_events": sum(int(m.get("compression_count", 0)) for m in role_metrics),
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
    parser.add_argument("--experiment-id", default="crewai-two-role-fixed-r1-pilot")
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
    plan = [(task, repeat, method) for task in tasks for repeat in range(args.repeats)
            for method in methods]
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
        "protocol": "crewai-two-role-fixed-r1",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
        "tasks": [task["task_id"] for task in tasks], "methods": methods,
        "repeats": args.repeats, "model": args.model, "mode": args.mode,
        "budget": calibration.as_manifest(),
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
