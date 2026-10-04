"""Independent, zero-model audit for the OpenAI Agents v4 selective-retention batch.

Checks, from the frozen hashes and the persisted rows only (no re-run, no model):

* source / public-input SHA256 against the v4 freeze;
* manifest identity and the honest payload class (v2 mislabelled a public-source
  batch as ``synthetic_only``);
* the complete sample grid, including every failure and cap-exhausted row;
* per-sample answers, tool sequence, structure safety and the strict contract;
* the request ledger: attempts per sample, the global cap, and the report total;
* protected Responses tool-group boundaries in the evidence files, plus the one
  sanctioned tool-output transition (an already-served output replaced by this
  module's compaction note);
* ``task_anchor_restore_failures``: persisted per sample, per model call, and
  consistent with the evidence records and the report totals;
* paired means over the full sample set for real input tokens and for the
  *complete* total including every auxiliary summary request and failed attempt.

The contract rules are restated here rather than imported from the runner, so a
mistake in the runner's own scoring cannot hide itself in the audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "pruner_v1", "native_summary")
SCHEMA = "openai_selective_retention_v4"
EVIDENCE_STAGES = {"filter_before", "filter_after", "model_input"}
RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
)
CONTRACTS = {
    "pytest_mro": (
        ("read_pytest_mark_decorator", "read_pytest_mark_storage", "read_pytest_mark_context"),
        r"RESULT issue=pytest-10356 cause=.*getattr.* fix=.*__dict__.*MRO.*",
        ("getattr", "__dict__", "MRO"),
    ),
    "pylint_regex_csv": (
        ("read_pylint_option", "read_pylint_transformer", "read_pylint_csv_helper"),
        r"RESULT issue=pylint-8898 cause=.*_splitstrip.* fix=.*(?:quantifier|brace).*",
        ("_splitstrip", "quantifier"),
    ),
    "django_count_annotations": (
        ("read_django_count_entry", "read_django_aggregation", "read_django_count_call"),
        r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
        ("existing_annotations", "subquery", "referenced"),
    ),
}
#: Constraints that must be readable in the final payload for these public tasks.
REQUIRED_CONSTRAINTS = {
    "django_count_annotations": (
        "filter_references",
        "other_annotation_references",
        "ordering_references",
    ),
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def audit_evidence_file(path: Path, row: dict) -> list[str]:
    """Validate one sample's evidence: schema, boundaries, counters."""
    issues: list[str] = []
    key = f"{row.get('scenario')}-{row.get('repeat')}-{row.get('method')}"
    records = _read_jsonl(path)
    if not records:
        return [f"{key}: empty evidence file"]
    for record in records:
        if record.get("schema") != SCHEMA:
            issues.append(f"{key}: evidence schema mismatch")
            break
        if record.get("stage") not in EVIDENCE_STAGES or record.get("task") != row.get("scenario"):
            issues.append(f"{key}: evidence stage/task mismatch")
            break
        if not HEX64.fullmatch(str(record.get("input_sha256"))):
            issues.append(f"{key}: evidence digest invalid")
        for required in ("constraint_present", "constraint_present_in_unprotected_messages"):
            if not isinstance(record.get(required), dict):
                issues.append(f"{key}: evidence constraint flags missing")
    model = [record for record in records if record["stage"] == "model_input"]
    before = [record for record in records if record["stage"] == "filter_before"]
    after = [record for record in records if record["stage"] == "filter_after"]
    if len(model) != int(row.get("model_calls", -1)):
        issues.append(f"{key}: model-call evidence mismatch")
    if len(model) != int(row.get("input_evidence_model_calls", -1)):
        issues.append(f"{key}: recorded model-call evidence mismatch")
    if len(before) != len(after):
        issues.append(f"{key}: unpaired filter evidence")
    if len(before) != int(row.get("input_evidence_filter_calls", -1)):
        issues.append(f"{key}: filter-call evidence mismatch")
    if [record["index"] for record in model] != list(range(len(model))):
        issues.append(f"{key}: model indices mismatch")
    if [record["index"] for record in before] != list(range(len(before))):
        issues.append(f"{key}: filter indices mismatch")
    if row.get("method") == "none" and before:
        issues.append(f"{key}: baseline unexpectedly filtered")
    model_hashes = {record["input_sha256"] for record in model}
    for previous, following in zip(before, after):
        if [group["group_id"] for group in previous["protected_groups"]] != [
            group["group_id"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: tool group boundaries moved")
        if [group["tool_output_count"] for group in previous["protected_groups"]] != [
            group["tool_output_count"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: a protected call/output pair was split")
        notes_added = int(following.get("compaction_note_count", 0)) - int(
            previous.get("compaction_note_count", 0)
        )
        if notes_added == 0 and [group["tool_output_sha256"] for group in previous["protected_groups"]] != [
            group["tool_output_sha256"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: tool output changed without a compaction note")
        if notes_added > 0 and int(following["input_bytes"]) >= int(previous["input_bytes"]):
            issues.append(f"{key}: a compaction did not shrink the payload")
        if following["input_sha256"] not in model_hashes:
            issues.append(f"{key}: filter output not observed at the model boundary")
    # Restore-failure ledger.
    per_call = [int(record.get("task_anchor_restore_failures", -1)) for record in model]
    if per_call != [int(value) for value in row.get("restore_failures_by_model_call", [])]:
        issues.append(f"{key}: restore-failure ledger mismatch")
    if per_call and per_call[-1] != int(row.get("task_anchor_restore_failures", -1)):
        issues.append(f"{key}: restore-failure total mismatch")
    if not bool(row.get("restore_failure_is_whole_prefix_fallback")):
        issues.append(f"{key}: restore-failure fallback scope not stated")
    if int(row.get("task_restore_fallbacks", -1)) != int(row.get("task_anchor_restore_failures", -2)):
        issues.append(f"{key}: fallback count does not equal the restore-failure count")
    # Constraints at the last model boundary.
    for label in REQUIRED_CONSTRAINTS.get(str(row.get("scenario")), ()):
        if row.get("method") != "pruner_v1":
            continue
        final = model[-1]
        if not final["constraint_present"].get(label):
            issues.append(f"{key}: constraint {label} missing from the final model input")
        if not final["constraint_present_in_unprotected_messages"].get(label):
            issues.append(f"{key}: constraint {label} missing from the unprotected messages")
    text = path.read_text(encoding="utf-8")
    if re.search(r"sk-[A-Za-z0-9]{24,}", text):
        issues.append(f"{key}: credential-like text in evidence")
    return issues


def audit(batch: Path, freeze_path: Path, batch_name: str | None = None) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(batch / "samples.jsonl")
    issues: list[str] = []
    expected_batch = str(batch_name or freeze["batch"])

    # -- freeze ----------------------------------------------------------
    if batch.name != expected_batch or manifest.get("experiment_id") != expected_batch:
        issues.append("batch ID mismatch")
    for relative, wanted in {**freeze["source_sha256"], **freeze["public_input_sha256"]}.items():
        if _digest(ROOT / relative) != wanted:
            issues.append(f"hash mismatch: {relative}")
    for field, wanted in (
        ("scenarios", freeze["scenarios"]),
        ("methods", list(METHODS)),
        ("repeats", freeze["repeats"]),
        ("maximum_api_requests", freeze["max_api_requests"]),
        ("max_output_tokens", freeze["max_output_tokens"]),
    ):
        if manifest.get(field) != wanted:
            issues.append(f"manifest mismatch: {field}")
    if manifest.get("model") != freeze["model"] or manifest.get("base_url") != freeze["base_url"]:
        issues.append("model/endpoint mismatch")
    if manifest.get("trigger_policy") != "symmetric_budget":
        issues.append("trigger policy mismatch")
    if manifest.get("budget_calibration", {}).get("provider_tokens") != freeze["provider_budget_tokens"]:
        issues.append("provider budget mismatch")
    if manifest.get("data_class") != "public_source_diagnostic":
        issues.append("manifest payload class is not the honest public-source class")
    if manifest.get("mechanism", {}).get("name") != "selective_retention_v4":
        issues.append("manifest does not declare the selective-retention mechanism")
    if manifest.get("persisted_retention_fields") != freeze.get("persisted_retention_fields"):
        issues.append("persisted retention field list mismatch")

    # -- grid ------------------------------------------------------------
    keys = [(row.get("scenario"), row.get("repeat"), row.get("method")) for row in rows]
    expected = [
        (scenario, repeat, arm)
        for scenario in freeze["scenarios"]
        for repeat in range(freeze["repeats"])
        for arm in METHODS
    ]
    if Counter(keys) != Counter(expected):
        issues.append("sample grid mismatch")
    if len(rows) != freeze["samples"]:
        issues.append(f"sample count mismatch: {len(rows)} != {freeze['samples']}")

    # -- per sample ------------------------------------------------------
    attempts = 0
    evidence_checked = 0
    for row in rows:
        key = (row.get("scenario"), row.get("repeat"), row.get("method"))
        if key[0] not in CONTRACTS:
            issues.append(f"unknown task: {key}")
            continue
        for field in RETENTION_FIELDS:
            if field not in row:
                issues.append(f"missing retention field {field}: {key}")
        tool_names, pattern, terms = CONTRACTS[key[0]]
        final = str(row.get("final_output", ""))
        answer_ok = bool(re.fullmatch(pattern, final, flags=re.IGNORECASE)) and all(
            term.lower() in final.lower() for term in terms
        )
        format_ok = final.startswith("RESULT ") and "\n" not in final and len(final) <= 160
        observed = tuple(row.get("tool_names", []))
        position = 0
        for name in observed:
            if position < len(tool_names) and name == tool_names[position]:
                position += 1
        tools_ok = set(observed) == set(tool_names) and position == len(tool_names)
        safe = all(
            bool(row.get(flag))
            for flag in ("constraint_preserved", "pairing_integrity", "structure_safe")
        )
        success = answer_ok and format_ok and tools_ok and safe and not row.get("error_type")
        if bool(row.get("success")) != success:
            issues.append(f"quality mismatch: {key}")
        inputs = sum(int(value) for value in row.get("actual_input_tokens_by_call", []))
        outputs = sum(int(value) for value in row.get("actual_output_tokens_by_call", []))
        if inputs != row.get("actual_input_tokens") or outputs != row.get("actual_output_tokens"):
            issues.append(f"model usage mismatch: {key}")
        complete_total = (
            inputs
            + outputs
            + int(row.get("summary_input_tokens", 0))
            + int(row.get("summary_output_tokens", 0))
        )
        if complete_total != row.get("all_arm_total_tokens"):
            issues.append(f"complete total mismatch: {key}")
        calls = int(row.get("api_request_attempts", 0))
        attempts += calls
        if calls < int(row.get("model_calls", 0)) + int(row.get("summary_calls", 0)):
            issues.append(f"request ledger mismatch: {key}")
        if key[2] != "native_summary" and int(row.get("summary_calls", 0)):
            issues.append(f"unexpected summary calls: {key}")
        relative = row.get("input_evidence_file")
        if relative != f"input-evidence/{key[0]}-{key[1]}-{key[2]}.jsonl":
            issues.append(f"{key}: evidence path mismatch")
            continue
        path = batch / relative
        if not path.is_file():
            issues.append(f"{key}: evidence file missing")
            continue
        evidence_checked += 1
        issues.extend(audit_evidence_file(path, row))
    if attempts > freeze["max_api_requests"]:
        issues.append("global request cap exceeded")
    if attempts != report.get("request_budget", {}).get("recorded_attempts_all_rows"):
        issues.append("global request ledger mismatch")

    # -- paired means over the full sample set ---------------------------
    keyed = {(row.get("scenario"), row.get("repeat"), row.get("method")): row for row in rows}
    paired: dict[str, dict] = {}
    for arm in METHODS[1:]:
        input_savings: list[float] = []
        total_savings: list[float] = []
        success_deltas: list[float] = []
        for scenario in freeze["scenarios"]:
            for repeat in range(freeze["repeats"]):
                baseline = keyed.get((scenario, repeat, "none"))
                candidate = keyed.get((scenario, repeat, arm))
                if not baseline or not candidate:
                    continue
                if baseline["actual_input_tokens"]:
                    input_savings.append(
                        (baseline["actual_input_tokens"] - candidate["actual_input_tokens"])
                        / baseline["actual_input_tokens"]
                    )
                if baseline["all_arm_total_tokens"]:
                    total_savings.append(
                        (baseline["all_arm_total_tokens"] - candidate["all_arm_total_tokens"])
                        / baseline["all_arm_total_tokens"]
                    )
                success_deltas.append(
                    float(bool(candidate["success"])) - float(bool(baseline["success"]))
                )
        mean = sum(total_savings) / len(total_savings) if total_savings else None
        paired[arm] = {
            "paired_n": len(total_savings),
            "actual_input_savings_mean": sum(input_savings) / len(input_savings)
            if input_savings
            else None,
            "complete_total_savings_mean": mean,
            "complete_total_savings_positive": sum(value > 0 for value in total_savings),
            "success_delta_mean": sum(success_deltas) / len(success_deltas)
            if success_deltas
            else 0.0,
        }
        recorded = report.get("comparisons", {}).get(f"{arm}_vs_none", {})
        if recorded.get("paired_n") != len(total_savings):
            issues.append(f"paired savings mismatch: {arm}")
        elif total_savings and not math.isclose(
            recorded.get("all_arm_total_token_savings_rate_vs_baseline", float("nan")),
            mean,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            issues.append(f"paired savings mismatch: {arm}")

    failures = {
        arm: sum(1 for row in rows if row.get("method") == arm and row.get("error_type"))
        for arm in METHODS
    }
    successes = {
        arm: sum(1 for row in rows if row.get("method") == arm and row.get("success"))
        for arm in METHODS
    }
    restore_failures = {
        arm: sum(
            int(row.get("task_anchor_restore_failures", 0))
            for row in rows
            if row.get("method") == arm
        )
        for arm in METHODS
    }
    return {
        "complete": not issues,
        "issues": issues,
        "batch": batch.name,
        "expected_batch": expected_batch,
        "freeze": str(freeze_path),
        "samples": len(rows),
        "attempts": attempts,
        "evidence_checked": evidence_checked,
        "successes": successes,
        "failures": failures,
        "task_anchor_restore_failures": restore_failures,
        "paired": paired,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--freeze", required=True, type=Path)
    parser.add_argument(
        "--batch-name", default=None,
        help="Override the expected batch id (zero-API self-tests only).",
    )
    args = parser.parse_args()
    result = audit(args.batch, args.freeze, args.batch_name)
    (args.batch / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
