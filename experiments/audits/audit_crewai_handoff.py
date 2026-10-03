"""Independent row and accounting audit for the two-role CrewAI pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "tasks/stage5_autogen/natural_tasks.json"
HANDOFF_FACT = {
    "incident_triage": ("degraded", "3.8"),
    "release_readiness": ("184", "0"),
    "customer_migration": ("eu-west", "02:00 UTC"),
}


def audit(directory: Path, freeze_path: Path | None = None) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    tasks = {item["task_id"]: item for item in json.loads(TASKS.read_text(encoding="utf-8"))}
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    errors: list[str] = []
    if freeze_path is not None:
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("batch") != directory.name:
            errors.append("freeze batch ID mismatch")
        planned = len(manifest["tasks"]) * manifest["repeats"] * len(manifest["methods"])
        if freeze.get("samples") != planned:
            errors.append("freeze sample count mismatch")
        if freeze.get("max_api_requests") != manifest.get("max_api_requests"):
            errors.append("freeze request cap mismatch")
        for path, digest in freeze.get("source_sha256", {}).items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
                errors.append(f"source changed: {path}")
    if hashlib.sha256(TASKS.read_bytes()).hexdigest() != manifest["task_sha256"]:
        errors.append("task hash changed")
    expected_keys = {(task, repeat, arm) for task in manifest["tasks"]
                     for repeat in range(manifest["repeats"])
                     for arm in manifest["methods"]}
    keys = [(row["task_id"], row["repeat"], row["method"]) for row in rows]
    if len(set(keys)) != len(keys):
        errors.append("duplicate sample key")
    if not set(keys).issubset(expected_keys):
        errors.append("unexpected sample key")
    if len(rows) == len(expected_keys) and set(keys) != expected_keys:
        errors.append("sample grid mismatch")
    attempts = 0
    quality: dict[str, list[bool]] = {arm: [] for arm in manifest["methods"]}
    for row in rows:
        key = f"{row['task_id']}/{row['repeat']}/{row['method']}"
        task = tasks[row["task_id"]]
        outputs = row["role_outputs"]
        traces = row["role_tool_traces"]
        recalculated = (not row["error"] and len(outputs) == 2 and len(traces) == 2
                        and outputs[0].startswith("HANDOFF ")
                        and all(term.lower() in outputs[0].lower()
                                for term in HANDOFF_FACT[row["task_id"]])
                        and all(Counter(traces[i]) == Counter([task["tools"][i]]) for i in (0, 1))
                        and outputs[1].startswith(f"RESULT task={row['task_id']} ")
                        and "\n" not in outputs[1]
                        and all(term.lower() in outputs[1].lower()
                                for term in task["expected_terms"] if term != "0 failed")
                        and all(int(metric.get(field, 0)) == 0
                                for metric in row["role_metrics"]
                                for field in ("crewai_group_restore_failure_count",
                                              "crewai_unmatched_call_count",
                                              "crewai_active_pending_view_count")))
        if row["task_id"] == "release_readiness" and len(outputs) == 2:
            recalculated = recalculated and any(
                value in outputs[1].lower() for value in
                ("0 failed", "failed=0", "failed 0", "184/0", "184 passed/0")
            )
        if bool(row["success"]) != bool(recalculated):
            errors.append(f"{key}: success mismatch")
        quality[row["method"]].append(bool(recalculated))
        if row["all_arm_total_tokens"] != sum(int(row[field]) for field in (
            "agent_input_tokens", "agent_output_tokens", "summary_input_tokens", "summary_output_tokens")):
            errors.append(f"{key}: token sum mismatch")
        if row["summary_attempts"] < row["summary_recorded_calls"] or (
            row["summary_attempts"] != row["summary_recorded_calls"] + row["summary_failures"]
        ):
            errors.append(f"{key}: summary attempt accounting mismatch")
        if row["method"] != "native_summary" and row["summary_attempts"]:
            errors.append(f"{key}: summary in non-native arm")
        if row["method"] != "native_summary" and row["summary_input_tokens"]:
            errors.append(f"{key}: summary input in non-native arm")
        if manifest["mode"] == "api" and row["api_request_attempts"] != len(row["agent_attempt_records"]) + row["summary_attempts"]:
            errors.append(f"{key}: global request accounting mismatch")
        attempts += row["api_request_attempts"]
    if attempts > manifest["max_api_requests"]:
        errors.append("global request limit exceeded")
    return {"complete": len(rows) == len(expected_keys), "rows": len(rows),
            "expected_rows": len(expected_keys), "request_attempts": attempts,
            "quality": {arm: {"passed": sum(values), "total": len(values)}
                        for arm, values in quality.items()}, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    result = audit(args.directory, args.freeze)
    (args.directory / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["errors"] or not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
