"""Independent, zero-model audit for the v7 applicability-boundary batch.

Checks, from the frozen hashes and the persisted rows only (no re-run, no model):

* source / public-input SHA256 against the v7 freeze, **including the audit's own
  source file**, the v7 runner/mechanism/evidence modules and the reused v6 gate
  helper, so everything that judges the batch is inside the frozen set;
* the registries: the frozen v5 slice and the v6 span table are re-verified against
  their own public ranges, and both fingerprints are compared with the manifest;
* the frozen recency policy: ``RECENT_TURNS_KEPT`` in the freeze, the manifest and the
  persisted rows must agree, and every sample must report that policy;
* manifest identity and the honest payload class;
* the complete sample grid, including every failure and cap-exhausted row;
* per-sample answers, tool sequence, structure safety and the strict contract;
* the request ledger, the global cap and the report total;
* protected Responses tool-group boundaries in the evidence files;
* **the boundary claim itself**: every registered literal present at the model
  boundary, the plugin arm's payload never larger than the baseline's, and - when the
  mechanism did not elide - the plugin's recorded payload hashes byte-identical to the
  baseline arm's at every shared boundary, with the projectable saving therefore 0;
* the guard and restore ledgers: ``task_anchor_restore_failures``, ``task_restore_fallbacks``
  and ``budget_fallbacks`` persisted per sample and per model call, consistent with the
  evidence and the report, with the whole-payload fallback scope stated;
* the boundary proposition recomputed from the batch's own baseline arm, compared with
  the rows and the report;
* paired means over the full sample set for real input tokens and for the *complete*
  total including every auxiliary summary request and failed attempt, plus the
  pre-registered acceptance verdict.

The contract rules are restated here rather than imported from the runner, so a mistake
in the runner's own scoring cannot hide itself in the audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from experiments.audits import amendment_hashes
from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import openai_agents_recency_boundary_v7 as policy

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "pruner_v1", "native_summary")
SCHEMA = "openai_recency_boundary_v7"
MECHANISM = "recency_boundary_v7"
NOTE_PREFIX = "[pointer-retention v6]"
EVIDENCE_STAGES = {"filter_before", "filter_after", "model_input"}
CONTRACTS = {
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
SELF_ENTRIES = (
    "experiments/audits/audit_openai_agents_repo_diagnostic_v7.py",
    "experiments/runners/openai_agents_recency_boundary_v7.py",
    "experiments/runners/run_openai_agents_repo_diagnostic_v7.py",
    "experiments/runners/openai_agents_evidence_v7.py",
    ".tooling/gate_openai_agents_pointer_retention_v6.py",
)
RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "recency_boundary_recent_turns_kept_verbatim",
    "recency_boundary_elidable_indices",
    "recency_boundary_protected_turn_items",
    "recency_boundary_elided_sources",
    "recency_boundary_baseline_payload_sha256_by_call",
    "recency_boundary_plugin_payload_sha256_by_call",
    "recency_boundary_payload_identical_to_baseline",
    "recency_boundary_shared_boundary_count",
    "recency_boundary_boundary_proposition",
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


def _carriers(task: str) -> dict[str, tuple[str, ...]]:
    return {
        source.tool: tuple(source.literal_labels)
        for source in registry.base.SOURCES.get(task, ())
        if source.literal_labels
    }


def audit_evidence_file(path: Path, row: dict) -> tuple[list[str], dict]:
    """Validate one sample's evidence and return ``(issues, facts)``."""
    issues: list[str] = []
    task = str(row.get("scenario"))
    key = f"{task}-{row.get('repeat')}-{row.get('method')}"
    records = _read_jsonl(path)
    if not records:
        return [f"{key}: empty evidence file"], {}
    literals = registry.base.literals_for(task)
    expected_v6 = registry.registry_fingerprint(task)
    expected_v5 = registry.base_registry_fingerprint(task)
    for record in records:
        if record.get("schema") != SCHEMA:
            issues.append(f"{key}: evidence schema mismatch")
            break
        if record.get("stage") not in EVIDENCE_STAGES or record.get("task") != task:
            issues.append(f"{key}: evidence stage/task mismatch")
            break
        if record.get("registry_fingerprint") != expected_v6:
            issues.append(f"{key}: evidence v6 registry fingerprint mismatch")
        if record.get("base_registry_fingerprint") != expected_v5:
            issues.append(f"{key}: evidence v5 registry fingerprint mismatch")
        if not HEX64.fullmatch(str(record.get("input_sha256"))):
            issues.append(f"{key}: evidence digest invalid")
        frozen_policy = record.get("recency_policy") or {}
        if int(frozen_policy.get("recent_turns_kept_verbatim", -1)) != int(
            row.get("recency_boundary_recent_turns_kept_verbatim", -2)
        ):
            issues.append(f"{key}: recency policy constant mismatch in the evidence")
        if not isinstance(record.get("output_manifest"), list):
            issues.append(f"{key}: evidence output manifest missing")
    model = [record for record in records if record["stage"] == "model_input"]
    before = [record for record in records if record["stage"] == "filter_before"]
    after = [record for record in records if record["stage"] == "filter_after"]
    if len(model) != int(row.get("model_calls", -1)):
        issues.append(f"{key}: model-call evidence mismatch")
    if len(model) != int(row.get("input_evidence_model_calls", -1)):
        issues.append(f"{key}: recorded model-call evidence mismatch")
    if len(before) != len(after) or len(before) != int(row.get("input_evidence_filter_calls", -1)):
        issues.append(f"{key}: filter-call evidence mismatch")
    if [record["index"] for record in model] != list(range(len(model))):
        issues.append(f"{key}: model indices mismatch")
    if row.get("method") == "none" and before:
        issues.append(f"{key}: baseline unexpectedly filtered")

    # -- retained evidence: literals and the newest turn -------------------
    carriers = _carriers(task)
    for record in model:
        manifest = record.get("output_manifest") or []
        carries_source = any(str(entry.get("tool") or "") in carriers for entry in manifest)
        for label in REQUIRED_CONSTRAINTS.get(task, ()):
            if record["registered_literals_present"].get(label):
                continue
            # ``existing_annotations`` reaches the model only through a registered
            # source-code tool output, so it is required only from the first boundary
            # that carries one - exactly as the frozen v5/v6 audits state it.
            if label == "aggregation_decision" and not carries_source:
                continue
            issues.append(f"{key}: required literal {label} absent from boundary {record['index']}")
        for entry in manifest:
            if not entry.get("output_elided"):
                continue
            if not entry.get("note_has_source_pointer"):
                issues.append(f"{key}: an elision note carries no source pointer")
            if not entry.get("pointer_completeness"):
                issues.append(f"{key}: an elision did not partition its registered range")
            if str(entry.get("tool") or "") in carriers and not entry.get(
                "span_retained_literal_lines"
            ):
                issues.append(
                    f"{key}: a constraint carrier lost its registered literal lines"
                )
        if str(row.get("method")) == "pruner_v1" and not record.get(
            "most_recent_tool_group_kept_in_full", False
        ):
            issues.append(f"{key}: the most recent tool group was not kept in full")
    for previous, following in zip(before, after):
        if [group["group_id"] for group in previous["protected_groups"]] != [
            group["group_id"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: tool group boundaries moved")
        if [group["tool_output_count"] for group in previous["protected_groups"]] != [
            group["tool_output_count"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: a protected call/output pair was split")
        elided = int(following.get("outputs_elided", 0)) - int(
            previous.get("outputs_elided", 0)
        )
        if elided == 0 and [
            group["tool_output_sha256"] for group in previous["protected_groups"]
        ] != [group["tool_output_sha256"] for group in following["protected_groups"]]:
            issues.append(f"{key}: tool output changed without an elision note")
        if elided > 0 and int(following["input_bytes"]) >= int(previous["input_bytes"]):
            issues.append(f"{key}: an elision did not shrink the payload")
        for label in literals:
            if int(following["registered_literal_counts"].get(label, -1)) < int(
                previous["registered_literal_counts"].get(label, -1)
            ):
                issues.append(f"{key}: literal {label} lost across the filter boundary")

    # -- the recency policy, per boundary ----------------------------------
    for record in before:
        elidable = [int(value) for value in record.get("recency_elidable_indices", [])]
        item_count = int(record.get("item_count", 0))
        if any(value < 0 or value >= item_count for value in elidable):
            issues.append(f"{key}: an elidable index falls outside the payload")
        if len(set(elidable)) != len(elidable):
            issues.append(f"{key}: duplicate elidable indices")
        if int(record.get("recency_turn_count", 0)) and not int(
            record.get("recency_protected_turn_items", 0)
        ):
            issues.append(f"{key}: a payload with tool turns protected nothing")
    protected_outputs = int(row.get("recency_boundary_protected_outputs", 0))
    elided_sources = int(row.get("recency_boundary_elided_sources", 0))
    if elided_sources and not protected_outputs:
        issues.append(f"{key}: sources were elided while nothing was protected")

    # -- the boundary claim -------------------------------------------------
    baseline_hashes = list(row.get("recency_boundary_baseline_payload_sha256_by_call") or [])
    plugin_hashes = list(row.get("recency_boundary_plugin_payload_sha256_by_call") or [])
    shared = int(row.get("recency_boundary_shared_boundary_count", 0))
    identical = row.get("recency_boundary_payload_identical_to_baseline")
    if str(row.get("method")) == "pruner_v1":
        shared_expected = min(len(baseline_hashes), len(plugin_hashes))
        if shared != shared_expected:
            issues.append(
                f"{key}: shared boundary count {shared} does not match the recorded hashes "
                f"({shared_expected})"
            )
        identical_expected = shared_expected > 0 and (
            baseline_hashes[:shared_expected] == plugin_hashes[:shared_expected]
        )
        if bool(identical) != bool(identical_expected):
            issues.append(f"{key}: payload-identity flag disagrees with the recorded hashes")
        if elided_sources == 0 and not identical:
            issues.append(
                f"{key}: nothing was elided but the payload is not identical to the baseline"
            )
        if identical and float(row.get("recency_boundary_projectable_saving_rate") or 0.0) != 0.0:
            issues.append(f"{key}: an identical payload reported a non-zero projectable saving")
        baseline_tokens = int(row.get("recency_boundary_baseline_input_tokens", 0))
        plugin_tokens = int(row.get("actual_input_tokens", 0))
        if baseline_tokens and plugin_tokens > baseline_tokens and elided_sources == 0:
            issues.append(
                f"{key}: the plugin sent more than the baseline while eliding nothing"
            )
        proposition = row.get("recency_boundary_boundary_proposition") or {}
        recomputed = policy.boundary_proposition(
            int(row.get("recency_boundary_baseline_model_calls", 0)), baseline_tokens
        )
        for field in (
            "elidable_turns_available_to_the_plugin",
            "plugin_can_elide_anything",
            "plugin_wins_possible",
        ):
            if proposition.get(field) != recomputed[field]:
                issues.append(f"{key}: boundary proposition field {field} mismatch")

    # -- guard and restore ledgers -----------------------------------------
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
    budget_sequence = [int(value) for value in row.get("budget_fallbacks_by_model_call", [])]
    if int(row.get("budget_fallbacks", -1)) != (budget_sequence[-1] if budget_sequence else 0):
        issues.append(f"{key}: budget-fallback ledger mismatch")
    if re.search(r"sk-[A-Za-z0-9]{24,}", path.read_text(encoding="utf-8")):
        issues.append(f"{key}: credential-like text in evidence")
    return issues, {
        "model_calls": len(model),
        "elided_sources": elided_sources,
        "protected_outputs": protected_outputs,
        "payload_identical_to_baseline": bool(identical),
        "shared_boundaries": shared,
        "actual_input_tokens": int(row.get("actual_input_tokens", 0)),
        "complete_total_tokens": int(row.get("all_arm_total_tokens", 0)),
        "success": bool(row.get("success")),
    }


def audit(batch: Path, freeze_path: Path, batch_name: str | None = None) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(batch / "samples.jsonl")
    issues: list[str] = []
    expected_batch = str(batch_name or freeze["batch"])

    # -- freeze ------------------------------------------------------------
    if batch.name != expected_batch or manifest.get("experiment_id") != expected_batch:
        issues.append("batch ID mismatch")
    # A frozen path counts as intact when it still hashes to the frozen value, or when its
    # current content is exactly what the amendment file records for it (freeze files are
    # never edited, so post-freeze changes are declared there with a reason). Anything else
    # is a mismatch, and a missing amendment file suppresses nothing.
    issues.extend(amendment_hashes.freeze_hash_issues(freeze, _digest))
    for relative in SELF_ENTRIES:
        if relative not in freeze["source_sha256"]:
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
    if manifest.get("data_class") != "public_source_diagnostic":
        issues.append("manifest payload class is not the honest public-source class")
    if manifest.get("mechanism", {}).get("name") != MECHANISM:
        issues.append("manifest does not declare the v7 recency-boundary mechanism")
    if int(manifest.get("mechanism", {}).get("recent_turns_kept_verbatim", -1)) != int(
        freeze["recent_turns_kept_verbatim"]
    ):
        issues.append("manifest recency constant mismatch")
    if int(policy.RECENT_TURNS_KEPT) != int(freeze["recent_turns_kept_verbatim"]):
        issues.append("the running mechanism's recency constant is not the frozen one")
    if manifest.get("persisted_retention_fields") != freeze.get("persisted_retention_fields"):
        issues.append("persisted retention field list mismatch")
    if int(manifest.get("filter_hard_bytes", -1)) != int(freeze["filter_hard_bytes"]):
        issues.append("filter byte ceiling mismatch")

    # -- registries --------------------------------------------------------
    for problems, label in (
        (registry.all_problems(freeze["scenarios"]), "span registry"),
        (registry.base.all_problems(freeze["scenarios"]), "v5 registry"),
    ):
        if problems:
            issues.append(f"{label} does not verify: " + "; ".join(problems))
    for scenario in freeze["scenarios"]:
        if registry.registry_fingerprint(scenario) != freeze["registry_fingerprints"].get(scenario):
            issues.append(f"v6 registry fingerprint mismatch: {scenario}")
        if registry.base_registry_fingerprint(scenario) != freeze["base_registry_fingerprints"].get(
            scenario
        ):
            issues.append(f"v5 registry fingerprint mismatch: {scenario}")
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
            for span in registry.spans(scenario)
        ]
        if freeze.get("registered_spans", {}).get(scenario) != recomputed:
            issues.append(f"registered span table mismatch: {scenario}")

    # -- grid --------------------------------------------------------------
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

    # -- per sample --------------------------------------------------------
    attempts = 0
    evidence_checked = 0
    per_sample_facts: dict[tuple, dict] = {}
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
        sample_issues, facts = audit_evidence_file(path, row)
        issues.extend(sample_issues)
        per_sample_facts[key] = facts
    if attempts > freeze["max_api_requests"]:
        issues.append("global request cap exceeded")
    if attempts != report.get("request_budget", {}).get("recorded_attempts_all_rows"):
        issues.append("global request ledger mismatch")

    # -- boundary proposition, recomputed from the batch's own baseline ----
    keyed = {(row.get("scenario"), row.get("repeat"), row.get("method")): row for row in rows}
    boundary: dict[str, dict] = {}
    for scenario in freeze["scenarios"]:
        for repeat in range(freeze["repeats"]):
            baseline = keyed.get((scenario, repeat, "none"))
            plugin = keyed.get((scenario, repeat, "pruner_v1"))
            if baseline is None or plugin is None:
                continue
            proposition = policy.boundary_proposition(
                int(baseline.get("model_calls", 0)),
                int(baseline.get("actual_input_tokens", 0)),
            )
            recorded = report.get("recency_boundary", {}).get(f"{scenario}-{repeat}")
            if recorded is not None and recorded != proposition:
                issues.append(f"boundary proposition mismatch in the report: {scenario}-{repeat}")
            identical = bool(plugin.get("recency_boundary_payload_identical_to_baseline"))
            boundary[f"{scenario}-{repeat}"] = {
                **proposition,
                "plugin_elided_sources": int(plugin.get("recency_boundary_elided_sources", 0)),
                "payload_identical_to_baseline": identical,
                "plugin_input_tokens": int(plugin.get("actual_input_tokens", 0)),
                "projectable_saving_rate": 0.0 if identical else None,
            }

    # -- paired means over the full sample set -----------------------------
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
            "actual_input_savings_mean": _mean(input_savings),
            "complete_total_savings_mean": mean,
            "complete_total_savings_positive": sum(value > 0 for value in total_savings),
            "success_delta_mean": _mean(success_deltas),
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

    arm_means: dict[str, dict] = {}
    for arm in METHODS:
        arm_rows = [row for row in rows if row.get("method") == arm]
        if not arm_rows:
            continue
        arm_means[arm] = {
            "n": len(arm_rows),
            "successes": sum(1 for row in arm_rows if row.get("success")),
            "mean_actual_input_tokens": _mean(
                [int(row.get("actual_input_tokens", 0)) for row in arm_rows]
            ),
            "mean_complete_total_tokens": _mean(
                [int(row.get("all_arm_total_tokens", 0)) for row in arm_rows]
            ),
            "mean_model_calls": _mean([int(row.get("model_calls", 0)) for row in arm_rows]),
            "elided_sources": sum(
                int(row.get("recency_boundary_elided_sources", 0)) for row in arm_rows
            ),
            "error_types": sorted(
                {str(row.get("error_type")) for row in arm_rows if row.get("error_type")}
            ),
        }
    plugin_facts = [
        facts for key, facts in per_sample_facts.items() if str(key[2]) == "pruner_v1"
    ]
    plugin_paired = paired.get("pruner_v1", {})
    quality_ok = arm_means.get("pruner_v1", {}).get("successes", 0) >= arm_means.get(
        "none", {}
    ).get("successes", 0)
    cost_ok = bool(
        plugin_paired.get("complete_total_savings_mean") is not None
        and plugin_paired["complete_total_savings_mean"] > 0
    )
    projectable = [
        value
        for value in (
            entry["projectable_saving_rate"] for entry in boundary.values()
        )
        if value is not None
    ]
    return {
        "complete": not issues,
        "issues": issues,
        "batch": batch.name,
        "expected_batch": expected_batch,
        "freeze": str(freeze_path),
        "mechanism": MECHANISM,
        "registry_schema": registry.REGISTRY_SCHEMA,
        "base_registry_schema": registry.base.REGISTRY_SCHEMA,
        "recent_turns_kept_verbatim": int(freeze["recent_turns_kept_verbatim"]),
        "samples": len(rows),
        "attempts": attempts,
        "evidence_checked": evidence_checked,
        "arm_means": arm_means,
        "successes": {
            arm: arm_means.get(arm, {}).get("successes", 0) for arm in METHODS
        },
        "failures": {
            arm: sum(1 for row in rows if row.get("method") == arm and row.get("error_type"))
            for arm in METHODS
        },
        "task_anchor_restore_failures": {
            arm: sum(
                int(row.get("task_anchor_restore_failures", 0))
                for row in rows
                if row.get("method") == arm
            )
            for arm in METHODS
        },
        "budget_fallbacks": {
            arm: sum(
                int(row.get("budget_fallbacks", 0)) for row in rows if row.get("method") == arm
            )
            for arm in METHODS
        },
        "plugin_elided_sources_total": sum(
            int(facts.get("elided_sources", 0)) for facts in plugin_facts
        ),
        "plugin_samples_with_payload_identical_to_baseline": sum(
            1 for facts in plugin_facts if facts.get("payload_identical_to_baseline")
        ),
        "projectable_saving_rates": projectable or [0.0],
        "boundary": boundary,
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
    parser.add_argument("--batch-name", default=None)
    args = parser.parse_args()
    result = audit(args.batch, args.freeze, args.batch_name)
    (args.batch / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
