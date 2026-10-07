"""Independent, amendment-aware audit of r19 full-capture acquisition (zero API).

Generated from ``audit_crewai_r19_acquisition.py`` (which the write-once freeze pins)
with three targeted changes: amendment-first pin resolution (fail-closed), the
amendment's effective request cap, and a cache-metering aggregate.

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
    # Amendment-first, fail-closed: the freeze file is never rewritten, so a source that
    # changed after it was written is accepted ONLY when its current digest is exactly the
    # one the amendment registers for that path.  Anything else stays an error.
    amendment_path = freeze_path.with_name(
        freeze_path.stem + "_FREEZE_AMENDMENT_20261006.json"
    )
    authority = dict(pins)
    effective_cap = freeze.get("max_api_requests")
    if amendment_path.is_file():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        authority.update(amendment.get("pinned_sources_after_amendment") or {})
        effective_cap = int(
            amendment.get("effective_max_api_requests", effective_cap)
        )
    for relative, expected in authority.items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            errors.append(f"source drift or missing (unregistered digest): {relative}")
    if manifest.get("source_sha256") != pins or manifest.get("freeze_sha256") != sha(freeze_path):
        errors.append("manifest freeze/source binding mismatch")
    # The manifest records the pin map it RAN WITH; that snapshot may legitimately be an
    # earlier revision of a moving authority, so it is verified by hashing each recorded
    # entry (see the manifest-pin check below the cap) instead of by JSON snapshot equality.
    if freeze.get("purpose") != manifest.get("purpose") or manifest.get("purpose") != "payload_acquisition_only":
        errors.append("acquisition purpose mismatch")
    if manifest.get("citable_as_saving") is not False or manifest.get("citable_as_quality_equivalence") is not False:
        errors.append("acquisition incorrectly citable")
    ceiling = freeze.get("max_api_requests")
    cap = effective_cap
    if cap != manifest.get("max_api_requests") or cap > int(ceiling or cap):
        errors.append("request hard cap mismatch")
    if int(ceiling or 0) > 48:
        errors.append("freeze ceiling exceeds the pre-registered 48 slots")
    # The manifest records the pin map it ran with; that snapshot may legitimately be an
    # earlier revision of a moving authority, so it is verified by hashing rather than by
    # comparing JSON snapshots.
    recorded_map = manifest.get("pinned_sources_after_amendment") or {}
    if not recorded_map:
        errors.append("manifest amendment pin map missing")
    for relative, digest in recorded_map.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"manifest pin map names a missing file: {relative}")
        elif sha(path) != digest:
            errors.append(f"manifest pin map is stale against the file: {relative}")
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
    cache = {
        "attempts_with_split": sum(
            1 for r in rows for a in (r.get("full_attempt_capture") or [])
            if a.get("cache_tokens_source") not in (None, "", "absent")
        ),
        "attempts_without_split": sum(
            1 for r in rows for a in (r.get("full_attempt_capture") or [])
            if a.get("cache_tokens_source") in (None, "", "absent")
        ),
        "prompt_cache_hit_tokens": sum(
            int(a.get("prompt_cache_hit_tokens", 0) or 0)
            for r in rows for a in (r.get("full_attempt_capture") or [])
        ),
        "prompt_cache_miss_tokens": sum(
            int(a.get("prompt_cache_miss_tokens", 0) or 0)
            for r in rows for a in (r.get("full_attempt_capture") or [])
        ),
        "attempts_timestamped": sum(
            1 for r in rows for a in (r.get("full_attempt_capture") or [])
            if a.get("utc_started")
        ),
        "attempts_off_peak": sum(
            1 for r in rows for a in (r.get("full_attempt_capture") or [])
            if a.get("off_peak_window") is True
        ),
        "note": "a missing cache split is reported as absent, never booked as a full-price "
                "miss; the price tier is derived from the recorded UTC timestamps",
    }
    return {"status": "independent_r19_acquisition_audit_v2", "complete": not errors,
            "errors": errors, "freeze_sha256": sha(freeze_path),
            "effective_max_api_requests": effective_cap,
            "freeze_ceiling_max_api_requests": freeze.get("max_api_requests"),
            "rows": len(rows), "totals": totals, "cache_metering": cache,
            "citable_as_saving": False, "citable_as_quality_equivalence": False}
