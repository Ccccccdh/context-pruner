"""Independent prospective audit for the CrewAI r4 handoff contract.

This module deliberately does not import the paid runner or its quality module.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks.json"
FACTS = {
    "incident_triage": ("degraded", "3.8"),
    "release_readiness": ("184", "0"),
    "customer_migration": ("eu-west", "02:00 UTC"),
}
METHODS = ("none", "native_summary", "pruner_v1")
FROZEN_PATHS = (
    "experiments/runners/run_crewai_handoff_v4.py",
    "experiments/runners/crewai_handoff_quality_v4.py",
    "experiments/runners/run_crewai_experiment.py",
    "experiments/audits/audit_crewai_handoff_v4.py",
    "tasks/stage5_autogen/natural_tasks.json",
    "integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R4_DRAFT.md",
)
MARKER = re.compile(r"^\s*HANDOFF(?=\s|[:：|]|$)[\s:：|]*", re.IGNORECASE)
SAFETY_FIELDS = (
    "crewai_group_restore_failure_count",
    "crewai_unmatched_call_count",
    "crewai_active_pending_view_count",
)


def _quality(task: str, raw: str) -> dict:
    text = raw.strip()
    facts = all(part.casefold() in text.casefold() for part in FACTS[task])
    one_line = "\n" not in text and "\r" not in text
    strict = text.startswith("HANDOFF ")
    marker_variant = bool(MARKER.match(text)) and not strict
    canonical = None
    if facts and one_line:
        body = MARKER.sub("", text, count=1).strip()
        if body:
            canonical = "HANDOFF " + body
    return {"facts_present": facts, "strict_marker": strict,
            "marker_variant": marker_variant,
            "one_line": one_line, "first_pass_valid": facts and strict and one_line,
            "canonical": canonical, "needs_recovery": not facts or not one_line}


def _answer_valid(task: dict, text: str) -> bool:
    lower = text.casefold()
    if not text.startswith(f"RESULT task={task['task_id']} ") or "\n" in text:
        return False
    required = [str(part).casefold() for part in task["expected_terms"]]
    if task["task_id"] == "release_readiness":
        required = [part for part in required if part != "0 failed"]
        if not any(part in lower for part in
                   ("0 failed", "failed=0", "failed 0", "184/0", "184 passed/0")):
            return False
    return all(part in lower for part in required)


def audit(directory: Path, freeze_path: Path | None = None,
          dry_run_mock: bool = False) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in
            (directory / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    tasks = {task["task_id"]: task for task in json.loads(TASK_FILE.read_text(encoding="utf-8"))}
    errors: list[str] = []
    if dry_run_mock and freeze_path is None:
        errors.append("mock dry run requires a freeze")
    if freeze_path is not None:
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("batch") != directory.name and not dry_run_mock:
            errors.append("freeze batch mismatch")
        frozen_sources = freeze.get("source_sha256", {})
        if set(frozen_sources) != set(FROZEN_PATHS):
            errors.append("freeze source path set mismatch")
        for source in FROZEN_PATHS:
            expected_hash = frozen_sources.get(source)
            if not isinstance(expected_hash, str) or not (ROOT / source).is_file():
                errors.append(f"freeze source absent: {source}")
            elif hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != expected_hash:
                errors.append(f"freeze source changed: {source}")
        expected_manifest = freeze.get("manifest", {})
        required_settings = {
            "protocol", "tasks", "methods", "repeats", "model", "mode",
            "base_url", "budget", "limits", "max_api_requests",
            "runner_sha256", "task_sha256",
        }
        if set(expected_manifest) != required_settings:
            errors.append("freeze manifest field set mismatch")
        for field in required_settings:
            frozen_value = expected_manifest.get(field)
            actual_value = manifest.get(field)
            if dry_run_mock and field == "mode":
                if frozen_value != "api" or actual_value != "mock":
                    errors.append("mock dry-run mode mismatch")
            elif frozen_value != actual_value:
                errors.append(f"freeze manifest mismatch: {field}")
        expected_count = (len(expected_manifest.get("tasks", [])) *
                          int(expected_manifest.get("repeats", 0)) *
                          len(expected_manifest.get("methods", [])))
        if freeze.get("samples") != expected_count:
            errors.append("freeze sample count mismatch")
    if manifest.get("protocol") != "crewai-two-role-r4-handoff-quality":
        errors.append("protocol mismatch")
    if hashlib.sha256(TASK_FILE.read_bytes()).hexdigest() != manifest.get("task_sha256"):
        errors.append("task hash mismatch")
    expected = {(task, repeat, method) for task in manifest["tasks"]
                for repeat in range(manifest["repeats"])
                for method in manifest["methods"]}
    keys = [(row["task_id"], row["repeat"], row["method"]) for row in rows]
    if len(keys) != len(set(keys)):
        errors.append("duplicate sample key")
    if set(keys) - expected:
        errors.append("unexpected sample key")
    if set(manifest["methods"]) - set(METHODS):
        errors.append("unexpected method")
    counts = {method: {"n": 0, "first_pass_facts": 0, "first_pass_strict": 0,
                       "first_pass_valid": 0, "recovered": 0, "success": 0}
              for method in manifest["methods"]}
    total_attempts = 0
    for row in rows:
        task_id, repeat, method = row["task_id"], row["repeat"], row["method"]
        key = f"{task_id}/{repeat}/{method}"
        if task_id not in tasks:
            errors.append(f"{key}: unknown task")
            continue
        task = tasks[task_id]
        outputs = row["role_outputs"]
        first = row.get("handoff_first_pass")
        recovery = row.get("handoff_recovery")
        if not outputs or not first:
            errors.append(f"{key}: missing first-pass handoff")
            continue
        expected_first = _quality(task_id, outputs[0])
        if first.get("raw") != outputs[0] or any(
            first.get(field) != value for field, value in expected_first.items()
        ):
            errors.append(f"{key}: first-pass quality mismatch")
        recoveries = int(row["recovery_invocations"])
        if recoveries not in (0, 1):
            errors.append(f"{key}: recovery cap exceeded")
        if bool(recoveries) != bool(expected_first["needs_recovery"]):
            errors.append(f"{key}: recovery trigger mismatch")
        if recoveries:
            observation = row.get("recovery_observation", "")
            try:
                observed = json.loads(observation)
            except (TypeError, ValueError):
                observed = None
            if not isinstance(observed, dict):
                errors.append(f"{key}: recovery lacks recorded JSON observation")
            if not recovery:
                errors.append(f"{key}: missing recovery record")
                expected_handoff = ""
            else:
                expected_recovery = _quality(task_id, recovery["raw"])
                if any(recovery.get(field) != value
                       for field, value in expected_recovery.items()):
                    errors.append(f"{key}: recovery quality mismatch")
                expected_handoff = expected_recovery["canonical"] or ""
        else:
            if recovery is not None or row.get("recovery_observation"):
                errors.append(f"{key}: unmetered recovery data")
            expected_handoff = expected_first["canonical"] or ""
        if row.get("handoff_for_decider") != expected_handoff:
            errors.append(f"{key}: decider handoff mismatch")
        if int(row["recovery_api_attempts"]) > int(row["api_request_attempts"]):
            errors.append(f"{key}: recovery request accounting mismatch")
        if manifest["mode"] == "mock" and row["recovery_api_attempts"] != 0:
            errors.append(f"{key}: mock made API request")
        if manifest["mode"] == "api" and recoveries and row["recovery_api_attempts"] < 1:
            errors.append(f"{key}: recovery API attempt absent")
        traces = row["role_tool_traces"]
        tool_pair = (len(traces) == 2 and all(
            Counter(traces[i]) == Counter([task["tools"][i]]) for i in (0, 1)))
        metrics = [*row["role_metrics"], row.get("recovery_metrics", {})]
        role_safe = all(int(metric.get(field, 0)) == 0
                        for metric in metrics for field in SAFETY_FIELDS)
        expected_success = (not row["error"] and len(outputs) == 2 and tool_pair
                            and role_safe and bool(expected_handoff)
                            and _answer_valid(task, outputs[1]))
        if bool(row["success"]) != bool(expected_success):
            errors.append(f"{key}: success mismatch")
        if bool(row["role_safe"]) != role_safe:
            errors.append(f"{key}: role safety mismatch")
        if row["all_arm_total_tokens"] != sum(int(row[field]) for field in (
            "agent_input_tokens", "agent_output_tokens",
            "summary_input_tokens", "summary_output_tokens")):
            errors.append(f"{key}: token sum mismatch")
        if row["summary_attempts"] != row["summary_recorded_calls"] + row["summary_failures"]:
            errors.append(f"{key}: summary accounting mismatch")
        if method != "native_summary" and row["summary_attempts"]:
            errors.append(f"{key}: summary in other method")
        if manifest["mode"] == "api" and row["api_request_attempts"] != (
            len(row["agent_attempt_records"]) + row["summary_attempts"]
        ):
            errors.append(f"{key}: global request accounting mismatch")
        total_attempts += int(row["api_request_attempts"])
        counter = counts[method]
        counter["n"] += 1
        counter["first_pass_facts"] += int(expected_first["facts_present"])
        counter["first_pass_strict"] += int(expected_first["strict_marker"])
        counter["first_pass_valid"] += int(expected_first["first_pass_valid"])
        counter["recovered"] += recoveries
        counter["success"] += int(expected_success)
    if total_attempts > manifest["max_api_requests"]:
        errors.append("global request cap exceeded")
    return {"complete": len(rows) == len(expected) and set(keys) == expected,
            "freeze_checked": freeze_path is not None,
            "dry_run_mock": dry_run_mock,
            "rows": len(rows), "expected_rows": len(expected),
            "request_attempts": total_attempts, "quality": counts, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--dry-run-mock", action="store_true")
    args = parser.parse_args()
    result = audit(args.directory, args.freeze, args.dry_run_mock)
    (args.directory / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["complete"] or result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
