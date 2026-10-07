"""Zero-API host-path smoke for the prospective v18 all-arm guard.

This module deliberately has no API mode. It reuses the r17 case flow only inside
this process, replacing its guard binding and LLM factory temporarily. It writes
one new diagnostic JSON file; no r17 run or freeze is touched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.runners import crewai_loop_guard_v18 as guard
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v17 as pilot

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r17_confirmation.json"
OUT = ROOT / "integrations/crewai/R18_HOST_PATH_ZERO_API_SMOKE_20261006.json"


def make_live_guarded_llm(method, client, request_budget, args, budget, summary_client,
                          guard_state):
    """Prospective v18 factory; construction alone never sends an API request."""
    common = dict(client=client, model=args.model, request_budget=request_budget,
                  max_output_tokens=args.max_output_tokens, thinking_mode="disabled")
    if method == "native_summary":
        llm = guard.GuardedNativeSummaryCrewAILLM(
            **common, summary_client=summary_client, summary_model=args.model,
            soft_limit_tokens=budget.soft_limit_tokens,
            hard_limit_tokens=budget.hard_limit_tokens,
            target_tokens=budget.target_tokens or 1,
            fixed_reserved_tokens=args.fixed_reserved_tokens,
            max_summary_tokens=args.max_summary_tokens,
            max_summary_calls=args.max_summary_calls,
        )
    else:
        llm = guard.GuardedCrewAILLM(**common)
    guard.register_guard_state(llm, guard_state)
    return llm


def main() -> None:
    task = next(t for t in json.loads(TASK_FILE.read_text(encoding="utf-8"))
                if t["task_id"] == "watermark_publish_gate")
    args = argparse.Namespace(provider_soft=1200, provider_hard=3000, provider_target=900,
                              soft_limit=0, hard_limit=0, target=0,
                              fixed_reserved_tokens=300, guard_all_arms=True)
    budget, _ = base.resolve_budget(args)
    old_guard, old_factory = pilot.guard_module, pilot._make_llm
    old_probe = pilot.REMEDY_PROBE

    def make_llm(method, mode, case, stage, client, request_budget, options,
                 case_budget, trace, *, missing_fact=False, guard_state=None):
        if mode != "mock" or guard_state is None:
            raise RuntimeError("v18 smoke is zero API and requires recorded guard state")
        responses = pilot._mock_stage_responses(case, stage, trace, missing_fact=missing_fact)
        if stage == pilot.FIRST_ROLE_STAGE and method == "pruner_v1":
            responses.insert(1, "Thought: skip ahead\nAction: " + case["tools"][2]["name"] + "\nAction Input: {}")
            responses.insert(3, "HANDOFF too early")
        llm = guard.GuardedReplayCrewAILLM(responses)
        guard.register_guard_state(llm, guard_state)
        return llm

    try:
        pilot.guard_module = guard
        pilot._make_llm = make_llm
        pilot.REMEDY_PROBE = ("__disabled_in_v18_smoke__", -1)
        rows = []
        for method in pilot.METHODS:
            row = pilot.run_case(task, method, 0, "mock", None,
                                 base.RequestBudget(100), args, budget)
            rows.append(row)
    finally:
        pilot.guard_module = old_guard
        pilot._make_llm = old_factory
        pilot.REMEDY_PROBE = old_probe
    by_arm = {r["method"]: r for r in rows}
    for method, row in by_arm.items():
        assert row["guard_enabled"] is True, method
        assert row["first_role_trace"] == row["expected_first_role_trace"], method
        assert row["guard_exhausted"] is False and row["error"] == "", method
        # The replay provider records attempts but does not increment the paid-request
        # budget. Keep the two ledgers separate in this mock-only smoke.
        assert len(row["agent_attempt_records"]) == len(row["model_input_traces"]), method
        assert row["all_arm_total_tokens"] == sum(row[k] for k in
            ("agent_input_tokens", "agent_output_tokens", "summary_input_tokens", "summary_output_tokens")), method
    assert by_arm["none"]["guard_rejections"] == 0
    assert by_arm["native_summary"]["guard_rejections"] == 0
    assert by_arm["pruner_v1"]["guard_rejections"] == 2
    assert len(by_arm["pruner_v1"]["agent_attempt_records"]) == len(by_arm["none"]["agent_attempt_records"]) + 2
    result = {
        "status": "mock_host_path_only_no_api_and_not_real_payload_replay",
        "task_id": task["task_id"],
        "all_arm_guard_class": "GuardedReplayCrewAILLM",
        "live_summary_guard_class_available": "GuardedNativeSummaryCrewAILLM",
        "rows": [{"arm": r["method"], "guard_enabled": r["guard_enabled"],
                  "guard_rejections": r["guard_rejections"],
                  "guard_rejection_records": r["guard_rejection_records"],
                  "api_request_attempts": r["api_request_attempts"],
                  "recorded_mock_agent_attempts": len(r["agent_attempt_records"]),
                  "mock_agent_attempt_ledger": [
                      {"status": a["status"], "input_tokens": a["input_tokens"],
                       "output_tokens": a["output_tokens"]}
                      for a in r["agent_attempt_records"]
                  ],
                  "summary_attempts": r["summary_attempts"],
                  "all_arm_total_tokens": r["all_arm_total_tokens"],
                  "tool_sequence_consistent": r["tool_sequence_consistent"],
                  "strict_success": r["strict_success"], "error": r["error"]} for r in rows],
        "citable_as_saving": False,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
