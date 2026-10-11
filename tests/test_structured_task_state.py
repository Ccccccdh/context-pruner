from __future__ import annotations

import json

from context_pruner.task_state import TaskStateSnapshot


def test_model_block_is_stable_and_never_exposes_hidden_host_feedback() -> None:
    snapshot = TaskStateSnapshot(
        task_id="pydata__xarray-3095",
        current_source_location="xarray/core/variable.py:1944-1951",
        changed_files=("xarray/core/variable.py",),
        edit_applied=True,
        latest_public_test_status="passed",
        latest_host_feedback="hidden assertion expected dtype '<U3'",
        remaining_action="Review the current edit.",
        workspace_epoch=2,
    )

    block = snapshot.to_model_block()

    assert block.startswith("[context-pruner task state]\n")
    payload = json.loads(block.split("\n", 1)[1])
    assert list(payload) == [
        "task_id",
        "current_source_location",
        "changed_files",
        "edit_applied",
        "latest_public_test_status",
        "remaining_action",
        "workspace_epoch",
    ]
    assert payload["changed_files"] == ["xarray/core/variable.py"]
    assert "hidden" not in block
    assert "<U3" not in block
    assert snapshot.to_audit_record()["latest_host_feedback"].startswith("hidden assertion")


def test_task_state_rejects_invalid_public_test_status() -> None:
    try:
        TaskStateSnapshot(
            task_id="task",
            current_source_location="source.py",
            changed_files=(),
            edit_applied=False,
            latest_public_test_status="probably_passed",
            latest_host_feedback=None,
            remaining_action="Inspect source.",
            workspace_epoch=0,
        )
    except ValueError as error:
        assert "latest_public_test_status" in str(error)
    else:
        raise AssertionError("invalid public-test status was accepted")

