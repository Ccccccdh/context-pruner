"""Small structured state that survives ordinary context compression."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any


PUBLIC_TEST_STATUSES = frozenset(
    {"not_run", "passed", "failed", "stale_after_edit"}
)


@dataclass(frozen=True)
class TaskStateSnapshot:
    """Current task facts derived from the host, not from model narration."""

    task_id: str
    current_source_location: str
    changed_files: tuple[str, ...]
    edit_applied: bool
    latest_public_test_status: str
    latest_host_feedback: str | None
    remaining_action: str
    workspace_epoch: int

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must be nonempty")
        if not self.current_source_location.strip():
            raise ValueError("current_source_location must be nonempty")
        if self.latest_public_test_status not in PUBLIC_TEST_STATUSES:
            raise ValueError("invalid latest_public_test_status")
        if self.workspace_epoch < 0:
            raise ValueError("workspace_epoch must be nonnegative")
        if self.edit_applied != bool(self.changed_files):
            raise ValueError("edit_applied must match current changed_files")
        if not self.remaining_action.strip():
            raise ValueError("remaining_action must be nonempty")

    def model_record(self) -> dict[str, Any]:
        """Return only facts that are safe to send to the model."""
        return {
            "task_id": self.task_id,
            "current_source_location": self.current_source_location,
            "changed_files": list(self.changed_files),
            "edit_applied": self.edit_applied,
            "latest_public_test_status": self.latest_public_test_status,
            "remaining_action": self.remaining_action,
            "workspace_epoch": self.workspace_epoch,
        }

    def to_model_block(self) -> str:
        payload = json.dumps(
            self.model_record(), ensure_ascii=False, separators=(",", ":")
        )
        return f"[context-pruner task state]\n{payload}"

    def to_audit_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["changed_files"] = list(self.changed_files)
        return record


__all__ = ["PUBLIC_TEST_STATUSES", "TaskStateSnapshot"]
