"""Judge routing for the CrewAI r13 tool-round / contract batch.

Two things matter here and nothing else:

  * the **judging rules** are the frozen r8 module, re-exported unchanged, routed to
    the frozen r10 task file.  r13 changes a *prompt sentence*, not a judgement:
    strictness, thresholds and every stored answer stay exactly as frozen for r9;
  * the **strict gate's template** is declared here so the runner and the audit can
    state, in their own records, what the strict gate looks for.  The frozen rule
    itself still reads ``decision`` as a labelled field and still compares the
    canonicalised value, so replacing the template with the real value passes and
    echoing the template fails -- exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiments.runners import crewai_semantic_equivalence_v8 as _frozen

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r10.json"
PROTOCOL = "crewai-two-role-r13-toolrounds-and-contract"

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
_json_key_pattern = _frozen._JSON_KEY

#: The frozen rule module this batch judges with; the audit hashes its bytes.
FROZEN_RULE_MODULE = Path(_frozen.__file__)

#: The strict gate's decision template, declared (not changed): the frozen judge
#: reads the labelled field and canonicalises it, so this text is what the contract
#: asks the role to replace.
ANSWERS_WITH_DECISION_TEMPLATE = True


def answer_rule_v13(task: dict[str, Any]) -> dict[str, Any]:
    """The frozen answer rule plus the declared decision template."""
    rule = answer_rule_for([task], str(task["task_id"]))
    rule["decision_template"] = f"decision=<{task['decision']}>"
    return rule


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the frozen r10 task file unless an explicit path is given."""
    return _frozen.load_tasks(path or TASK_FILE)


__all__ = [
    "ANSWERS_WITH_DECISION_TEMPLATE",
    "FactCheck",
    "FROZEN_RULE_MODULE",
    "JudgeVerdict",
    "PROTOCOL",
    "TASK_FILE",
    "answer_rule",
    "answer_rule_for",
    "answer_rule_v13",
    "canonical_decision",
    "handle_rule",
    "handle_rule_for",
    "judge_answer",
    "judge_handoff",
    "load_tasks",
    "task_evidence",
    "tool_evidence",
]
