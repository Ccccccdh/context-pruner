"""Nine-sample CrewAI coverage-guard development pilot, with a durable cap."""

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
BATCH = "crewai-r25-coverage-guard-dev-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R25_COVERAGE_PILOT_01.json"
TASK_ID = "ledger_lock_attestation"
ARMS = ("none", "pruner_v1", "native_summary")
REPEATS = 3
CAP = 120


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_freeze(freeze_path: Path) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert freeze["batch"] == BATCH
    assert freeze["tasks"] == [TASK_ID] and freeze["methods"] == list(ARMS)
    assert freeze["repeats"] == REPEATS and freeze["max_api_requests"] == CAP
    assert freeze["guard_scope"] == "all_arms_order_free_coverage"
    assert freeze["citable_as_saving"] is False
    for name, expected in freeze["source_sha256"].items():
        assert digest(ROOT / name) == expected, f"source drift: {name}"
    return freeze


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
            responses = v17._mock_stage_responses(task, stage, trace,
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
        print(f"{BATCH}: {REPEATS * len(ARMS)} samples, cap {CAP}; no API request")
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
    v17.TASK_FILE = reuse.TASK_FILE
    v17.TASK_IDS = (TASK_ID,)
    v17.PROTOCOL = "crewai-two-role-r25-coverage-dev"
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
                 "--task-ids", TASK_ID, "--methods", ",".join(ARMS),
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
        "batch": BATCH, "protocol": "crewai-two-role-r25-coverage-dev",
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
