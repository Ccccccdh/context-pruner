"""Independent, zero-model audit for the v9 long-baseline batch.

v9 differs from v8 in one place: the guard's protected message-unit set is narrowed to the
units that carry a registered literal.  The audit therefore checks, from the frozen hashes
and the persisted rows only:

* freeze hashes, including this audit's own source file and the v9 mechanism/runner,
  evidence and gate modules;
* the v4-v8 registrations still verify, and the v9 protected set is recomputed from the
  public issue statement and asserted to be a **strict subset** of the statement units -
  with every registered literal still carried by a protected unit or a registered source
  range;
* the complete grid, quality, the request ledger, tool-group boundaries;
* **positive protection**: every registered literal present and its occurrence count never
  below the baseline arm's at the same boundary, and never below the observed payload's
  count across the filter boundary;
* **negative control**: the persisted row states that a repeated literal-carrying unit was
  kept, and the batch's guard counters show the failures are absent (a run that had
  disabled the guard would carry no ``registered_literal_lost`` accounting at all, which
  the offline gate's injected-fault case exercises);
* the boundary proposition recomputed from the batch's own baseline arm, including the
  regime (long/short) and the elidable-turn count;
* paired means over the full sample set, and the pre-registered acceptance verdict.
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
from experiments.runners import openai_agents_literal_registry_v5 as frozen_v5
from experiments.runners import openai_agents_literal_registry_v6 as frozen_v6
from experiments.runners import openai_agents_long_baseline_boundary_v9 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "pruner_v1", "native_summary")
SCHEMA = "openai_narrow_guard_boundary_v9"
MECHANISM = "narrow_guard_boundary_v9"
CONTRACTS = {
    "django_long_investigation": (
        ("read_django_count_entry", "read_django_aggregation", "read_django_count_call"),
        r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
        ("existing_annotations", "subquery", "referenced"),
    ),
}
REQUIRED_CONSTRAINTS = {
    "django_long_investigation": (
        "filter_references",
        "other_annotation_references",
        "ordering_references",
        "aggregation_decision",
    ),
}
SELF_ENTRIES = (
    "experiments/audits/audit_openai_agents_repo_diagnostic_v9.py",
    "experiments/runners/openai_agents_long_baseline_boundary_v9.py",
    "experiments/runners/run_openai_agents_repo_diagnostic_v9.py",
    "experiments/runners/openai_agents_evidence_v9.py",
    ".tooling/gate_openai_agents_long_baseline_v8.py",
)
RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "narrow_guard_schema",
    "narrow_guard_protected_unit_count",
    "narrow_guard_unprotected_statement_unit_count",
    "long_baseline_recent_turns_kept_verbatim",
    "long_baseline_elidable_turns_available",
    "long_baseline_plugin_can_elide_anything",
    "long_baseline_regime",
    "long_baseline_payload_identical_to_baseline",
    "long_baseline_shared_boundary_count",
    "long_baseline_boundary_proposition",
    "evidence_registered_literal_counts_final",
    "evidence_literal_carrier_units_present_by_boundary",
    "evidence_span_elision_records",
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


def audit_evidence_file(path: Path, row: dict, baseline: list[dict] | None) -> list[str]:
    issues: list[str] = []
    task = str(row.get("scenario"))
    key = f"{task}-{row.get('repeat')}-{row.get('method')}"
    records = _read_jsonl(path)
    if not records:
        return [f"{key}: empty evidence file"]
    literals = long_registry.literals_for(policy.LONG_TASK_ID)
    for record in records:
        if record.get("schema") != SCHEMA:
            issues.append(f"{key}: evidence schema mismatch")
            break
        if record.get("stage") not in ("filter_before", "filter_after", "model_input"):
            issues.append(f"{key}: evidence stage mismatch")
            break
        if record.get("task") != policy.SHORT_TASK_ID:
            issues.append(f"{key}: evidence task registration mismatch")
        if not HEX64.fullmatch(str(record.get("input_sha256"))):
            issues.append(f"{key}: evidence digest invalid")
        protected = int(record.get("narrow_guard_protected_unit_count", -1))
        full = int(record.get("narrow_guard_full_statement_unit_count", -1))
        if str(row.get("method")) == "pruner_v1":
            if protected < 0 or full < 0:
                issues.append(f"{key}: narrowed-guard counters missing")
            elif protected >= full:
                issues.append(
                    f"{key}: the protected set was not narrowed ({protected} of {full})"
                )
    model = [record for record in records if record["stage"] == "model_input"]
    before = [record for record in records if record["stage"] == "filter_before"]
    after = [record for record in records if record["stage"] == "filter_after"]
    if len(model) != int(row.get("model_calls", -1)):
        issues.append(f"{key}: model-call evidence mismatch")
    if len(before) != len(after) or len(before) != int(row.get("input_evidence_filter_calls", -1)):
        issues.append(f"{key}: filter-call evidence mismatch")

    # -- retention: literals never fall below the observed payload --------
    for previous, following in zip(before, after):
        if [group["group_id"] for group in previous["protected_groups"]] != [
            group["group_id"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: tool group boundaries moved")
        if [group["tool_output_count"] for group in previous["protected_groups"]] != [
            group["tool_output_count"] for group in following["protected_groups"]
        ]:
            issues.append(f"{key}: a protected call/output pair was split")
        for label in literals:
            if int(following["registered_literal_counts"].get(label, -1)) < int(
                previous["registered_literal_counts"].get(label, -1)
            ):
                issues.append(f"{key}: literal {label} lost across the filter boundary")
        elided = int(following.get("outputs_elided", 0)) - int(
            previous.get("outputs_elided", 0)
        )
        if elided == 0 and [
            group["tool_output_sha256"] for group in previous["protected_groups"]
        ] != [group["tool_output_sha256"] for group in following["protected_groups"]]:
            issues.append(f"{key}: tool output changed without an elision note")
        if elided > 0 and int(following["input_bytes"]) >= int(previous["input_bytes"]):
            issues.append(f"{key}: an elision did not shrink the payload")

    # -- positive protection: never below the baseline arm ----------------
    if baseline is not None and str(row.get("method")) == "pruner_v1":
        baseline_model = [record for record in baseline if record["stage"] == "model_input"]
        for index in range(min(len(model), len(baseline_model))):
            for label in literals:
                plugin_count = int(model[index]["registered_literal_counts"].get(label, -1))
                base_count = int(
                    baseline_model[index]["registered_literal_counts"].get(label, -1)
                )
                if plugin_count < base_count:
                    issues.append(
                        f"{key}: literal {label} count {plugin_count} at boundary {index} is "
                        f"below the baseline arm's {base_count}"
                    )
        if baseline_model:
            for label in literals:
                plugin_count = int(
                    model[-1]["registered_literal_counts"].get(label, -1)
                )
                base_count = int(
                    baseline_model[-1]["registered_literal_counts"].get(label, -1)
                )
                if plugin_count < base_count:
                    issues.append(
                        f"{key}: literal {label} count {plugin_count} in the final input is "
                        f"below the baseline arm's {base_count}"
                    )
        for index, record in enumerate(model):
            for label in REQUIRED_CONSTRAINTS.get(task, ()):
                if record["registered_literals_present"].get(label):
                    continue
                carries = any(
                    str(entry.get("tool") or "") in
                    {
                        source.tool
                        for source in frozen_v5.SOURCES.get(policy.SHORT_TASK_ID, ())
                        if source.literal_labels
                    }
                    for entry in record.get("output_manifest") or []
                )
                if label == "aggregation_decision" and not carries:
                    continue
                issues.append(f"{key}: required literal {label} absent from boundary {index}")
        carriers = row.get("evidence_literal_carrier_units_present_by_boundary") or []
        if len(carriers) != len(model):
            issues.append(f"{key}: carrier-unit ledger does not cover every boundary")

    # -- span records ------------------------------------------------------
    for entry in row.get("evidence_span_elision_records") or []:
        if not entry.get("pointer_completeness"):
            issues.append(f"{key}: an elision did not partition its registered range")
        if not entry.get("note_states_pointer"):
            issues.append(f"{key}: an elision note states no pointer")
        registered = set(
            long_registry.span_for_tool(task, entry["tool"]).literal_lines
        )
        omitted: set[int] = set()
        for interval in entry["omitted_intervals"]:
            omitted.update(range(int(interval[0]), int(interval[1]) + 1))
        if registered & omitted:
            issues.append(f"{key}: an omitted line carries a registered literal")

    # -- guard ledger ------------------------------------------------------
    per_call = [int(record.get("task_anchor_restore_failures", -1)) for record in model]
    if per_call != [int(value) for value in row.get("restore_failures_by_model_call", [])]:
        issues.append(f"{key}: restore-failure ledger mismatch")
    if not bool(row.get("restore_failure_is_whole_prefix_fallback")):
        issues.append(f"{key}: restore-failure fallback scope not stated")
    if int(row.get("task_restore_fallbacks", -1)) != int(
        row.get("task_anchor_restore_failures", -2)
    ):
        issues.append(f"{key}: fallback count does not equal the restore-failure count")
    budget = [int(value) for value in row.get("budget_fallbacks_by_model_call", [])]
    if int(row.get("budget_fallbacks", -1)) != (budget[-1] if budget else 0):
        issues.append(f"{key}: budget-fallback ledger mismatch")
    if re.search(r"sk-[A-Za-z0-9]{24,}", path.read_text(encoding="utf-8")):
        issues.append(f"{key}: credential-like text in evidence")
    return issues


def audit(batch: Path, freeze_path: Path, batch_name: str | None = None) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(batch / "samples.jsonl")
    issues: list[str] = []
    expected_batch = str(batch_name or freeze["batch"])

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
    ):
        if manifest.get(field) != wanted:
            issues.append(f"manifest mismatch: {field}")
    if manifest.get("data_class") != "public_source_diagnostic":
        issues.append("manifest payload class is not the honest public-source class")
    if manifest.get("mechanism", {}).get("name") != MECHANISM:
        issues.append("manifest does not declare the v9 mechanism")
    if int(manifest.get("mechanism", {}).get("recent_turns_kept_verbatim", -1)) != int(
        freeze["recent_turns_kept_verbatim"]
    ):
        issues.append("manifest recency constant mismatch")
    if manifest.get("persisted_retention_fields") != freeze.get("persisted_retention_fields"):
        issues.append("persisted retention field list mismatch")

    # -- registrations -----------------------------------------------------
    for problems, label in (
        (frozen_v6.all_problems([policy.SHORT_TASK_ID]), "frozen v6 registry"),
        (frozen_v6.base.all_problems([policy.SHORT_TASK_ID]), "frozen v5 registry"),
        (long_registry.verify(policy.LONG_TASK_ID), "v8 long-task registration"),
    ):
        if problems:
            issues.append(f"{label} does not verify: " + "; ".join(problems))
    statement = freeze.get("registered_statement", "")
    if not statement:
        issues.append("freeze does not record the registered statement")
    else:
        protectable = policy.protectable_units(policy.LONG_TASK_ID, statement)
        full_units = [
            line.strip()
            for line in statement.splitlines()
            if line.strip() and len(line.strip()) >= 20
        ]
        if not protectable:
            issues.append("the narrowed protected set is empty")
        if len(protectable) >= len(full_units):
            issues.append(
                f"the narrowed protected set is not a strict subset "
                f"({len(protectable)} of {len(full_units)})"
            )
        for unit in protectable:
            if not long_registry.labels_in_text(policy.LONG_TASK_ID, unit):
                issues.append("a protected unit carries no registered literal")
        protected_text = " ".join(protectable).lower()
        for label, phrases in long_registry.literals_for(policy.LONG_TASK_ID).items():
            if label == "aggregation_decision":
                continue
            if not any(str(phrase).lower() in protected_text for phrase in phrases):
                issues.append(
                    f"registered literal {label} is no longer carried by a protected unit"
                )
        frozen_protectable = freeze.get("narrow_guard_protectable_units")
        if frozen_protectable is not None and frozen_protectable != protectable:
            issues.append("narrowed protected set does not match the freeze")

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

    baseline_evidence: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        if str(row.get("method")) != "none":
            continue
        relative = row.get("input_evidence_file")
        if relative and (batch / relative).is_file():
            baseline_evidence[(str(row["scenario"]), int(row["repeat"]))] = _read_jsonl(
                batch / relative
            )
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
        complete = (
            inputs + outputs + int(row.get("summary_input_tokens", 0))
            + int(row.get("summary_output_tokens", 0))
        )
        if complete != row.get("all_arm_total_tokens"):
            issues.append(f"complete total mismatch: {key}")
        calls = int(row.get("api_request_attempts", 0))
        attempts += calls
        if calls < int(row.get("model_calls", 0)) + int(row.get("summary_calls", 0)):
            issues.append(f"request ledger mismatch: {key}")
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
        issues.extend(audit_evidence_file(path, row, anchor))
    if attempts > freeze["max_api_requests"]:
        issues.append("global request cap exceeded")
    if attempts != report.get("request_budget", {}).get("recorded_attempts_all_rows"):
        issues.append("global request ledger mismatch")

    # -- boundary + paired means -------------------------------------------
    keyed = {(row.get("scenario"), row.get("repeat"), row.get("method")): row for row in rows}
    boundary: dict[str, dict] = {}
    for scenario in freeze["scenarios"]:
        for repeat in range(freeze["repeats"]):
            baseline = keyed.get((scenario, repeat, "none"))
            plugin = keyed.get((scenario, repeat, "pruner_v1"))
            if baseline is None or plugin is None:
                continue
            proposition = policy.long_task_boundary(
                int(baseline.get("model_calls", 0)),
                int(baseline.get("actual_input_tokens", 0)),
            )
            boundary[f"{scenario}-{repeat}"] = {
                **proposition,
                "plugin_elided_sources": int(
                    plugin.get("long_baseline_elided_sources", 0)
                ),
                "plugin_input_tokens": int(plugin.get("actual_input_tokens", 0)),
                "payload_identical_to_baseline": bool(
                    plugin.get("long_baseline_payload_identical_to_baseline")
                ),
            }
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
                int(row.get("long_baseline_elided_sources", 0)) for row in arm_rows
            ),
            "error_types": sorted(
                {str(row.get("error_type")) for row in arm_rows if row.get("error_type")}
            ),
        }
    plugin_paired = paired.get("pruner_v1", {})
    quality_ok = arm_means.get("pruner_v1", {}).get("successes", 0) >= arm_means.get(
        "none", {}
    ).get("successes", 0)
    cost_ok = bool(
        plugin_paired.get("complete_total_savings_mean") is not None
        and plugin_paired["complete_total_savings_mean"] > 0
    )
    return {
        "complete": not issues,
        "issues": issues,
        "batch": batch.name,
        "expected_batch": expected_batch,
        "freeze": str(freeze_path),
        "mechanism": MECHANISM,
        "samples": len(rows),
        "attempts": attempts,
        "evidence_checked": evidence_checked,
        "arm_means": arm_means,
        "successes": {arm: arm_means.get(arm, {}).get("successes", 0) for arm in METHODS},
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
        "plugin_elided_sources_total": sum(
            int(row.get("long_baseline_elided_sources", 0))
            for row in rows
            if row.get("method") == "pruner_v1"
        ),
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
