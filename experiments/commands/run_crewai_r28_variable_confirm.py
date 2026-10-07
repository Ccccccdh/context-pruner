"""Prospective variable-source CrewAI confirmation with task-scaled protection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from experiments.commands import run_crewai_r20_orderfree_3arm as reuse
from experiments.runners import crewai_loop_guard_v17 as guard_module
from experiments.runners import crewai_coverage_guard_v24 as coverage
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v17 as v17

ROOT = Path(__file__).resolve().parents[2]
BATCH = "crewai-r28-variable-source-confirm-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R28_VARIABLE_CONFIRM_01.json"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json"
TASK_IDS = ("invoice_settlement_finalize", "dns_cutover_wait")
TASKS_BY_ID = {task["task_id"]: task for task in json.loads(TASK_FILE.read_text(encoding="utf-8"))}
ARMS = ("none", "pruner_v1", "native_summary")
REPEATS = 3
CAP = 270


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_freeze(freeze_path: Path) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert freeze["batch"] == BATCH
    assert freeze["tasks"] == list(TASK_IDS) and freeze["methods"] == list(ARMS)
    assert freeze["repeats"] == REPEATS and freeze["max_api_requests"] == CAP
    assert freeze["guard_scope"] == "all_arms_order_free_coverage"
    assert freeze["citable_as_saving"] is False
    for name, expected in freeze["source_sha256"].items():
        assert digest(ROOT / name) == expected, f"source drift: {name}"
    return freeze


_OLD_FIXED_HISTORY = v17.fixed_history
_OLD_MOCK_RESPONSES = v17._mock_stage_responses
_OLD_CREATE_RECENCY_ADAPTER = v17.mechanism.create_recency_adapter


def task_scaled_recency_adapter(config, *, rule, budget, task_state="",
                                fixed_reserved_tokens=0, agent_roles=None,
                                k_recent_tool_rounds=3):
    # The v13 fixed K=3 was calibrated for three tools. When a task has five
    # required sources, protect all five tool groups from compression so the
    # CrewAI adapter can restore every group exactly once.
    effective_k = (len(TASKS_BY_ID[rule.task_id]["tools"])
                   if k_recent_tool_rounds > 0 else 0)
    return _OLD_CREATE_RECENCY_ADAPTER(
        config, rule=rule, budget=budget, task_state=task_state,
        fixed_reserved_tokens=fixed_reserved_tokens, agent_roles=agent_roles,
        k_recent_tool_rounds=effective_k,
    )


def order_free_history(task: dict, repeat: int) -> list[dict[str, str]]:
    history = _OLD_FIXED_HISTORY(task, repeat)
    last = history[-1]["content"]
    assert "请按固定顺序对每个可用证据源各调用一次" in last
    history[-1]["content"] = last.replace(
        "请按固定顺序对每个可用证据源各调用一次",
        "请按任意顺序对每个可用证据源至少调用一次；允许重复调用",
    )
    return history


def contract_mock_responses(task: dict, stage: int, trace: list[str],
                            *, missing_fact: bool = False) -> list[str]:
    responses = _OLD_MOCK_RESPONSES(task, stage, trace, missing_fact=missing_fact)
    if stage == 1:
        evidence = " ".join(str(fact) for fact in task["answer_facts"])
        responses[-1] = ("Thought: I have all required current evidence.\nFinal Answer: "
                         f"RESULT task={task['task_id']} decision={task['decision']} "
                         f"evidence={evidence}")
    return responses


def budget_class(output: Path):
    class DurableBudget(base.RequestBudget):
        def __init__(self, limit: int) -> None:
            if limit != CAP:
                raise ValueError("request cap differs from freeze")
            super().__init__(limit)

        def consume(self) -> None:
            super().consume()
            with (output / "request-slots.jsonl").open("a", encoding="utf-8", newline="\n") as out:
                out.write(json.dumps({"slot": self.used}) + "\n")
                out.flush()
                os.fsync(out.fileno())

    return DurableBudget


def make_llm_factory(clients: list[Any], summary_records: list[dict]):
    def make_llm(method, mode, task, stage, client, request_budget, args, budget,
                 trace, *, missing_fact=False, guard_state=None):
        enabled = bool(guard_state and guard_state.enabled)
        if mode == "mock":
            responses = contract_mock_responses(task, stage, trace,
                                                    missing_fact=missing_fact)
            llm = (coverage.GuardedReplayCrewAILLM(responses) if enabled
                   else base.ReplayCrewAILLM(responses))
            if enabled:
                coverage.register(llm, guard_state)
            return llm
        names = [str(tool["name"]) for tool in task["tools"]] if stage == 0 else [
            str(task["tools"][-1]["name"])]
        common = dict(client=client, model=args.model, request_budget=request_budget,
                      max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
                      executed_tool_trace=trace, frozen_tool_names=names)
        if method == "native_summary":
            llm = coverage.GuardedCapturedNativeSummaryCrewAILLM(
                **common,
                summary_client_factory=lambda: v17.AsyncOpenAI(
                    api_key=(base.os.getenv("OPENAI_API_KEY")
                             or base.os.getenv("DEEPSEEK_API_KEY") or ""),
                    base_url=args.base_url, timeout=60.0, max_retries=0),
                summary_model=args.model, soft_limit_tokens=budget.soft_limit_tokens,
                hard_limit_tokens=budget.hard_limit_tokens,
                target_tokens=budget.target_tokens or 1,
                fixed_reserved_tokens=args.fixed_reserved_tokens,
                max_summary_tokens=args.max_summary_tokens,
                max_summary_calls=args.max_summary_calls,
                summary_records=summary_records)
        else:
            llm = coverage.GuardedCapturedCrewAILLM(**common)
        if enabled:
            coverage.register(llm, guard_state)
        clients.append(llm)
        return llm

    return make_llm


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--mode", choices=("mock", "api"), default="mock")
    args.add_argument("--out", default="runs/stage5-crewai")
    args.add_argument("--freeze", default=str(FREEZE))
    args.add_argument("--plan", action="store_true")
    args.add_argument("--confirm-send-synthetic-data", action="store_true")
    parsed = args.parse_args(sys.argv[1:] if argv is None else argv)
    freeze_path = Path(parsed.freeze).resolve()
    freeze = check_freeze(freeze_path)
    output = Path(parsed.out) / BATCH
    if parsed.plan:
        print(f"{BATCH}: {len(TASK_IDS) * REPEATS * len(ARMS)} samples, cap {CAP}; no API request")
        return 0
    if output.exists():
        raise SystemExit("write-once batch already exists; partial batches are never resumed")
    if parsed.mode == "api":
        if freeze_path != FREEZE.resolve():
            raise SystemExit("API run requires the final write-once freeze")
        if Path(parsed.out).resolve() != (ROOT / "runs/stage5-crewai").resolve():
            raise SystemExit("API output must use frozen runs root")
        if not parsed.confirm_send_synthetic_data:
            raise SystemExit("explicit synthetic-data send flag required")
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
            raise SystemExit("provider key absent")

    clients: list[Any] = []
    summary_records: list[dict] = []
    guard_module.create_guard_state = lambda names=(), *, enabled=False, max_rejections=6: (
        coverage.CoverageGuardState(tuple(str(n) for n in names), enabled=enabled,
                                    max_rejections=max_rejections))
    reuse.TASK_FILE = TASK_FILE
    v17.TASK_FILE = TASK_FILE
    v17.TASK_IDS = TASK_IDS
    v17.fixed_history = order_free_history
    v17.mechanism.create_recency_adapter = task_scaled_recency_adapter
    v17.MAX_GUARD_REJECTIONS = 6
    v17.PROTOCOL = "crewai-two-role-r28-variable-confirm"
    v17.DEFAULT_PURPOSE = "three_arm_pilot"
    v17.GUARD_ALL_ARMS = True
    v17._make_llm = make_llm_factory(clients, summary_records)
    inner = reuse._wrap_run_case(clients, summary_records)

    def run_case(*pos, **kw):
        before = len(clients)
        row = inner(*pos, **kw)
        for llm in clients[before:]:
            coverage.release(llm)
        row["guard_installed"] = True
        row["guard_scope"] = "all_arms_order_free_coverage"
        task = TASKS_BY_ID[row["task_id"]]
        row["k_recent_tool_rounds"] = len(task["tools"])
        expected = {str(tool["name"]) for tool in task["tools"]}
        first_trace = list(row.get("first_role_trace") or [])
        second_trace = list((row.get("role_tool_traces") or [[], []])[1])
        coverage_ok = expected.issubset(set(first_trace)) and set(first_trace) <= expected
        safe = bool(not row.get("error") and not row.get("guard_exhausted")
                    and row.get("role_safe") and row.get("handoff_for_decider")
                    and len(row.get("role_outputs") or []) == 2)
        handoff_verdict = row.get("handoff_first_pass") or {}
        row["coverage_success"] = coverage_ok
        row["tool_sequence_consistent"] = coverage_ok
        row["decider_tool_sequence_consistent"] = second_trace == [task["tools"][-1]["name"]]
        row["strict_success"] = bool(safe and coverage_ok and row["decider_tool_sequence_consistent"]
                                     and handoff_verdict.get("strict_pass")
                                     and row["answer_verdict"]["strict"]["pass"])
        row["semantic_success"] = bool(safe and coverage_ok and row["decider_tool_sequence_consistent"]
                                       and handoff_verdict.get("semantic_pass")
                                       and row["answer_verdict"]["semantic"]["pass"])
        row["success"] = row["strict_success"]
        row["capture_complete"] = (
            len(row["full_attempt_capture"]) == int(row["agent_request_attempts"])
            and len(row["native_summary_capture"]) == int(row["auxiliary_request_attempts"])
        )
        return row

    v17.run_case = run_case
    v17.FREEZE = str(freeze_path)
    base.RequestBudget = budget_class(output)
    forwarded = ["--mode", parsed.mode, "--out", parsed.out,
                 "--experiment-id", BATCH, "--freeze", str(freeze_path),
                 "--task-ids", ",".join(TASK_IDS), "--methods", ",".join(ARMS),
                 "--repeats", str(REPEATS), "--max-api-requests", str(CAP),
                 "--model", "deepseek-v4-flash", "--max-output-tokens", "512",
                 "--max-summary-tokens", "1024", "--max-summary-calls", "4",
                 "--guard-scope", "all_arms", "--purpose", "three_arm_pilot"]
    if parsed.confirm_send_synthetic_data:
        forwarded.append("--confirm-send-synthetic-data")
    v17.main(forwarded)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "batch": BATCH, "protocol": "crewai-two-role-r28-variable-confirm",
        "guard_scope": "all_arms_order_free_coverage",
        "guard_installed_arms": list(ARMS),
        "quality_gate": freeze["quality_gate"], "cost_gate": freeze["cost_gate"],
        "citable_as_saving": False, "citable_as_quality_equivalence": False,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
