"""Independent, zero-model audit for the OpenAI Agents v5 evidence-safe batch.

Checks, from the frozen hashes and the persisted rows only (no re-run, no model):

* source / public-input SHA256 against the v5 freeze;
* the literal registry: re-verified against its own registered public ranges, and
  its fingerprint recomputed for the task and compared with the manifest;
* manifest identity and the honest payload class (v2 mislabelled a public-source
  batch as ``synthetic_only``);
* the complete sample grid, including every failure and cap-exhausted row;
* per-sample answers, tool sequence, structure safety and the strict contract;
* the request ledger: attempts per sample, the global cap, and the report total;
* protected Responses tool-group boundaries in the evidence files;
* **constraint retention, per literal and per sample**, at the final model
  boundary: every registered literal must be present, its occurrence count must
  not have fallen anywhere in the sample, and a registered literal must never be
  lost from a boundary relative to the observed input;
* **the v5 eligibility rule**, reconstructed from the evidence layer alone:
  every tool output that carries a registered literal must still be present
  verbatim (never replaced by a note), no compaction note may lack a source
  pointer, the newest tool group must be kept in full, and the answer's own
  constraint-carrying source must survive;
* ``task_anchor_restore_failures``: persisted per sample, per model call, and
  consistent with the evidence records and the report totals, with the whole-payload
  fallback scope stated;
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

from experiments.runners import openai_agents_literal_registry_v5 as registry

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "pruner_v1", "native_summary")
SCHEMA = "openai_evidence_safe_retention_v5"
MECHANISM = "evidence_safe_retention_v5"
EVIDENCE_STAGES = {"filter_before", "filter_after", "model_input"}
NOTE_PREFIX = "[evidence-safe-retention v5]"
RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
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
#: Constraints that must be readable in the final payload.  The Django literal
#: that exists *only* inside a tool output (``existing_annotations``) is named
#: explicitly: it is the exact loss v4 suffered and v5 must prevent.
REQUIRED_CONSTRAINTS = {
    "django_count_annotations": (
        "filter_references",
        "other_annotation_references",
        "ordering_references",
        "aggregation_decision",
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


def _constraint_carriers(task: str) -> dict[str, tuple[str, ...]]:
    """``tool name -> registered literals that tool's registered range carries``.

    Re-derived from the registry (which is itself verified against its own public
    ranges), so the audit does not need any public text to state which tool
    outputs are constraint carriers.
    """
    carriers: dict[str, tuple[str, ...]] = {}
    for source in registry.SOURCES.get(task, ()):
        if source.literal_labels:
            carriers[source.tool] = tuple(source.literal_labels)
    return carriers


def audit_evidence_file(path: Path, row: dict) -> list[str]:
    """Validate one sample's evidence: schema, boundaries, literals, counters."""
    issues: list[str] = []
    task = str(row.get("scenario"))
    key = f"{task}-{row.get('repeat')}-{row.get('method')}"
    records = _read_jsonl(path)
    if not records:
        return [f"{key}: empty evidence file"]
    expected_fingerprint = registry.registry_fingerprint(task)
    carriers = _constraint_carriers(task)
    literals = registry.literals_for(task)
    if not literals:
        issues.append(f"{key}: no registered literals for this task")
    for record in records:
        if record.get("schema") != SCHEMA:
            issues.append(f"{key}: evidence schema mismatch")
            break
        if record.get("stage") not in EVIDENCE_STAGES or record.get("task") != task:
            issues.append(f"{key}: evidence stage/task mismatch")
            break
        if record.get("registry_fingerprint") != expected_fingerprint:
            issues.append(f"{key}: evidence registry fingerprint mismatch")
        if not HEX64.fullmatch(str(record.get("input_sha256"))):
            issues.append(f"{key}: evidence digest invalid")
        for required in (
            "registered_literals_present",
            "registered_literals_in_unprotected_messages",
            "registered_literal_counts",
            "output_manifest",
        ):
            if not isinstance(record.get(required), (dict, list)):
                issues.append(f"{key}: evidence field missing: {required}")
        if not isinstance(record.get("output_manifest"), list):
            issues.append(f"{key}: evidence output manifest missing")
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
    if [record["index"] for record in after] != list(range(len(after))):
        issues.append(f"{key}: filter indices mismatch")
    if row.get("method") == "none" and before:
        issues.append(f"{key}: baseline unexpectedly filtered")

    # -- constraint retention, per literal, per boundary ------------------
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
        if notes_added == 0 and [
            group["tool_output_sha256"] for group in previous["protected_groups"]
        ] != [group["tool_output_sha256"] for group in following["protected_groups"]]:
            issues.append(f"{key}: tool output changed without a compaction note")
        if notes_added > 0 and int(following["input_bytes"]) >= int(previous["input_bytes"]):
            issues.append(f"{key}: a compaction did not shrink the payload")
        for label in literals:
            if int(following["registered_literal_counts"].get(label, -1)) < int(
                previous["registered_literal_counts"].get(label, -1)
            ):
                issues.append(
                    f"{key}: registered literal {label} lost across the filter boundary"
                )
            if previous["registered_literals_present"].get(label) and not following[
                "registered_literals_present"
            ].get(label):
                issues.append(
                    f"{key}: registered literal {label} disappeared in the filtered payload"
                )
    model_hashes = {record["input_sha256"] for record in model}
    for record in after:
        if record["input_sha256"] not in model_hashes:
            issues.append(f"{key}: filter output not observed at the model boundary")

    # -- the v5 eligibility rule, from the evidence layer alone ------------
    # The registry rule is a property of the plugin arm only: the baseline arm
    # installs no filter and the host-native arm summarises with its own
    # mechanism, so neither is audited against a rule it does not implement.
    is_plugin = str(row.get("method")) == "pruner_v1"
    for index, record in enumerate(model):
        manifest = record.get("output_manifest") or []
        latest = index == len(model) - 1
        for entry in manifest:
            if not is_plugin:
                break
            labels = [label for label in entry.get("registered_literals", []) if label in literals]
            tool = str(entry.get("tool") or "")
            expected = carriers.get(tool, ())
            if tool and expected and not labels:
                issues.append(
                    f"{key}: constraint carrier {tool} does not report its registered literals"
                )
            if labels and entry.get("output_replaced_by_note"):
                issues.append(
                    f"{key}: a constraint-carrying output was replaced by a note "
                    f"({tool}: {','.join(labels)})"
                )
            if labels and not entry.get("pinned"):
                issues.append(f"{key}: constraint carrier {tool} is not pinned")
            if entry.get("output_replaced_by_note") and not entry.get(
                "note_has_source_pointer"
            ):
                issues.append(
                    f"{key}: a compaction note for {tool} carries no source pointer"
                )
        if not is_plugin:
            continue
        # ``existing_annotations`` reaches the model only through a registered
        # source-code tool output, so it is required from the first boundary that
        # carries one; the issue-statement literals are required at every boundary.
        carries_registered_source = any(
            str(entry.get("tool") or "") in carriers for entry in manifest
        )
        for label in literals:
            if record["registered_literals_present"].get(label):
                continue
            if label == "aggregation_decision" and not carries_registered_source:
                continue
            issues.append(
                f"{key}: registered literal {label} absent from a model boundary"
            )
        if not record.get("most_recent_tool_group_kept_in_full", False):
            issues.append(f"{key}: the most recent tool group was not kept in full")
        if not record.get("note_pointer_coverage", True):
            issues.append(f"{key}: a compaction note lacked a recovery pointer")
        if latest:
            # A registered literal must never be lost from the payload that the
            # provider actually billed for.
            for label in literals:
                if record["registered_literals_present"].get(label):
                    continue
                if label == "aggregation_decision" and not carries_registered_source:
                    continue
                issues.append(
                    f"{key}: registered literal {label} absent from the final model input"
                )

    # -- explicit required-constraint check on the final input -------------
    final = model[-1]
    if is_plugin:
        for label in REQUIRED_CONSTRAINTS.get(task, ()):
            if not final["registered_literals_present"].get(label):
                issues.append(
                    f"{key}: required constraint {label} missing from the final model input"
                )

    # -- restore-failure ledger -------------------------------------------
    per_call = [int(record.get("task_anchor_restore_failures", -1)) for record in model]
    if per_call != [int(value) for value in row.get("restore_failures_by_model_call", [])]:
        issues.append(f"{key}: restore-failure ledger mismatch")
    if per_call and per_call[-1] != int(row.get("task_anchor_restore_failures", -1)):
        issues.append(f"{key}: restore-failure total mismatch")
    if not bool(row.get("restore_failure_is_whole_prefix_fallback")):
        issues.append(f"{key}: restore-failure fallback scope not stated")
    if int(row.get("task_restore_fallbacks", -1)) != int(
        row.get("task_anchor_restore_failures", -2)
    ):
        issues.append(f"{key}: fallback count does not equal the restore-failure count")
    if int(row.get("budget_fallbacks", -1)) != record_budget(row):
        issues.append(f"{key}: budget-fallback ledger mismatch")
    text = path.read_text(encoding="utf-8")
    if re.search(r"sk-[A-Za-z0-9]{24,}", text):
        issues.append(f"{key}: credential-like text in evidence")
    return issues


def record_budget(row: dict) -> int:
    """Reported budget fallbacks, cross-checked against the per-call sequence."""
    sequence = [int(value) for value in row.get("budget_fallbacks_by_model_call", [])]
    return sequence[-1] if sequence else 0


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
    if manifest.get("mechanism", {}).get("name") != MECHANISM:
        issues.append("manifest does not declare the evidence-safe retention mechanism")
    if manifest.get("mechanism", {}).get("registry") != registry.REGISTRY_SCHEMA:
        issues.append("manifest does not declare the literal registry schema")
    if manifest.get("persisted_retention_fields") != freeze.get("persisted_retention_fields"):
        issues.append("persisted retention field list mismatch")
    if int(manifest.get("filter_hard_bytes", -1)) != int(freeze["filter_hard_bytes"]):
        issues.append("filter byte ceiling mismatch")
    if int(manifest.get("bytes_per_estimated_token", -1)) != int(
        freeze["bytes_per_estimated_token"]
    ):
        issues.append("byte-per-token constant mismatch")

    # -- literal registry (independent of the runner) ---------------------
    registry_problems = registry.all_problems(freeze["scenarios"])
    if registry_problems:
        issues.append("literal registry does not verify: " + "; ".join(registry_problems))
    for scenario in freeze["scenarios"]:
        wanted = freeze.get("registry_fingerprints", {}).get(scenario)
        if wanted is None:
            issues.append(f"freeze does not record a registry fingerprint for {scenario}")
            continue
        if registry.registry_fingerprint(scenario) != wanted:
            issues.append(f"registry fingerprint mismatch: {scenario}")
        if manifest.get("literal_registry", {}).get("fingerprints", {}).get(scenario) != wanted:
            issues.append(f"manifest registry fingerprint mismatch: {scenario}")

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
        final_answer = str(row.get("final_output", ""))
        answer_ok = bool(re.fullmatch(pattern, final_answer, flags=re.IGNORECASE)) and all(
            term.lower() in final_answer.lower() for term in terms
        )
        format_ok = (
            final_answer.startswith("RESULT ")
            and "\n" not in final_answer
            and len(final_answer) <= 160
        )
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
    constraint_losses = {
        arm: sum(
            1
            for row in rows
            if row.get("method") == arm
            and not all(
                bool(value)
                for value in (row.get("evidence_registered_literals_present_final") or {}).values()
            )
        )
        for arm in METHODS
    }
    return {
        "complete": not issues,
        "issues": issues,
        "batch": batch.name,
        "expected_batch": expected_batch,
        "freeze": str(freeze_path),
        "mechanism": MECHANISM,
        "registry_schema": registry.REGISTRY_SCHEMA,
        "samples": len(rows),
        "attempts": attempts,
        "evidence_checked": evidence_checked,
        "successes": successes,
        "failures": failures,
        "task_anchor_restore_failures": restore_failures,
        "samples_with_a_missing_registered_literal": constraint_losses,
        "paired": paired,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--freeze", required=True, type=Path)
    parser.add_argument(
        "--batch-name",
        default=None,
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
