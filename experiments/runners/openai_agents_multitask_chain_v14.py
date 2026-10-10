"""Shared chain for the v14 multi-task runs: recorder, filter, report fix, manifest writer.

Three defects found in the v13 runs are fixed **here**, before any paid request:

1. a single-arm batch crashed in the shared report step (`KeyError: paired_n`) *after* the
   artifacts were written, so the manifest had to be amended by a separate tool.  The fix is a
   runtime patch of ``build_report`` that fills the missing keys for single-arm batches and
   keeps the shared, frozen-hashed module untouched; the runner then writes the manifest
   itself, in one pass.
2. the manifest write failed on a wrong relative-path base; the path handling below is
   absolute and cannot depend on the caller's base.
3. the mechanism counters the audit asked for
   (``exact_duplicate_replacements``, ``trigger_gate_*``, ``narrow_guard_protected_unit_count``)
   were not persisted into the sample rows, because the frozen counter builder uses a fixed
   field list.  It is replaced at runtime by an extended builder that merges the filter's own
   metrics, so assertions can read the row fields directly.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.runners import openai_agents_literal_registry_v5 as frozen_v5
from experiments.runners import openai_agents_literal_registry_v6 as frozen_v6
from experiments.runners import openai_agents_long_baseline_boundary_v8 as frozen_long
from experiments.runners import openai_agents_multitask_registry_v14 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import ExactDuplicateFilter
from experiments.runners.openai_agents_long_baseline_boundary_v9 import (
    _text_sha,
    normalise_unit,
)

MECHANISM_SCHEMA = "exact_duplicate_multitask_v14"
GUARD_SCHEMA = "heldout_literal_carriers_v14"
EVIDENCE_SCHEMA = "openai_multitask_heldout_boundary_v14"
MIN_UNIT_CHARS = 8

#: Mechanism counters the audits read directly from the sample rows.
PERSISTED_EXTRA_FIELDS = (
    "selective_retention_schema",
    "exact_duplicate_schema",
    "exact_duplicate_replacements",
    "exact_duplicate_saved_bytes",
    "exact_duplicate_rejected",
    "selective_retention_saved_bytes_total",
    "trigger_gate_calls",
    "trigger_gate_triggered_calls",
    "trigger_gate_passthrough_calls",
    "trigger_gate_soft_limit_tokens",
    "trigger_gate_last_estimated_tokens",
    "narrow_guard_schema",
    "narrow_guard_protected_unit_count",
    "narrow_guard_protectable_unit_count",
    "narrow_guard_full_statement_unit_count",
    "narrow_guard_unprotected_statement_unit_count",
    "narrow_guard_literal_labels",
    "heldout_task",
    "heldout_instance",
    "heldout_registry_fingerprint",
)


def register_task_data(task_id: str) -> None:
    """Runtime data registration so the frozen guard resolves this task's literals."""
    constraints = frozen_v5.CONSTRAINTS
    if task_id not in constraints:
        constraints[task_id] = {
            label: {"literals": tuple(phrases), "units": tuple(phrases)}
            for label, phrases in registry.literal_phrases(task_id).items()
        }
    frozen_v5.SOURCES.setdefault(task_id, ())
    frozen_v6._SPAN_CACHE.setdefault(task_id, ())


class MultitaskRecorder(NarrowGuardRecorder):
    """v9's recorder with this task's registration recorded per boundary."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        register_task_data(str(task))
        super().__init__(frozen_long.SHORT_TASK_ID, retention)
        self.task = str(task)
        self.base_registry_fingerprint = registry.fingerprint(self.task)
        self.registry_fingerprint = registry.fingerprint(self.task)

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        lowered = str(searchable).lower()
        return {
            label: sum(lowered.count(str(phrase).lower()) for phrase in phrases)
            for label, phrases in registry.literal_phrases(self.task).items()
        }

    def _snapshot(
        self, items: Any, instructions: str | None, stage: str, index: int
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        entry = registry.task(self.task)
        record["schema"] = EVIDENCE_SCHEMA
        record.update(
            {
                "long_baseline_task_id": self.task,
                "long_baseline_registry_schema": registry.REGISTRY_SCHEMA,
                "long_baseline_registry_fingerprint": registry.fingerprint(self.task),
                "long_baseline_base_registry_fingerprint": registry.fingerprint(self.task),
                "long_baseline_source_registration_task": self.task,
                "long_baseline_investigation_steps": list(entry["protocol_steps"]),
                "long_baseline_literal_context_units": list(entry["required_terms"]),
                "heldout_instance_id": entry["instance_id"],
                "heldout_base_commit": entry["base_commit"],
                "narrow_guard_literal_labels": list(entry["literals"]),
                "registered_literal_counts": self._literal_counts(
                    "\n".join(_searchable_text(item) for item in items)
                ),
            }
        )
        return record


def _searchable_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("content", "output", "arguments"):
            value = item.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [
                    str(part.get("text", "")) for part in value if isinstance(part, dict)
                ]
                if any(parts):
                    return "\n".join(parts)
    return ""


def statement_units(task_statement: str) -> list[str]:
    from experiments.runners.openai_agents_evidence_safe_retention_v5 import split_units

    return [
        unit
        for unit in split_units(str(task_statement))
        if len(str(unit).strip()) >= MIN_UNIT_CHARS
    ]


def protectable_units(task_id: str, task_statement: str) -> list[str]:
    phrases = [
        phrase.lower()
        for values in registry.literal_phrases(task_id).values()
        for phrase in values
    ]
    return [
        unit
        for unit in statement_units(task_statement)
        if any(phrase in unit.lower() for phrase in phrases)
    ]


class MultitaskDuplicateFilter(ExactDuplicateFilter):
    """The frozen v12 duplicate rule on one held-out task's registration."""

    def __init__(
        self,
        *,
        task: str,
        task_statement: str,
        constraint_phrases: Mapping[str, Sequence[str]] | None = None,
        token_counter: Any | None = None,
        hard_limit_bytes: int = 24_000,
    ) -> None:
        register_task_data(str(task))
        super().__init__(
            task=frozen_long.LONG_TASK_ID,
            task_statement=str(task_statement),
            constraint_phrases=constraint_phrases
            or registry.literal_phrases(str(task)),
            token_counter=token_counter,
            hard_limit_bytes=hard_limit_bytes,
        )
        self.heldout_task = str(task)
        self.long_task = str(task)
        self.task_statement = str(task_statement)
        units = statement_units(self.task_statement)
        protected = protectable_units(self.heldout_task, self.task_statement)
        self.task_units = units
        self.task_unit_sha256 = [_text_sha(normalise_unit(unit)) for unit in units]
        self.literal_units = protected
        self.protectable_units = protected
        self.protected_unit_sha256 = sorted(
            {_text_sha(normalise_unit(unit)) for unit in protected}
        )
        self.unprotected_statement_unit_sha256 = sorted(
            set(self.task_unit_sha256) - set(self.protected_unit_sha256)
        )
        self.guard_narrowing_schema = GUARD_SCHEMA
        self.registry_fingerprint = registry.fingerprint(self.heldout_task)
        self.base_registry_fingerprint = registry.fingerprint(self.heldout_task)
        self.long_registry_fingerprint = registry.fingerprint(self.heldout_task)
        self.long_base_registry_fingerprint = registry.fingerprint(self.heldout_task)
        self.long_registry_problems = registry.verify(self.heldout_task)
        self.registry_problems = list(self.long_registry_problems)

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "exact_duplicate_schema": "exact_duplicate_v12",
                "heldout_task": self.heldout_task,
                "heldout_instance": registry.task(self.heldout_task)["instance_id"],
                "heldout_registry_fingerprint": registry.fingerprint(self.heldout_task),
                "heldout_registry_problems": list(self.long_registry_problems),
                "narrow_guard_schema": GUARD_SCHEMA,
                "narrow_guard_protected_unit_count": len(self.protected_unit_sha256),
                "narrow_guard_protectable_unit_count": len(self.protectable_units),
                "narrow_guard_full_statement_unit_count": len(self.task_unit_sha256),
                "narrow_guard_unprotected_statement_unit_count": len(
                    self.unprotected_statement_unit_sha256
                ),
                "narrow_guard_literal_labels": list(
                    registry.task(self.heldout_task)["literals"]
                ),
            }
        )
        return metrics


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install this task's duplicate filter in the plugin arm only."""
    from experiments.runners.trigger_gate import BudgetTriggeredFilter

    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    wrapper = original(method, **kwargs)
    retention = MultitaskDuplicateFilter(
        task=str(case.scenario),
        task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    if isinstance(wrapper, BudgetTriggeredFilter):
        wrapper.inner = retention
        return v8._GateAware(wrapper, retention)
    return retention


def extended_retention_counters(original, recorder: Any, context_filter: Any) -> dict[str, Any]:
    """The frozen counter builder plus the mechanism counters the audits assert on."""
    counters = original(recorder, context_filter)
    metrics = context_filter.metrics_dict() if hasattr(context_filter, "metrics_dict") else {}
    extra = {}
    for name in PERSISTED_EXTRA_FIELDS:
        if name in metrics:
            extra[name] = metrics[name]
    counters.update(extra)
    counters["persisted_extra_fields"] = sorted(extra)
    return counters


@contextmanager
def chain_wiring():
    """Install the runtime patches the frozen modules cannot make themselves."""
    original_counters = v8._retention_counters
    original_filter = v8.build_retentive_filter
    original_report = base.build_report
    original_reporter = v8._retention_counters

    def single_arm_report(samples):
        report = original_report(samples)
        paired = report.get("paired")
        if isinstance(paired, dict) and "paired_n" not in paired:
            report["paired"] = {
                "paired_n": 0,
                "actual_input_savings_rate_vs_baseline": 0.0,
                "success_delta": 0.0,
                **paired,
            }
            report["paired_note"] = (
                "single-arm payload-acquisition batch: no paired comparison exists, and none may "
                "be reported from this batch"
            )
        return report

    v8._retention_counters = (
        lambda recorder, context_filter: extended_retention_counters(
            original_counters, recorder, context_filter
        )
    )
    v8.build_retentive_filter = build_retentive_filter
    base.build_report = single_arm_report
    try:
        yield
    finally:
        v8._retention_counters = original_reporter
        v8.build_retentive_filter = original_filter
        base.build_report = original_report


def write_manifest(
    root: Path | None,
    *,
    kind: str,
    purpose: str,
    batch_id: str,
    freeze: Path,
    task_ids: Sequence[str],
    methods: Sequence[str],
    repeats: int,
    max_api_requests: int,
    max_summary_calls_per_sample: int,
    admission: dict | None = None,
) -> dict[str, Any] | None:
    """Write every frozen manifest key in one pass, right after the batch finishes."""
    if root is None:
        return None
    path = Path(root) / "manifest.json"
    if not path.is_file():
        return None
    freeze = Path(freeze)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["kind"] = kind
    manifest["purpose"] = purpose
    manifest["citable_as_saving"] = False
    manifest["citable_as_quality_equivalence"] = False
    manifest["citable_note"] = (
        "the flags stay false unless the frozen acceptance line is met; a single batch of "
        "tasks is not multi-task stability on its own"
    )
    manifest["data_class"] = "public_source_diagnostic"
    manifest["held_out_task_ids"] = list(task_ids)
    manifest["freeze_path"] = str(freeze).replace("\\", "/")
    manifest["freeze_sha256"] = hashlib.sha256(freeze.read_bytes()).hexdigest()
    manifest["v14_registry"] = registry.manifest_block()
    manifest["task_registration_problems"] = registry.all_problems()
    manifest["mechanism"] = {
        "arm": "pruner_v1",
        "name": "exact_duplicate_output_v14_multitask",
        "schema": MECHANISM_SCHEMA,
        "rule": (
            "the frozen v12 rule: an older tool output whose text is byte-identical to a newer "
            "one is replaced by a pointer naming the newest call id and the source sha256; the "
            "newest full copy and every call/output item stay"
        ),
        "frozen_rule_module": "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
        "chain_module": "experiments/runners/openai_agents_multitask_chain_v14.py",
        "persisted_extra_fields": list(PERSISTED_EXTRA_FIELDS),
    }
    manifest["request_budget"] = {
        "max_api_requests": int(max_api_requests),
        "methods": list(methods),
        "repeats": int(repeats),
        "tasks": len(list(task_ids)),
        "max_summary_calls_per_sample": int(max_summary_calls_per_sample),
        "worst_case_model_calls": int(repeats) * len(list(task_ids)) * len(list(methods)) * 7,
        "worst_case_summary_calls": int(repeats) * len(list(task_ids)) * int(
            max_summary_calls_per_sample
        ),
    }
    if admission is not None:
        manifest["admission"] = admission
    manifest["manifest_written_in_one_pass"] = True
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


__all__ = [
    "EVIDENCE_SCHEMA",
    "GUARD_SCHEMA",
    "MECHANISM_SCHEMA",
    "MultitaskDuplicateFilter",
    "MultitaskRecorder",
    "PERSISTED_EXTRA_FIELDS",
    "build_retentive_filter",
    "chain_wiring",
    "extended_retention_counters",
    "protectable_units",
    "register_task_data",
    "statement_units",
    "write_manifest",
]
