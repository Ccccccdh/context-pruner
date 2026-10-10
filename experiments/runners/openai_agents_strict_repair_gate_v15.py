"""Zero-API feasibility gate for a prospective shared bounded final-answer repair.

This is diagnostic code. It never calls a model, changes a recorded answer, or
changes the v12-v14 acceptance decisions. A prospective paid runner must pass
the additional integration and independent-audit gates recorded below.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "integrations/openai_agents/STRICT_FAILURE_INVENTORY_20261005.json"
V14 = REPO / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v14-multitask-confirm-01/samples.jsonl"
V14_FREEZE = REPO / "integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json"
OUT = REPO / "integrations/openai_agents/V15_STRICT_REPAIR_ZERO_API_GATE_20261006.json"


def strict_check(answer: str, pattern: str, terms: list[str]) -> dict[str, bool]:
    """Mirror the frozen answer and final format conditions, without rescoring."""
    return {
        "prefix": answer.startswith("RESULT "),
        "one_line": "\n" not in answer,
        "max_160_chars": len(answer) <= 160,
        "cause_fix_fields": bool(re.fullmatch(r"RESULT issue=\S+ cause=\S.* fix=\S.*", answer)),
        "required_terms": all(term.lower() in answer.lower() for term in terms),
        "answer_pattern": bool(re.fullmatch(pattern, answer, flags=re.IGNORECASE)),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    v14_freeze = json.loads(V14_FREEZE.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in V14.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 27
    assert sum(int(row["api_request_attempts"]) for row in rows) == 207
    failures = []
    for entry in inventory["entries"]:
        pattern = (v14_freeze["held_out_basis"]["per_task"][entry["task"]]["answer_pattern"]
                   if entry["batch"] == "v14" else entry["frozen_contract"])
        checks = strict_check(entry["answer"], pattern, entry["required_terms"])
        assert len(entry["answer"]) == entry["answer_chars"]
        assert all(ok for key, ok in checks.items() if key != "max_160_chars")
        assert not checks["max_160_chars"]
        failures.append({
            "batch": entry["batch"],
            "task": entry["task"],
            "repeat": entry["repeat"],
            "method": entry["method"],
            "answer_chars": entry["answer_chars"],
            "checks": checks,
        })
    assert len(failures) == 3

    # These are fail-closed validator controls, not proposed answer rewrites.
    e = inventory["entries"][0]
    controls = {
        "reject_newline": strict_check(e["answer"][:70] + "\n" + e["answer"][70:], e["frozen_contract"], e["required_terms"])["one_line"] is False,
        "reject_missing_cause": strict_check("RESULT issue=django-16263 fix=referenced", e["frozen_contract"], e["required_terms"])["cause_fix_fields"] is False,
        "reject_missing_literal": strict_check(e["answer"].replace("subquery", "join"), e["frozen_contract"], e["required_terms"])["required_terms"] is False,
        "reject_bad_regex": strict_check("RESULT issue=django-16263 cause=existing_annotations fix=referenced", e["frozen_contract"], e["required_terms"])["answer_pattern"] is False,
    }
    assert all(controls.values())

    # 3 tasks x 3 repeats x 3 arms; seven diagnosis calls per sample; native
    # summary is charged up to three times for its nine samples. One retry is
    # charged to *every* sample in the worst case, even if the first answer fits.
    samples = 3 * 3 * 3
    model_calls = samples * 7
    repair_calls = samples
    native_summaries = 3 * 3 * 3
    worst_logical_requests = model_calls + repair_calls + native_summaries
    assert worst_logical_requests == 243
    # The existing provider wrapper permits two transport retries (three
    # attempts per diagnosis/repair model call); native summary uses one.
    worst_transport_attempts = (model_calls + repair_calls) * 3 + native_summaries
    assert worst_transport_attempts == 675
    output = {
        "schema": "openai_agents_v15_strict_repair_zero_api_gate",
        "paid_requests": 0,
        "source_sha256": {str(p.relative_to(REPO)).replace("\\", "/"): _sha(p) for p in (INVENTORY, V14, V14_FREEZE)},
        "old_strict_results_unchanged": True,
        "observed_failures": failures,
        "validator_negative_controls": controls,
        "prospective_shared_host_config": {
            "max_repair_calls_per_sample": 1,
            "fail_closed_if_second_answer_invalid": True,
            "all_arms_receive_same_validator": True,
            "all_transport_attempts_and_provider_tokens_must_be_counted": True,
            "logical_request_upper_bound_for_3_tasks_3_repeats_3_arms": worst_logical_requests,
            "transport_attempt_upper_bound_with_two_model_retries": worst_transport_attempts,
            "one_task_one_repeat_three_arm_logical_upper_bound": 27,
            "one_task_one_repeat_three_arm_transport_attempt_upper_bound": 75,
            "transport_retry_buffer_not_yet_frozen": True,
        },
        "gate": {
            "old_payload_length_failure_reproduced": True,
            "validator_negative_controls_pass": True,
            "safe_fact_preserving_rewrite_demonstrated_on_real_payload": False,
            "shared_runner_counts_repair_provider_usage": False,
            "shared_wrapper_synthetic_accounting_controls_pass": True,
            "exact_new_baseline_source_blobs_available": False,
            "new_tasks_pre_registered_and_acquired": False,
            "new_task_replay_four_invariants_pass": False,
            "independent_auditor_positive_negative_source_drift_controls_pass": False,
            "passed": False,
            "decision": "STOP_BEFORE_PAID_REQUESTS",
        },
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paid_requests": 0, "observed_failures": len(failures), "controls_pass": all(controls.values()), "worst_logical_requests": worst_logical_requests, "gate_passed": False}))


if __name__ == "__main__":
    main()
