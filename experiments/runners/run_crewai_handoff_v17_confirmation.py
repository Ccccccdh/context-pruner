"""CrewAI r17 confirmation three-arm batch: the audited pilot flow, confirmation tasks.

The pilot runner (``run_crewai_handoff_v17.py``) is pinned by its own freeze, so this file
does not edit it: it imports it and overrides only the task file, the task ids and the
protocol name.  Every other line of behaviour -- the case flow, the guard wiring, the
adapter configuration, the manifest fields and the accounting -- is the audited pilot code,
and the delegation is asserted below.

``assert_reused_modules_are_pinned()`` (the pilot runner's own r17 §0.1 assertion) runs
inside ``main``, so the reused modules are checked against the freeze that pins them before
any request is sent.
"""

from __future__ import annotations

import json
from pathlib import Path

from experiments.runners import run_crewai_handoff_v17 as pilot

REUSED_FROM_PILOT = {
    "source_file": "experiments/runners/run_crewai_handoff_v17.py",
    "source_sha256_at_build": "634655ce5d22fcee8ff07b0930c54dc11fbe1b479ae62de2922e99c5d0e951f5",
    "frozen_sha256": "634655ce5d22fcee8ff07b0930c54dc11fbe1b479ae62de2922e99c5d0e951f5",
    "reused_changes": [
        "TASK_FILE, TASK_IDS, PROTOCOL and REMEDY_PROBE are the confirmation ones",
        "everything else is imported from the audited pilot module"
    ]
}

#: The confirmation batch's own configuration: task file, task ids, protocol, batch id.
TASK_FILE = Path("tasks/stage5_autogen/natural_tasks_r17_confirmation.json")
PROTOCOL = "crewai-two-role-r17-confirmation"
TASK_IDS = (
    "watermark_publish_gate",
    "autoscale_policy_gate",
    "key_rotation_gate",
    "index_rebuild_gate",
)
REMEDY_PROBE = ("watermark_publish_gate", 0)

# Everything else comes from the audited pilot module.
METHODS = pilot.METHODS
K_RECENT_TOOL_ROUNDS = pilot.K_RECENT_TOOL_ROUNDS
MAX_GUARD_REJECTIONS = pilot.MAX_GUARD_REJECTIONS
GUARD_ALL_ARMS = pilot.GUARD_ALL_ARMS
FIRST_ROLE = pilot.FIRST_ROLE
SECOND_ROLE = pilot.SECOND_ROLE
QUALITY_GATES = pilot.QUALITY_GATES
FAILURE_CLASSES = pilot.FAILURE_CLASSES
FIRST_ROLE_STAGE = pilot.FIRST_ROLE_STAGE
DECISION_PLACEHOLDER_SENTENCE = pilot.DECISION_PLACEHOLDER_SENTENCE

#: Bind the task-file-dependent helpers to the confirmation task file.
load_tasks = pilot.load_tasks
build_tools = pilot.build_tools
latest_task_prompt = pilot.latest_task_prompt
_all_observations = pilot._all_observations
fixed_history = pilot.fixed_history
decider_role_prompt = pilot.decider_role_prompt
classify_failure = pilot.classify_failure
run_case = pilot.run_case
_make_llm = pilot._make_llm
_mock_stage_responses = pilot._mock_stage_responses


def assert_task_file_is_the_confirmation_one() -> None:
    """Fail loudly if the pilot module's task file is not the confirmation file."""
    current = Path(pilot.TASK_FILE).as_posix()
    expected = TASK_FILE.as_posix()
    if current != expected:
        raise SystemExit(
            "refusing to run: the reused pilot runner reads "
            f"{current!r}, not the confirmation task file {expected!r}"
        )


def main(argv=None) -> None:
    # Rebind the pilot module's configuration FIRST, then assert: the assertion checks that
    # the module the case flow reads from now points at the confirmation task file.
    pilot.TASK_FILE = TASK_FILE
    pilot.PROTOCOL = PROTOCOL
    pilot.TASK_IDS = TASK_IDS
    pilot.REMEDY_PROBE = REMEDY_PROBE
    assert_task_file_is_the_confirmation_one()
    pilot.assert_reused_modules_are_pinned()
    pilot.main(argv)


if __name__ == "__main__":
    main()
