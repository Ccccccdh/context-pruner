"""Content-free v9 evidence: v8's records with the narrowed-guard counters.

Reuses the v8 evidence layer (``openai_agents_evidence_v8.LongBaselineRecorder``) and
adds what this version has to show:

* the schema is ``openai_narrow_guard_boundary_v9``;
* every record carries the narrowed guard's protected-unit count and the number of
  statement units the narrowing leaves unprotected (counts and digests only, never text),
  so "the protected set really is the literal carriers" is a checkable property of the
  record;
* the registered-literal occurrence counts are recomputed through the **frozen v5
  registration** the long task reuses.  The inherited helpers resolve literals through the
  task id string, which is the frozen key here, and the long task owns no separate
  literal table, so this override makes the counts explicit instead of relying on that.
"""

from __future__ import annotations

from typing import Any, Sequence

from experiments.runners import openai_agents_literal_registry_v5 as frozen
from experiments.runners import openai_agents_long_baseline_boundary_v9 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners.openai_agents_evidence_v8 import LongBaselineRecorder

SCHEMA = "openai_narrow_guard_boundary_v9"
STAGES = ("filter_before", "filter_after", "model_input")


class NarrowGuardRecorder(LongBaselineRecorder):
    """v8's recorder with the narrowed guard's own counters.

    The task id is accepted in either form: the v8 long id or the frozen registration id
    it reuses.  The runner creates recorders from the case's scenario, which is the long
    id; the frozen v6/v7 constructors validate against the frozen registry, which only
    knows the short id, so the alias is resolved here rather than at every call site.
    """

    #: Long task id -> frozen registration id it reuses.
    TASK_ALIASES = {policy.LONG_TASK_ID: policy.SHORT_TASK_ID}

    def __init__(self, task: str, retention: Any | None = None) -> None:
        resolved = self.TASK_ALIASES.get(str(task), str(task))
        super().__init__(resolved, retention)
        self.long_task = str(task)

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        """Occurrence counts through the frozen registration the long task reuses."""
        return {
            label: sum(
                str(searchable).lower().count(str(phrase).lower()) for phrase in phrases
            )
            for label, phrases in frozen.literals_for(policy.SHORT_TASK_ID).items()
        }

    def _snapshot(
        self,
        items: Sequence[Any],
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        record["schema"] = SCHEMA
        protected = list(getattr(self.retention, "protected_unit_sha256", []) or [])
        record.update(
            {
                "narrow_guard_schema": "narrow_to_registered_literal_carriers_v9",
                "narrow_guard_task_id": self.long_task,
                "narrow_guard_protected_unit_count": len(protected),
                "narrow_guard_unprotected_statement_unit_count": len(
                    getattr(self.retention, "unprotected_statement_unit_sha256", []) or []
                ),
                "narrow_guard_full_statement_unit_count": len(
                    getattr(self.retention, "task_unit_sha256", []) or []
                ),
                "narrow_guard_literal_labels": list(
                    long_registry.literals_for(policy.LONG_TASK_ID)
                ),
            }
        )
        return record


def mechanism_identity() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mechanism_schema": policy.MECHANISM_SCHEMA,
        "v8_mechanism_schema": policy.v8.MECHANISM_SCHEMA,
        **policy.policy_dict(),
    }


__all__ = ["NarrowGuardRecorder", "SCHEMA", "STAGES", "mechanism_identity"]
