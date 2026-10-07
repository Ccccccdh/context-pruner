"""Independent, source-pinned audit of r19 full-capture acquisition (zero API).

It imports neither the r19 runner nor its capture class. Acquisition is non-citable
even when this audit is complete; quality or cost confirmation belongs to later IDs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
ACTION = re.compile(r"(?im)^\s*Action\s*:\s*([^\r\n]+)")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory: Path, freeze_path: Path) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    errors: list[str] = []
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    pins = freeze.get("source_sha256") or {}
    for relative, expected in pins.items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            errors.append(f"source drift or missing: {relative}")
    if manifest.get("source_sha256") != pins or manifest.get("freeze_sha256") != sha(freeze_path):
        errors.append("manifest freeze/source binding mismatch")
    if freeze.get("purpose") != manifest.get("purpose") or manifest.get("purpose") != "payload_acquisition_only":
        errors.append("acquisition purpose mismatch")
    if manifest.get("citable_as_saving") is not False or manifest.get("citable_as_quality_equivalence") is not False:
        errors.append("acquisition incorrectly citable")
    cap = freeze.get("max_api_requests")
    if cap != manifest.get("max_api_requests") or cap != 48:
        errors.append("request hard cap mismatch")
    task_path = ROOT / "tasks/stage5_autogen/natural_tasks_r19_acquisition.json"
    task_data = json.loads(task_path.read_text(encoding="utf-8"))
    tasks = {t["task_id"]: t for t in task_data}
    expected_ids = [t["task_id"] for t in task_data]
    if len(tasks) != 3 or manifest.get("tasks") != expected_ids or manifest.get("methods") != ["none"]:
        errors.append("task grid or arm mismatch")
    if len(rows) != 3 or [r.get("task_id") for r in rows] != expected_ids:
        errors.append("missing, duplicate or reordered sample")
    slots: list[int] = []
    totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    for r in rows:
        task_id = r.get("task_id")
        task = tasks.get(task_id)
        if task is None:
            errors.append(f"unknown task: {task_id}")
            continue
        captures = r.get("full_attempt_capture") or []
        if r.get("capture_complete") is not True or len(captures) != r.get("api_request_attempts") or len(captures) != len(r.get("agent_attempt_records") or []):
            errors.append(f"attempt count/capture mismatch: {task_id}")
        if r.get("method") != "none" or r.get("summary_attempts") != 0:
            errors.append(f"non-baseline or hidden summary: {task_id}")
        executed: list[str] = []
        row_input = row_output = 0
        for attempt in captures:
            slot = attempt.get("request_slot")
            if not isinstance(slot, int):
                errors.append(f"missing slot: {task_id}")
            else:
                slots.append(slot)
            full_input = attempt.get("full_input_messages")
            if not isinstance(full_input, list) or not full_input or any(
                not isinstance(m, dict) or not isinstance(m.get("content"), str) for m in full_input):
                errors.append(f"incomplete full input: {task_id}")
            raw, normalized = attempt.get("raw_model_output"), attempt.get("normalized_model_output")
            if attempt.get("status") == "success" and (not isinstance(raw, str) or not isinstance(normalized, str)):
                errors.append(f"missing full output: {task_id}")
            if isinstance(normalized, str):
                matches = [m.group(1).strip().strip("` ") for m in ACTION.finditer(normalized)]
                candidate = matches[0] if matches else None
                if attempt.get("response_action_candidate") != candidate:
                    errors.append(f"action candidate mismatch: {task_id}")
            parsed = attempt.get("host_parsed_action")
            actual = attempt.get("host_executed_tool")
            if parsed != actual or (actual is not None and actual != attempt.get("response_action_candidate")):
                errors.append(f"parsed/executed tool binding mismatch: {task_id}")
            if actual is not None:
                executed.append(actual)
            if attempt.get("guard_installed") is not False or attempt.get("guard_allowance") != "not_installed_acquisition":
                errors.append(f"acquisition guard scope mismatch: {task_id}")
            if not isinstance(attempt.get("input_tokens"), int) or not isinstance(attempt.get("output_tokens"), int):
                errors.append(f"provider usage missing: {task_id}")
            else:
                row_input += attempt["input_tokens"]
                row_output += attempt["output_tokens"]
        expected_trace = [name for stage in (r.get("role_tool_traces") or []) for name in stage]
        if executed != expected_trace:
            errors.append(f"executed tool trace mismatch: {task_id}")
        if row_input != r.get("agent_input_tokens") or row_output != r.get("agent_output_tokens") or row_input + row_output != r.get("all_arm_total_tokens"):
            errors.append(f"complete token ledger mismatch: {task_id}")
        totals["requests"] += len(captures)
        totals["input_tokens"] += row_input
        totals["output_tokens"] += row_output
    if slots != list(range(1, len(slots) + 1)) or len(slots) > cap:
        errors.append("global request slots are not contiguous or exceed cap")
    return {"status": "independent_r19_acquisition_audit", "complete": not errors,
            "errors": errors, "freeze_sha256": sha(freeze_path),
            "rows": len(rows), "totals": totals,
            "citable_as_saving": False, "citable_as_quality_equivalence": False}
