"""Judge routing for the CrewAI r12 recency batch (identical rules).

r12 changes the plugin mechanism only, so the judging rules must not change at all.
This module re-exports the frozen r8 judge module unchanged and routes the loaded
task file to the frozen r10 task file, which r12 reuses on purpose so the mechanism
stays the single variable between r10, r11 and r12.

The first-class gate fields of r12 (``tool_sequence_consistent`` and
``first_pass_facts``) are measurements over the recorded traces, not judging rules,
so they live in the runner and in the independent audit -- not here.  That keeps
"same judge, new mechanism" checkable by hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiments.runners import crewai_semantic_equivalence_v8 as _frozen

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r10.json"
PROTOCOL = "crewai-two-role-r12-recency-tool-rounds"

#: Re-exported from the frozen r8 judge, deliberately unchanged.
FactCheck = _frozen.FactCheck
JudgeVerdict = _frozen.JudgeVerdict
answer_rule = _frozen.answer_rule
answer_rule_for = _frozen.answer_rule_for
canonical_decision = _frozen.canonical_decision
handle_rule = _frozen.handle_rule
handle_rule_for = _frozen.handle_rule_for
judge_answer = _frozen.judge_answer
judge_handoff = _frozen.judge_handoff
task_evidence = _frozen.task_evidence
tool_evidence = _frozen.tool_evidence
_fold = _frozen._fold
_literal_present = _frozen._literal_present
_ordered_present = _frozen._ordered_present

#: The frozen rule module this batch judges with; the audit hashes its bytes.
FROZEN_RULE_MODULE = Path(_frozen.__file__)

__all__ = [
    "FactCheck",
    "FROZEN_RULE_MODULE",
    "JudgeVerdict",
    "PROTOCOL",
    "TASK_FILE",
    "answer_rule",
    "answer_rule_for",
    "canonical_decision",
    "handle_rule",
    "handle_rule_for",
    "judge_answer",
    "judge_handoff",
    "load_tasks",
    "task_evidence",
    "tool_evidence",
]


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the frozen r10 task file unless an explicit path is given."""
    return _frozen.load_tasks(path or TASK_FILE)
