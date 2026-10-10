"""The frozen duplicate-output rule, applied to the held-out `requests-1766` registration.

Only the *task registration* is new here.  The reduction (``filter_items``), the structural
invariant, the literal-survival guard, the pointer checks and the whole-payload fallback are
inherited from the frozen v12/v9/v6 classes by identity, and the zero-API gate asserts that by
``is`` comparison.  What this module supplies is the held-out task's own registration: its
literal carrier set (used by the guard) and its statement units.

Why an explicit registration rewrite is needed: the frozen constructor chain validates task
ids against the frozen Django tables (``TASKS``), and derives the guard's protected units and
literal units through the Django registration.  A held-out task cannot resolve through those
tables, so the parent is constructed with a task id it knows and every registration-derived
attribute is then rewritten from this task's own registry.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping, Sequence

from experiments.runners import openai_agents_long_baseline_boundary_v8 as frozen_long
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import (
    ExactDuplicateFilter,
)
from experiments.runners.openai_agents_long_baseline_boundary_v9 import (
    normalise_unit,
    _text_sha,
)

MECHANISM_SCHEMA = "exact_duplicate_requests_v13"
GUARD_SCHEMA = "heldout_literal_carriers_v13"
MIN_UNIT_CHARS = 8


def statement_units(task_statement: str) -> list[str]:
    from experiments.runners.openai_agents_evidence_safe_retention_v5 import split_units

    return [
        unit
        for unit in split_units(str(task_statement))
        if len(str(unit).strip()) >= MIN_UNIT_CHARS
    ]


def protectable_units(task_statement: str) -> list[str]:
    """Statement units that carry one of this task's registered literals."""
    phrases = [
        phrase.lower()
        for values in registry.LITERAL_LABELS.values()
        for phrase in values
    ]
    return [
        unit
        for unit in statement_units(task_statement)
        if any(phrase in unit.lower() for phrase in phrases)
    ]


class RequestsDuplicateFilter(ExactDuplicateFilter):
    """v12's exact-duplicate rule on the held-out task's registration."""

    def __init__(
        self,
        *,
        task: str,
        task_statement: str,
        constraint_phrases: Mapping[str, Sequence[str]] | None = None,
        token_counter: Callable[[str], int] | None = None,
        hard_limit_bytes: int = 24_000,
    ) -> None:
        super().__init__(
            # The parent chain validates the task id against the frozen Django tables; the
            # real id is restored below and every registration-derived field is rewritten.
            task=frozen_long.LONG_TASK_ID,
            task_statement=str(task_statement),
            constraint_phrases=constraint_phrases or registry.literal_phrases(),
            token_counter=token_counter,
            hard_limit_bytes=hard_limit_bytes,
        )
        self.heldout_task = str(task)
        self.long_task = str(task)
        self.task_statement = str(task_statement)
        units = statement_units(self.task_statement)
        protected = protectable_units(self.task_statement)
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
        # The inherited fields named the frozen Django registration; on this task they must
        # name this task's own registration.
        self.registry_fingerprint = registry.registry_fingerprint(self.heldout_task)
        self.base_registry_fingerprint = registry.registry_fingerprint(self.heldout_task)
        self.long_registry_fingerprint = registry.registry_fingerprint(self.heldout_task)
        self.long_base_registry_fingerprint = registry.registry_fingerprint(self.heldout_task)
        self.long_registry_problems = registry.verify(self.heldout_task)
        self.registry_problems = list(self.long_registry_problems)

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "v12_schema": "exact_duplicate_v12",
                "heldout_task": self.heldout_task,
                "heldout_registry_schema": registry.REGISTRY_SCHEMA,
                "heldout_registry_fingerprint": registry.registry_fingerprint(
                    self.heldout_task
                ),
                "heldout_registry_problems": list(self.long_registry_problems),
                "narrow_guard_schema": GUARD_SCHEMA,
                "narrow_guard_protected_unit_count": len(self.protected_unit_sha256),
                "narrow_guard_protectable_unit_count": len(self.protectable_units),
                "narrow_guard_full_statement_unit_count": len(self.task_unit_sha256),
                "narrow_guard_unprotected_statement_unit_count": len(
                    self.unprotected_statement_unit_sha256
                ),
                "narrow_guard_literal_labels": list(registry.LITERAL_LABELS),
            }
        )
        return metrics


def inherited_rule_objects() -> dict[str, Any]:
    """The frozen rule objects this filter must not re-implement (checked by identity)."""
    return {
        "filter_items": ExactDuplicateFilter.filter_items,
        "deduplicate": __import__(
            "experiments.runners.openai_agents_exact_duplicate_replay_v12",
            fromlist=["deduplicate"],
        ).deduplicate,
        "_structural_violation": ExactDuplicateFilter._structural_violation,
        "_carrier_loss": ExactDuplicateFilter._carrier_loss,
        "_elide": ExactDuplicateFilter._elide,
    }


def mechanism_fingerprint() -> str:
    return hashlib.sha256(
        f"{MECHANISM_SCHEMA}:{registry.registry_fingerprint()}".encode()
    ).hexdigest()


__all__ = [
    "GUARD_SCHEMA",
    "MECHANISM_SCHEMA",
    "RequestsDuplicateFilter",
    "inherited_rule_objects",
    "mechanism_fingerprint",
    "protectable_units",
    "statement_units",
]
