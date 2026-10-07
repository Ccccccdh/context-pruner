"""Independent zero-API schema/audit smoke test for a prospective CrewAI registry.

This is not a frozen paid-batch auditor. It imports neither the r17 auditor nor the
v18 guard and cannot retrospectively validate the r17 confirmation batch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ARMS = ("none", "pruner_v1", "native_summary")


def audit(registry_path: Path, rows: list[dict], *, repeats: int) -> dict:
    registry_path = Path(registry_path)
    raw = registry_path.read_bytes()
    registry = json.loads(raw)
    tasks = {t["task_id"]: t for t in registry["tasks"]}
    errors: list[str] = []
    if len(tasks) != len(registry["tasks"]) or len(tasks) < 3:
        errors.append("registry requires at least three unique tasks")
    if registry.get("status") != "synthetic_zero_api_controls_only_not_paid_or_confirmation_tasks":
        errors.append("registry scope is not explicit")
    expected_grid = {(task, repeat, arm) for task in tasks for repeat in range(repeats) for arm in ARMS}
    actual_grid = [(r.get("task_id"), r.get("repeat"), r.get("method")) for r in rows]
    if set(actual_grid) != expected_grid or len(actual_grid) != len(expected_grid):
        errors.append("sample grid is incomplete, duplicated or contains unknown rows")
    total_requests = 0
    total_tokens = 0
    arm_totals = {arm: {"requests": 0, "tokens": 0, "rows": 0, "strict": 0} for arm in ARMS}
    for key, r in zip(actual_grid, rows):
        if key not in expected_grid:
            continue
        task = tasks[key[0]]
        name = "/".join(map(str, key))
        expected_tools = task["tools"]
        if r.get("expected_first_role_trace") != expected_tools or r.get("first_role_trace") != expected_tools:
            errors.append(f"tool trace mismatch: {name}")
        if r.get("guard_enabled") is not True:
            errors.append(f"all-arm guard missing: {name}")
        if r.get("guard_exhausted") or r.get("error"):
            errors.append(f"failed or exhausted row: {name}")
        if key[2] != "pruner_v1" and r.get("guard_rejections") != 0:
            errors.append(f"non-plugin guard rejection: {name}")
        if r.get("guard_rejections") != len(r.get("guard_rejection_records", [])):
            errors.append(f"guard ledger mismatch: {name}")
        if any(lit not in r.get("answer", "") for lit in task["required_literals"]):
            errors.append(f"required literal absent: {name}")
        if task["decision"] not in r.get("answer", ""):
            errors.append(f"decision absent: {name}")
        agent_calls = r.get("agent_attempt_records", [])
        summary_calls = r.get("summary_attempt_records", [])
        requests = r.get("api_request_attempts")
        if requests != len(agent_calls) + len(summary_calls):
            errors.append(f"request ledger mismatch: {name}")
        tokens = sum(x.get("input_tokens", 0) + x.get("output_tokens", 0) for x in [*agent_calls, *summary_calls])
        if r.get("all_arm_total_tokens") != tokens:
            errors.append(f"complete token ledger mismatch: {name}")
        if isinstance(requests, int):
            total_requests += requests
            arm_totals[key[2]]["requests"] += requests
        if isinstance(tokens, int):
            total_tokens += tokens
            arm_totals[key[2]]["tokens"] += tokens
        arm_totals[key[2]]["rows"] += 1
        arm_totals[key[2]]["strict"] += int(r.get("strict_success") is True)
    return {"status": "synthetic_zero_api_auditor_sanity_only", "complete": not errors,
            "errors": errors, "registry_sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows), "requests": total_requests, "complete_tokens": total_tokens,
            "arm_totals": arm_totals, "citable_as_paid_saving": False}


def audit_mock_host_smoke(path: Path) -> dict:
    """Check recorded mock attempts separately from charged API requests."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("status") != "mock_host_path_only_no_api_and_not_real_payload_replay":
        errors.append("not a v18 mock host-path smoke")
    rows = data.get("rows") or []
    if len(rows) != 3 or {r.get("arm") for r in rows} != set(ARMS):
        errors.append("three-arm smoke grid invalid")
    by_arm = {r.get("arm"): r for r in rows}
    for arm, row in by_arm.items():
        if row.get("guard_enabled") is not True:
            errors.append(f"guard absent: {arm}")
        if row.get("guard_rejections") != len(row.get("guard_rejection_records") or []):
            errors.append(f"guard attempts mismatch: {arm}")
        if row.get("api_request_attempts") != 0:
            errors.append(f"mock charged an API request: {arm}")
        ledger = row.get("mock_agent_attempt_ledger") or []
        if row.get("recorded_mock_agent_attempts") != len(ledger):
            errors.append(f"mock attempt count mismatch: {arm}")
        if row.get("all_arm_total_tokens") != sum(
            int(a.get("input_tokens") or 0) + int(a.get("output_tokens") or 0)
            for a in ledger
        ):
            errors.append(f"mock complete token mismatch: {arm}")
        if not row.get("tool_sequence_consistent") or not row.get("strict_success") or row.get("error"):
            errors.append(f"host path failed: {arm}")
    if set(by_arm) == set(ARMS):
        if [by_arm[a].get("guard_rejections") for a in ARMS] != [0, 2, 0]:
            errors.append("control rejection pattern mismatch")
        if [by_arm[a].get("recorded_mock_agent_attempts") for a in ARMS] != [7, 9, 7]:
            errors.append("mock attempt ledger mismatch")
    return {"status": "independent_mock_host_smoke_audit_only", "complete": not errors,
            "errors": errors, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "mock_attempts": sum(int(r.get("recorded_mock_agent_attempts") or 0) for r in rows),
            "charged_api_requests": sum(int(r.get("api_request_attempts") or 0) for r in rows),
            "citable_as_paid_saving": False}
