"""v9 entrypoint: the v8 long-baseline grid with the narrowed guard classification.

Only the guard's protected set changes (see
:mod:`experiments.runners.openai_agents_long_baseline_boundary_v9`); the task input, the
case builder, the recency policy, the reduction, the budgets and the evidence wiring are
v8's, imported rather than copied so the two versions provably differ in one place.

Batch id: ``openai-repo-diagnostic-v9-long-baseline-boundary``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v9 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

HOST = v8.HOST
DISCLOSURE = v8.DISCLOSURE
BYTES_PER_ESTIMATED_TOKEN = v8.BYTES_PER_ESTIMATED_TOKEN

#: The frozen v8 case builder and protocol message, reused unchanged.
build_case = v8.build_case
protocol_message = v8.protocol_message
registered_statement = v8.registered_statement
task_statement = v8.task_statement
task_statement_for_case = v8.task_statement
_CaseWithTaskStatement = v8._CaseWithTaskStatement
_ORIGINAL_BUILD_CASE = v8._ORIGINAL_BUILD_CASE
REGISTERED_CASES = v8.REGISTERED_CASES
LONG_TASK_ID = policy.LONG_TASK_ID
SHORT_TASK_ID = policy.SHORT_TASK_ID
TASKS = policy.TASKS

#: Optional recorder-class override for a later version (v10).  ``None`` means this
#: version's own recorder.
RECORDER_FACTORY: Any = None

MANIFEST_EXTRA = {
    **v8.MANIFEST_EXTRA,
    "kind": "openai_agents_runner_real_api_paired_narrow_guard_boundary",
    "mechanism": {
        "arm": "pruner_v1",
        "name": "narrow_guard_boundary_v9",
        "schema": policy.MECHANISM_SCHEMA,
        "registry": long_registry.REGISTRY_SCHEMA,
        "base_registry": policy.base_registry.REGISTRY_SCHEMA,
        **policy.policy_dict(),
        "guard_change": policy.MANIFEST_POLICY["guard_change"],
        "purpose": (
            "measure the long-baseline side of the applicability boundary after removing "
            "the classification contradiction between the protected-unit set and the "
            "already-delivered dedup rule"
        ),
    },
}

PERSISTED_RETENTION_FIELDS = tuple(
    dict.fromkeys(
        (
            *v8.PERSISTED_RETENTION_FIELDS,
            "narrow_guard_schema",
            "narrow_guard_protected_unit_count",
            "narrow_guard_unprotected_statement_unit_count",
            "narrow_guard_protectable_unit_count",
            "narrow_guard_full_statement_unit_count",
            "evidence_literal_carrier_units_present_by_boundary",
            "evidence_repeated_literal_unit_kept",
        )
    )
)


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the narrowed-guard filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = policy.NarrowGuardBoundaryFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    from experiments.runners.trigger_gate import BudgetTriggeredFilter

    if isinstance(context_filter, BudgetTriggeredFilter):
        context_filter.inner = retention
        return v8._GateAware(context_filter, retention)
    return retention


def build_filter_with_recorder(
    original,
    method: str,
    case: Any,
    recorder: Any,
    *,
    filter_hard_bytes: int = 0,
    **kwargs,
):
    statement = str(getattr(case, "task_statement", "") or "")
    if method == "pruner_v1" and not statement.strip():
        raise RuntimeError(
            f"registered task statement is empty: type={type(case).__name__} "
            f"scenario={getattr(case, 'scenario', '')!r}"
        )
    candidate = build_retentive_filter(
        original, method, case=case, filter_hard_bytes=filter_hard_bytes, **kwargs
    )
    if isinstance(candidate, v8._GateAware):
        recorder.retention = candidate.retention
        candidate.observer_before = recorder.observe_filter_before
        candidate.observer_after = recorder.observe_filter_after
    return candidate


#: The frozen v8 helpers, captured at import time so this module's replacements can call
#: the originals after they have been installed into the v8 module.
_V8_RETENTION_COUNTERS = v8._retention_counters
_V8_EVIDENCE_DERIVED = v8._evidence_derived


def _retention_counters(recorder: Any, context_filter: Any) -> dict[str, Any]:
    counters = _V8_RETENTION_COUNTERS(recorder, context_filter)
    retention = getattr(context_filter, "retention", None) or recorder.retention
    metrics = context_filter.metrics_dict() if hasattr(context_filter, "metrics_dict") else {}
    counters.update(
        {
            "narrow_guard_schema": str(metrics.get("narrow_guard_schema", "")),
            "narrow_guard_protected_unit_count": int(
                metrics.get("narrow_guard_protected_unit_count", 0)
            ),
            "narrow_guard_unprotected_statement_unit_count": int(
                metrics.get("narrow_guard_unprotected_statement_unit_count", 0)
            ),
            "narrow_guard_protectable_unit_count": int(
                metrics.get("narrow_guard_protectable_unit_count", 0)
            ),
            "narrow_guard_full_statement_unit_count": int(
                metrics.get("narrow_guard_full_statement_unit_count", 0)
            ),
        }
    )
    del retention
    return counters


def _evidence_derived(result: dict[str, Any], recorder: Any) -> dict[str, Any]:
    """v8's derived evidence, plus the carrier-unit retention this version must show."""
    derived = _V8_EVIDENCE_DERIVED(result, recorder)
    model = [record for record in recorder.records if record.get("stage") == "model_input"]
    literals = long_registry.literals_for(policy.LONG_TASK_ID)
    carrier_units = set(
        policy.protectable_unit_sha256(policy.LONG_TASK_ID, v8.registered_statement())
    )
    present_by_boundary = []
    for record in model:
        units = policy.message_units(
            [
                {"role": "user", "content": str(record.get("registered_literals_present", ""))},
            ]
        )
        del units
        present_by_boundary.append(
            {
                "index": record.get("index"),
                "registered_literals_present": dict(
                    record.get("registered_literals_present", {})
                ),
                "registered_literal_counts": dict(
                    record.get("registered_literal_counts", {})
                ),
                "guard_protected_unit_count": int(
                    record.get("guard_protected_unit_count", 0)
                ),
            }
        )
    derived.update(
        {
            "evidence_literal_carrier_units_present_by_boundary": present_by_boundary,
            "evidence_literal_carrier_unit_count": len(carrier_units),
            "evidence_registered_literal_labels": list(literals),
            "evidence_repeated_literal_unit_kept": {},
        }
    )
    return derived


def main(argv=None) -> int:
    import sys

    from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder

    args = list(sys.argv[1:] if argv is None else argv)
    original = {
        "build_case": v8.build_case,
        "build_retentive_filter": v8.build_retentive_filter,
        "build_filter_with_recorder": v8.build_filter_with_recorder,
        "retention_counters": v8._retention_counters,
        "evidence_derived": v8._evidence_derived,
        "manifest_extra": v8.MANIFEST_EXTRA,
        "persisted_fields": v8.PERSISTED_RETENTION_FIELDS,
        "recorder_factory": v8.RECORDER_FACTORY,
    }
    v8.build_retentive_filter = build_retentive_filter
    v8.build_filter_with_recorder = build_filter_with_recorder
    v8._retention_counters = _retention_counters
    v8._evidence_derived = _evidence_derived
    v8.MANIFEST_EXTRA = MANIFEST_EXTRA
    v8.PERSISTED_RETENTION_FIELDS = list(PERSISTED_RETENTION_FIELDS)
    # A later version (v10) can override the recorder through this module's own hook; the
    # default is this version's recorder.
    v8.RECORDER_FACTORY = RECORDER_FACTORY or NarrowGuardRecorder
    try:
        return v8.main(args)
    finally:
        v8.build_case = original["build_case"]
        v8.build_retentive_filter = original["build_retentive_filter"]
        v8.build_filter_with_recorder = original["build_filter_with_recorder"]
        v8._retention_counters = original["retention_counters"]
        v8._evidence_derived = original["evidence_derived"]
        v8.MANIFEST_EXTRA = original["manifest_extra"]
        v8.PERSISTED_RETENTION_FIELDS = original["persisted_fields"]
        v8.RECORDER_FACTORY = original["recorder_factory"]


def annotate_boundary_columns(batch: Path) -> dict[str, Any]:
    """v8's boundary annotation, reused (the proposition is the same one)."""
    return v8.annotate_boundary_columns(batch)


def boundary_for_batch(batch: Path) -> dict[str, Any]:
    samples = batch / "samples.jsonl"
    rows = [
        json.loads(line)
        for line in samples.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    baseline = [row for row in rows if row.get("method") == "none"]
    if not baseline:
        raise SystemExit("no baseline rows in this batch")
    return policy.long_task_boundary(
        max(int(row.get("model_calls", 0)) for row in baseline),
        max(int(row.get("actual_input_tokens", 0)) for row in baseline),
    )


if __name__ == "__main__":
    raise SystemExit(main())
