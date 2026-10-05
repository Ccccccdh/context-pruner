"""Judge routing for the CrewAI r10 fresh-task confirmation batch.

Why this file exists (and what it is **not**)
---------------------------------------------
r10 asks a different question from r8/r9: not "does the v9 pinned-evidence
mechanism work?" but "**is the plugin saving on the r8/r9 task set task-specific
or general?**"  Answering that needs tasks that were never used for development,
while every judging rule stays exactly the one that was frozen before r9.

So this module adds **no judging logic**.  It re-exports the frozen r8 judge
module (``experiments/runners/crewai_semantic_equivalence_v8.py``, SHA256
``26aea58b...``) unchanged and does exactly two things:

  * routes the loaded task file to the new frozen
    ``tasks/stage5_autogen/natural_tasks_r10.json``; and
  * hands that path to the two v8 helpers that default to their own task file
    (``load_tasks`` and ``tool_evidence``) instead of falling back to the r8
    task file by accident.

Both strict and semantic gates therefore continue to be the judges frozen for
r9, applied unchanged to a different task set, and the r10 freeze hashes this
file's bytes (routing) *and* the v8 module's bytes (rules) so neither can drift.

``judge(strict/semantic)`` never reads a task's own numbers out of thin air: the
rules come from the task record (``handle_facts``, ``answer_facts``,
``forbidden_facts``, ``decision``, ``version_fact``, ``region_fact``), which is
what makes "the new task file is the only variable" checkable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiments.runners import crewai_semantic_equivalence_v8 as _frozen

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r10.json"
PROTOCOL = "crewai-two-role-r10-fresh-task-confirmation"

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
#: Private helpers used by the pinning middleware and by the independent audit;
#: they are re-exported so the r10 runner and audit never import the r8 module
#: through a second path with a different default task file.
_fold = _frozen._fold
_literal_present = _frozen._literal_present
_ordered_present = _frozen._ordered_present
_JSON_KEY = _frozen._JSON_KEY
_GLUE = _frozen._GLUE


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the r10 task file unless an explicit path is given."""
    return _frozen.load_tasks(path or TASK_FILE)


def tool_evidence(task: dict[str, Any]) -> tuple[str, ...]:
    """The frozen evidence strings of one r10 task (uses this task's own data)."""
    return _frozen.tool_evidence(task)


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
