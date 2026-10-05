"""Content-free v8 evidence: v7's records plus the long-baseline task identity.

Reuses the v7 evidence layer (``openai_agents_evidence_v7.RecencyBoundaryRecorder``) and
restricts it in two ways:

* the schema is ``openai_long_baseline_boundary_v8``, so a v8 file can never be mistaken
  for a v7 file;
* every record additionally carries the v8 task identity and the registration the
  mechanism consulted (task id, recency constant, v8 registry fingerprint, the carrier
  registration task the frozen v6 span table resolves through, and the number of
  registered literal units the statement contributed).

Everything else is unchanged: no message text, tool output, instruction, path or
credential is written, and only digests, byte and line counts, booleans, counters and the
interval boundaries the mechanism published in its own notes are persisted.
"""

from __future__ import annotations

from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v8 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners.openai_agents_evidence_v7 import (
    PointerRetentionModelProxy,
    RecencyBoundaryRecorder,
)

SCHEMA = "openai_long_baseline_boundary_v8"
STAGES = ("filter_before", "filter_after", "model_input")


class LongBaselineRecorder(RecencyBoundaryRecorder):
    """v7's recorder with the v8 task and registration recorded per boundary."""

    #: The v7/v6 recorders validate the task id against the frozen v5 registry, which
    #: only knows ``django_count_annotations``.  The v8 long task reuses that
    #: registration, so the inherited constructor is allowed to see the id it knows
    #: while ``self.task`` keeps the real v8 task id for every lookup below.
    _task_alias_for_validation = long_registry.SHORT_TASK_ID

    def __init__(self, task: str, retention: Any | None = None) -> None:
        # The frozen v6/v7 recorders validate the task id against the frozen v5 registry,
        # which only knows ``django_count_annotations``.  The v8 long task reuses that
        # registration, so the inherited constructor is handed the id it knows and
        # ``self.task`` is then restored to the real v8 task id.
        super().__init__(long_registry.SHORT_TASK_ID, retention)
        self.task = str(task)
        self.base_registry_fingerprint = long_registry.base_fingerprint(self.task)
        self.registry_fingerprint = long_registry.registry_fingerprint(self.task)

    def _snapshot(
        self,
        items: Any,
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        record["schema"] = SCHEMA
        record.update(
            {
                "long_baseline_task_id": self.task,
                "long_baseline_registry_schema": long_registry.REGISTRY_SCHEMA,
                "long_baseline_registry_fingerprint": long_registry.registry_fingerprint(
                    self.task
                ),
                "long_baseline_base_registry_fingerprint": long_registry.base_fingerprint(
                    self.task
                ),
                "long_baseline_source_registration_task": (
                    policy.SOURCE_REGISTRATION_TASK
                ),
                "long_baseline_recent_turns_kept_verbatim": policy.RECENT_TURNS_KEPT,
                "long_baseline_policy": dict(policy.MANIFEST_POLICY),
                "long_baseline_investigation_steps": list(
                    long_registry.INVESTIGATION_STEPS
                ),
                "long_baseline_literal_context_units": list(
                    long_registry.LITERAL_CONTEXT_UNITS.get(self.task, ())
                ),
            }
        )
        return record


def mechanism_identity() -> dict[str, Any]:
    """The mechanism block written into the v8 manifest."""
    return {
        "schema": SCHEMA,
        "mechanism_schema": policy.MECHANISM_SCHEMA,
        "v7_mechanism_schema": policy.policy.MECHANISM_SCHEMA,
        **policy.policy_dict(),
    }


__all__ = [
    "LongBaselineRecorder",
    "PointerRetentionModelProxy",
    "SCHEMA",
    "STAGES",
    "mechanism_identity",
]
