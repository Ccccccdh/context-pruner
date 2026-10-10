"""Evidence recorder for the held-out `requests-1766` task.

The frozen v8/v9 recorder chain resolves every registration lookup through the Django task
tables (``long_registry`` -> the frozen v5/v6 literal registry).  This task has its own
registration, so this module

* **registers this task's literal data into those frozen tables at import time** - a runtime
  data registration, not an edit: no frozen file changes, and the dict objects are shared, so
  both the v5 and v6 modules see it;
* subclasses the frozen v9 recorder and rewrites the *registration-derived* fields per
  boundary from this task's own registry, so the evidence never claims Django's task id,
  Django's investigation steps or Django's fingerprints;
* keeps the recorded schema label distinct, so an audit can tell this task's evidence apart.

Everything the mechanism acts on (structure, bytes, hashes, per-output manifests, the
restore/fallback counters) is inherited untouched.
"""

from __future__ import annotations

from typing import Any

from experiments.runners import openai_agents_literal_registry_v5 as frozen_v5
from experiments.runners import openai_agents_literal_registry_v6 as frozen_v6
from experiments.runners import openai_agents_long_task_registry_v8 as frozen_long
from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder

SCHEMA = "openai_requests_heldout_boundary_v13"
STAGES = ("filter_before", "filter_after", "model_input")


def register_task_data() -> None:
    """Put this task's literal registration into the frozen data tables (runtime only)."""
    constraints = frozen_v5.CONSTRAINTS
    if registry.TASK_ID not in constraints:
        constraints[registry.TASK_ID] = {
            label: {"literals": tuple(phrases), "units": tuple(phrases)}
            for label, phrases in registry.LITERAL_LABELS.items()
        }
    sources = frozen_v5.SOURCES
    if registry.TASK_ID not in sources:
        # No registered span table for this task: the span machinery uses ``.get`` for
        # lookups, so an explicit empty entry keeps every lookup total without inventing
        # line spans that the mechanism must not act on.
        sources[registry.TASK_ID] = ()
    if registry.TASK_ID not in frozen_v6._SPAN_CACHE:
        frozen_v6._SPAN_CACHE[registry.TASK_ID] = ()


register_task_data()


class RequestsRecorder(NarrowGuardRecorder):
    """v9's recorder with this task's registration recorded per boundary."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        register_task_data()
        # The frozen chain must be handed a task id its tables know; the real id is restored
        # immediately and every registration-derived field is rewritten in ``_snapshot``.
        super().__init__(frozen_long.SHORT_TASK_ID, retention)
        self.task = str(task)
        self.base_registry_fingerprint = registry.registry_fingerprint(self.task)
        self.registry_fingerprint = registry.registry_fingerprint(self.task)

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        """Occurrence counts through **this** task's registration, not Django's."""
        lowered = str(searchable).lower()
        return {
            label: sum(lowered.count(str(phrase).lower()) for phrase in phrases)
            for label, phrases in registry.LITERAL_LABELS.items()
        }

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
                "long_baseline_registry_schema": registry.REGISTRY_SCHEMA,
                "long_baseline_registry_fingerprint": registry.registry_fingerprint(self.task),
                "long_baseline_base_registry_fingerprint": registry.registry_fingerprint(
                    self.task
                ),
                "long_baseline_source_registration_task": registry.TASK_ID,
                "long_baseline_investigation_steps": list(registry.PROTOCOL_STEPS),
                "long_baseline_literal_context_units": list(registry.REQUIRED_TERMS),
                "heldout_issue_id": registry.ISSUE_ID,
                "heldout_base_commit": registry.BASE_COMMIT,
                # The inherited fields name Django's labels; on this task they must name
                # this task's registration, so both are recomputed here.
                "narrow_guard_literal_labels": list(registry.LITERAL_LABELS),
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


__all__ = ["RequestsRecorder", "SCHEMA", "STAGES", "register_task_data"]
