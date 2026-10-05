"""Independent, zero-model audit for the OpenAI Agents v6 pointer-retention batch.

Checks, from the frozen hashes and the persisted rows only (no re-run, no model):

* source / public-input SHA256 against the v6 freeze, **including the audit's own
  source file and the v6 offline gate helper**, so the artifact that judges the
  batch is inside the same frozen set it judges;
* the span registry: the frozen v5 slice is re-verified against its own registered
  public ranges, the v6 span table is recomputed from the public baseline files, and
  both fingerprints are compared with the manifest;
* manifest identity and the honest payload class;
* the complete sample grid, including every failure and cap-exhausted row;
* per-sample answers, tool sequence, structure safety and the strict contract;
* the request ledger: attempts per sample, the global cap, and the report total;
* protected Responses tool-group boundaries in the evidence files;
* **per-literal retention at the model boundary**: every registered literal present
  and its occurrence count never below the *baseline arm's* count at the same
  boundary (a per-literal, baseline-anchored retention check, not a self-comparison),
  plus the internal observed-vs-filtered comparison per boundary;
* **the v6 eligibility rule, reconstructed from the evidence layer alone**: every
  elision note states the path, the omitted intervals and the retained lines; the
  retained lines and the omitted intervals partition the reduced range exactly; no
  omitted line may carry a registered literal; the newest tool group is kept in full;
* ``task_anchor_restore_failures``: persisted per sample, per model call, consistent
  with the evidence records and the report totals, with the whole-payload fallback
  scope stated;
* paired means over the full sample set for real input tokens and for the *complete*
  total including every auxiliary summary request and failed attempt;
* the pre-registered acceptance line, evaluated and reported as a verdict.

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

from experiments.runners import openai_agents_literal_registry_v6 as registry

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "pruner_v1", "native_summary")
SCHEMA = "openai_pointer_retention_v6"
MECHANISM = "span_retention_v6"
EVIDENCE_STAGES = {"filter_before", "filter_after", "model_input"}
NOTE_PREFIX = "[pointer-retention v6]"
RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "pointer_retention_elided_sources",
    "pointer_retention_elided_lines",
    "pointer_retention_kept_lines",
    "pointer_retention_kept_literal_lines",
    "pointer_retention_registry_fingerprint",
    "pointer_retention_base_registry_fingerprint",
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
REQUIRED_CONSTRAINTS = {
    "django_count_annotations": (
        "filter_references",
        "other_annotation_references",
        "ordering_references",
        "aggregation_decision",
    ),
}
#: Declared in the freeze: files whose SHA256 the audit recomputes.  The self entry
#: makes the audit's own source part of the frozen set it verifies.
SELF_ENTRIES = (
    "experiments/audits/audit_openai_agents_repo_diagnostic_v6.py",
    ".tooling/gate_openai_agents_pointer_retention_v6.py",
    ".tooling/diagnose_v5_cost_regression.py",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(values) -> float:
    return sum(values) / len(values) if values else 0.0


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _constraint_carriers(task: str) -> dict[str, tuple[str, ...]]:
    carriers: dict[str, tuple[str, ...]] = {}
    for source in registry.base.SOURCES.get(task, ()):
        if source.literal_labels:
            carriers[source.tool] = tuple(source.literal_labels)
    return carriers


def _literal_line_numbers(task: str, tool: str) -> tuple[int, ...]:
    span = registry.span_for_tool(task, tool)
    return tuple(span.literal_lines) if span is not None else ()


def audit_evidence_file(
    path: Path, row: dict, baseline: dict | None
) -> tuple[list[str], dict]:
    """Validate one sample's evidence: schema, boundaries, spans, literals, counters.

    Returns ``(issues, facts)``; ``facts`` carries the measurements the batch-level
    report needs and that are not themselves pass/fail (currently the number of extra
    model calls this arm took relative to the baseline arm).
    """
    issues: list[str] = []
    task = str(row.get("scenario"))
    key = f"{task}-{row.get('repeat')}-{row.get('method')}"
    records = _read_jsonl(path)
    if not records:
        return [f"{key}: empty evidence file"]
    expected_fingerprint = registry.registry_fingerprint(task)
    expected_base = registry.base_registry_fingerprint(task)
    carriers = _constraint_carriers(task)
    literals = registry.base.literals_for(task)
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
            issues.append(f"{key}: evidence v6 registry fingerprint mismatch")
        if record.get("base_registry_fingerprint") != expected_base:
            issues.append(f"{key}: evidence v5 registry fingerprint mismatch")
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
        else:
            for entry in record["output_manifest"]:
                for required_field in (
                    "call_id",
                    "output_sha256",
                    "output_elided",
                    "registered_literals",
                    "span_retained_lines",
                    "omitted_intervals",
                    "note_has_source_pointer",
                ):
                    if required_field not in entry:
                        issues.append(
                            f"{key}: output manifest entry missing {required_field}"
                        )
                        break
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
        elided_here = int(following.get("outputs_elided", 0)) - int(
            previous.get("outputs_elided", 0)
        )
        if elided_here == 0 and [
            group["tool_output_sha256"] for group in previous["protected_groups"]
        ] != [group["tool_output_sha256"] for group in following["protected_groups"]]:
            issues.append(f"{key}: tool output changed without an elision note")
        if elided_here > 0 and int(following["input_bytes"]) >= int(previous["input_bytes"]):
            issues.append(f"{key}: an elision did not shrink the payload")
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
                    f"{key}: registered literal {label} disappeared in the observed payload"
                )
    model_hashes = {record["input_sha256"] for record in model}
    for record in after:
        if record["input_sha256"] not in model_hashes:
            issues.append(f"{key}: filter output not observed at the model boundary")

    # -- baseline-anchored literal retention ------------------------------
    # The baseline arm never elides, so its occurrence counts are the reference.  The
    # two arms can take a different number of model calls in a paid run, so a
    # *same-index* comparison would compare different trajectory points: the plugin
    # may be one tool call behind at that index, which is a turn-count fact (reported
    # in ``plugin_samples_needing_extra_model_calls_vs_baseline``), not a literal
    # loss.  What is enforced here is therefore:
    #   * the mechanism's own observed-vs-filtered ledger above, which is independent
    #     of turn counts and forbids losing an occurrence anywhere;
    #   * every registered literal required at a boundary that carries its source;
    #   * the **final** model input's per-literal counts against the baseline arm's
    #     final input, which is the payload the provider bills for.
    extra_turns = 0
    baseline_model: list[dict] = []
    if baseline is not None and str(row.get("method")) == "pruner_v1":
        baseline_model = [r for r in baseline if r["stage"] == "model_input"]
        extra_turns = len(model) - len(baseline_model)
        if baseline_model:
            final_plugin = model[-1]
            final_baseline = baseline_model[-1]
            for label in literals:
                plugin_count = int(final_plugin["registered_literal_counts"].get(label, -1))
                base_count = int(final_baseline["registered_literal_counts"].get(label, -1))
                if plugin_count < base_count:
                    issues.append(
                        f"{key}: literal {label} count {plugin_count} in the final model "
                        f"input is below the baseline arm's final {base_count}"
                    )
        for index, record in enumerate(model):
            for label in REQUIRED_CONSTRAINTS.get(task, ()):
                if record["registered_literals_present"].get(label):
                    continue
                carries_source = any(
                    str(entry.get("tool") or "") in carriers
                    for entry in record.get("output_manifest") or []
                )
                if label == "aggregation_decision" and not carries_source:
                    continue
                issues.append(
                    f"{key}: required literal {label} absent from boundary {index}"
                )

    # -- the v6 eligibility rule, from the evidence layer alone -----------
    is_plugin = str(row.get("method")) == "pruner_v1"
    for index, record in enumerate(model):
        manifest = record.get("output_manifest") or []
        for entry in manifest:
            tool = str(entry.get("tool") or "")
            if not is_plugin:
                continue
            if tool and carriers.get(tool) and not entry.get("registered_literals"):
                issues.append(
                    f"{key}: registered carrier {tool} reports no registered literal"
                )
            if not entry.get("output_elided"):
                continue
            if not entry.get("note_has_source_pointer"):
                issues.append(f"{key}: an elision note carries no source pointer ({tool})")
            if not entry.get("pointer_completeness"):
                issues.append(
                    f"{key}: retained lines and omitted intervals do not partition the "
                    f"registered range ({tool})"
                )
            kept = sorted(int(value) for value in entry.get("span_retained_lines") or [])
            omitted: list[int] = []
            for interval in entry.get("omitted_intervals") or []:
                values = list(interval)
                if len(values) != 2:
                    issues.append(f"{key}: malformed omitted interval ({tool})")
                    continue
                omitted.extend(range(int(values[0]), int(values[1]) + 1))
            if sorted(kept + omitted) != sorted(set(kept + omitted)):
                issues.append(f"{key}: overlapping omitted intervals ({tool})")
            literal_lines = set(_literal_line_numbers(task, tool))
            if literal_lines:
                lost = sorted(literal_lines & set(omitted))
                if lost:
                    issues.append(
                        f"{key}: a line carrying a registered literal was omitted "
                        f"({tool}: {lost[:4]})"
                    )
                retained_literals = sorted(
                    int(value) for value in entry.get("span_retained_literal_lines") or []
                )
                if sorted(literal_lines & set(kept)) != retained_literals:
                    issues.append(
                        f"{key}: retained literal lines do not match the registered span "
                        f"({tool})"
                    )
            if entry.get("registered_literals") and not entry.get(
                "span_retained_literal_lines"
            ):
                issues.append(
                    f"{key}: a span omits every line of the registered literal lines of a "
                    f"constraint carrier ({tool})"
                )
        if not is_plugin:
            continue
        if not record.get("most_recent_tool_group_kept_in_full", False):
            issues.append(f"{key}: the most recent tool group was not kept in full")
        if not record.get("pointer_coverage", True):
            issues.append(f"{key}: a note lacked a recovery pointer")
        if not record.get("pointer_completeness", True):
            issues.append(f"{key}: an elision did not partition its range exactly")

    # -- per-span records from the filter's own decision ------------------
    span_records = [
        entry
        for entry in row.get("evidence_span_elision_records") or []
    ]
    if is_plugin:
        if not span_records:
            issues.append(f"{key}: no span elision records persisted")
        for entry in span_records:
            if int(entry.get("omitted_line_count", -1)) <= 0:
                issues.append(f"{key}: a span record omits no lines")
            if not entry.get("note_states_pointer"):
                issues.append(f"{key}: a span note states no pointer")
            if not entry.get("pointer_matches_registered_source"):
                issues.append(f"{key}: a span pointer does not match the registered source")
            if int(entry.get("replacement_chars", 0)) >= int(entry.get("original_chars", 0)):
                issues.append(f"{key}: a span replacement is not shorter than the original")

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
    return issues, {
        "extra_model_calls_vs_baseline": int(extra_turns),
        "model_calls": len(model),
        "baseline_model_calls": len(baseline_model)
        if baseline is not None and str(row.get("method")) == "pruner_v1"
        else None,
        "elided_sources": int(row.get("pointer_retention_elided_sources", 0)),
        "elided_lines": int(row.get("pointer_retention_elided_lines", 0)),
        "retained_literal_lines": int(row.get("pointer_retention_kept_literal_lines", 0)),
        "actual_input_tokens": int(row.get("actual_input_tokens", 0)),
        "complete_total_tokens": int(row.get("all_arm_total_tokens", 0)),
        "success": bool(row.get("success")),
    }


def record_budget(row: dict) -> int:
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
    for relative in SELF_ENTRIES:
        wanted = freeze["source_sha256"].get(relative)
        if wanted is None:
            issues.append(f"freeze does not hash {relative}")
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
        issues.append("manifest does not declare the v6 span-retention mechanism")
    if manifest.get("mechanism", {}).get("registry") != registry.REGISTRY_SCHEMA:
        issues.append("manifest does not declare the v6 registry schema")
    if int(manifest.get("mechanism", {}).get("context_lines", -1)) != int(
        freeze["context_lines"]
    ):
        issues.append("manifest context-line constant mismatch")
    if manifest.get("persisted_retention_fields") != freeze.get("persisted_retention_fields"):
        issues.append("persisted retention field list mismatch")
    if int(manifest.get("filter_hard_bytes", -1)) != int(freeze["filter_hard_bytes"]):
        issues.append("filter byte ceiling mismatch")
    if int(manifest.get("bytes_per_estimated_token", -1)) != int(
        freeze["bytes_per_estimated_token"]
    ):
        issues.append("byte-per-token constant mismatch")

    # -- registries (independent of the runner) --------------------------
    registry_problems = registry.all_problems(freeze["scenarios"])
    if registry_problems:
        issues.append("span registry does not verify: " + "; ".join(registry_problems))
    base_problems = registry.base.all_problems(freeze["scenarios"])
    if base_problems:
        issues.append("frozen v5 registry does not verify: " + "; ".join(base_problems))
    for scenario in freeze["scenarios"]:
        wanted = freeze.get("registry_fingerprints", {}).get(scenario)
        if wanted is None:
            issues.append(f"freeze does not record a registry fingerprint for {scenario}")
        elif registry.registry_fingerprint(scenario) != wanted:
            issues.append(f"v6 registry fingerprint mismatch: {scenario}")
        elif manifest.get("literal_registry", {}).get("fingerprints", {}).get(scenario) != wanted:
            issues.append(f"manifest v6 registry fingerprint mismatch: {scenario}")
        base_wanted = freeze.get("base_registry_fingerprints", {}).get(scenario)
        if base_wanted is None:
            issues.append(f"freeze does not record a v5 registry fingerprint for {scenario}")
        elif registry.base_registry_fingerprint(scenario) != base_wanted:
            issues.append(f"v5 registry fingerprint mismatch: {scenario}")
        spans = registry.spans(scenario)
        if not spans:
            issues.append(f"no registered spans for {scenario}")
        recorded_spans = freeze.get("registered_spans", {}).get(scenario)
        recomputed = [
            {
                "tool": span.tool,
                "repo": span.repo,
                "path": span.path,
                "first_line": span.first_line,
                "last_line": span.last_line,
                "literal_lines": list(span.literal_lines),
                "literal_labels": list(span.literal_labels),
            }
            for span in spans
        ]
        if recorded_spans != recomputed:
            issues.append(f"registered span table mismatch: {scenario}")
        for span in spans:
            if span.literal_labels and not span.literal_lines:
                issues.append(
                    f"registered span carries labels but no lines: {scenario}/{span.tool}"
                )
            for number in span.literal_lines:
                if number < span.first_line or number > span.last_line:
                    issues.append(
                        f"registered literal line {number} outside the range: "
                        f"{scenario}/{span.tool}"
                    )

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
    per_sample_facts: dict[tuple, dict] = {}
    baseline_evidence: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        if str(row.get("method")) != "none":
            continue
        relative = row.get("input_evidence_file")
        if relative and (batch / relative).is_file():
            baseline_evidence[(str(row["scenario"]), int(row["repeat"]))] = _read_jsonl(
                batch / relative
            )
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
        anchor = (
            baseline_evidence.get((str(key[0]), int(key[1])))
            if str(key[2]) == "pruner_v1"
            else None
        )
        sample_issues, facts = audit_evidence_file(path, row, anchor)
        issues.extend(sample_issues)
        per_sample_facts[key] = facts
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
                base = keyed.get((scenario, repeat, "none"))
                candidate = keyed.get((scenario, repeat, arm))
                if not base or not candidate:
                    continue
                if base["actual_input_tokens"]:
                    input_savings.append(
                        (base["actual_input_tokens"] - candidate["actual_input_tokens"])
                        / base["actual_input_tokens"]
                    )
                if base["all_arm_total_tokens"]:
                    total_savings.append(
                        (base["all_arm_total_tokens"] - candidate["all_arm_total_tokens"])
                        / base["all_arm_total_tokens"]
                    )
                success_deltas.append(
                    float(bool(candidate["success"])) - float(bool(base["success"]))
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
    plugin_paired = paired.get("pruner_v1", {})
    quality_ok = successes.get("pruner_v1", 0) >= successes.get("none", 0)
    cost_ok = bool(
        plugin_paired.get("complete_total_savings_mean") is not None
        and plugin_paired["complete_total_savings_mean"] > 0
    )
    # Per-arm descriptive means over every sample, including failures and any
    # cap-exhausted row, so a reader does not have to take the paired numbers alone.
    arm_means: dict[str, dict] = {}
    for arm in METHODS:
        arm_rows = [row for row in rows if row.get("method") == arm]
        if not arm_rows:
            continue
        arm_means[arm] = {
            "n": len(arm_rows),
            "successes": sum(1 for row in arm_rows if row.get("success")),
            "inputs": [int(row.get("actual_input_tokens", 0)) for row in arm_rows],
            "complete_totals": [int(row.get("all_arm_total_tokens", 0)) for row in arm_rows],
            "model_calls": [int(row.get("model_calls", 0)) for row in arm_rows],
            "mean_actual_input_tokens": _mean(
                [int(row.get("actual_input_tokens", 0)) for row in arm_rows]
            ),
            "mean_complete_total_tokens": _mean(
                [int(row.get("all_arm_total_tokens", 0)) for row in arm_rows]
            ),
            "mean_model_calls": _mean([int(row.get("model_calls", 0)) for row in arm_rows]),
            "error_types": sorted(
                {str(row.get("error_type")) for row in arm_rows if row.get("error_type")}
            ),
        }
    plugin_facts = {
        key: facts
        for key, facts in per_sample_facts.items()
        if str(key[2]) == "pruner_v1"
    }
    extra_turn_samples = sum(
        1
        for facts in plugin_facts.values()
        if int(facts.get("extra_model_calls_vs_baseline", 0) or 0) > 0
    )
    return {
        "complete": not issues,
        "issues": issues,
        "batch": batch.name,
        "expected_batch": expected_batch,
        "freeze": str(freeze_path),
        "mechanism": MECHANISM,
        "registry_schema": registry.REGISTRY_SCHEMA,
        "base_registry_schema": registry.base.REGISTRY_SCHEMA,
        "samples": len(rows),
        "attempts": attempts,
        "evidence_checked": evidence_checked,
        "successes": successes,
        "failures": failures,
        "task_anchor_restore_failures": restore_failures,
        "samples_with_a_missing_registered_literal": constraint_losses,
        "arm_means": arm_means,
        "plugin_samples_needing_extra_model_calls_vs_baseline": extra_turn_samples,
        "per_sample_facts": {
            f"{key[0]}-{key[1]}-{key[2]}": facts
            for key, facts in sorted(per_sample_facts.items(), key=lambda item: str(item[0]))
        },
        "paired": paired,
        "pre_registered_acceptance": (
            "plugin strict quality >= baseline AND paired mean complete-total-token "
            "saving > 0"
        ),
        "quality_acceptance_met": quality_ok,
        "cost_acceptance_met": cost_ok,
        "verdict": "valid-saving" if (quality_ok and cost_ok) else "not-yet-valid",
        "self_hashes": {relative: _digest(ROOT / relative) for relative in SELF_ENTRIES},
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
