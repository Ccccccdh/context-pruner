"""Fail-closed zero-API readiness check for a prospective new-ID acquisition.

This checks available artifacts only. It never imports a provider or reads a key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "integrations/crewai/R18_DEVELOPMENT_TASK_REGISTRY_20261006.json"
MOCK_RUNNER = ROOT / "experiments/runners/run_crewai_handoff_v18_zero_api.py"
AUDITOR = ROOT / "experiments/audits/audit_crewai_handoff_v18_sanity.py"
OLD_RESULTS = ROOT / "runs/stage5-crewai/crewai-two-role-r17-confirmation-3arm-01/results.jsonl"
OUT = Path(__file__).with_name("R18_ACQUISITION_READINESS_GATE_20261006.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    tasks = registry.get("tasks") or []
    required_task_fields = {"task_id", "title", "task", "history_constraint", "history_topic",
                            "tools", "handle_facts", "decision"}
    task_schema_ready = len(tasks) >= 3 and all(required_task_fields <= set(t) and
        all(isinstance(tool, dict) and {"name", "call", "result", "evidence_parts"} <= set(tool)
            for tool in t["tools"]) for t in tasks)
    old_row = json.loads(next(line for line in OLD_RESULTS.read_text(encoding="utf-8").splitlines() if line))
    old_attempt = old_row["agent_attempt_records"][0]
    old_capture_ready = all(k in old_attempt for k in (
        "full_input_messages", "raw_model_output", "normalised_model_output",
        "host_parsed_action", "guard_verdict", "executed_tool", "input_tokens", "output_tokens"))
    runner_source = MOCK_RUNNER.read_text(encoding="utf-8")
    prospective_api_runner_exists = "mode == \"api\"" in runner_source or "mode == 'api'" in runner_source
    freeze_candidates = list((ROOT / "integrations/crewai").glob("PRE_RUN_FREEZE_R18*ACQUISITION*.json"))
    freeze_ready = len(freeze_candidates) == 1
    audit_source = AUDITOR.read_text(encoding="utf-8")
    independent_auditor_ready = freeze_ready and "def audit_paid_acquisition" in audit_source
    # Three tasks x two roles x CrewAI max_iter 8; no summary in acquisition.
    prospective_request_hard_cap = len(tasks) * 2 * 8
    checks = {
        "new_full_task_registry": task_schema_ready,
        "complete_old_capture_reusable": old_capture_ready,
        "new_api_runner_with_complete_capture": prospective_api_runner_exists,
        "write_once_source_freeze": freeze_ready,
        "independent_frozen_acquisition_auditor": independent_auditor_ready,
        "hard_request_cap_arithmetic_defined": len(tasks) >= 3 and prospective_request_hard_cap > 0,
    }
    out = {
        "status": "zero_api_fail_closed_readiness",
        "paid_acquisition_allowed": all(checks.values()),
        "checks": checks,
        "task_count": len(tasks),
        "prospective_max_requests_if_three_full_tasks": prospective_request_hard_cap,
        "request_arithmetic_note": "3 tasks x 2 roles x max_iter 8 = 48 is a planning ceiling only; a real runner must enforce it across failed and retry attempts",
        "source_sha256": {"registry": sha(REGISTRY), "mock_runner": sha(MOCK_RUNNER), "sanity_auditor": sha(AUDITOR), "old_results": sha(OLD_RESULTS)},
        "exact_blockers": [
            "development registry has tool names and literals but lacks the full executable task, tool result, history and handoff contract schema",
            "r17 attempt records have token/status but no full input messages, full model outputs, host-parsed Action, guard allowance or executed-tool binding",
            "v18 runner is mock-only; no new-ID API acquisition path captures every attempt before/after the host parser",
            "no write-once r18 acquisition freeze pins runner, task registry, capture schema and independent auditor",
            "v18 sanity auditor checks synthetic rows and mock attempts only; it is not a source-frozen acquisition auditor",
        ],
        "old_batches_unchanged": True,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
