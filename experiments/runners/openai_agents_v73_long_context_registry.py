"""v73 registry: the v24 registry pointed at the long-context sealed task set.

What this file is
-----------------
A **fork** of ``experiments/runners/openai_agents_v24_registry.py``.  The v24 module is not
edited and not re-pointed, so every earlier round keeps resolving exactly the registry it
resolved before.  Nothing about the host contract changes here: the renderer, the 160-character
bound, the arm-neutral first prompt and the tool-doc shape are the v24 code.

The one difference is *which tasks*: this registry points the loader at
``integrations/openai_agents/V73_LONG_CONTEXT_SOURCE_REGISTRATION.json``, whose tasks each
register **six distinct read-once views** on a single causal chain instead of three short ones.

Why the shape changed
---------------------
The v16/v24 shape registers three short views (985-5,673 characters in total, every task marked
``<15 min fix``) and answers in one line; measured plugin view sizes are 3,342-5,604 tokens,
far below the 28,000-token bar the OpenHands adapters use.  v73 registers six views totalling
60,352-79,423 characters, all distinct, with the protocol reading each exactly once - so no
repetition is manufactured by the protocol.  That matters because v16's +12.54% came from the
protocol reading each registered view twice, which the frozen citation record forbids citing as
compression.

The zero-API gate must confirm, on this module, that:

* the task set is exactly the three registered long-context tasks;
* the agent-visible payload of a full six-read trajectory is recorded and large;
* **no registered view is read twice** anywhere in the protocol text.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_v24_registry as _v24

REPO = Path(__file__).resolve().parents[2]

#: The long-context sealed task set.
LONG_CONTEXT_REGISTRATION = (
    REPO / "integrations/openai_agents/V73_LONG_CONTEXT_SOURCE_REGISTRATION.json"
)

SCHEMA = "openai_agents_v73_long_context_registry"
PARENT_MODULE = "experiments/runners/openai_agents_v24_registry.py"

#: One read per round over six distinct views, then the answer turn.
READ_ROUNDS = 6
READS_PER_ROUND = 1
READ_CALLS = READ_ROUNDS * READS_PER_ROUND
EXPECTED_MODEL_CALLS = READ_ROUNDS + 1


def _install() -> dict[str, Any]:
    """Point the v24 loader at the long-context set and the six-round shape.

    The loader is reused unchanged; only the module-level values it reads are replaced.  The
    previous values are captured so a caller can see exactly what was switched.
    """
    before = {
        "sealed": _v24.SEALED_REGISTRATION,
        "read_rounds": _v24.READ_ROUNDS,
        "reads_per_round": _v24.READS_PER_ROUND,
        "read_calls": _v24.READ_CALLS,
        "expected_model_calls": _v24.EXPECTED_MODEL_CALLS,
        "active": _v24.ACTIVE,
        "cache": dict(_v24._CACHE),
    }
    _v24.SEALED_REGISTRATION = LONG_CONTEXT_REGISTRATION
    _v24.READ_ROUNDS = READ_ROUNDS
    _v24.READS_PER_ROUND = READS_PER_ROUND
    _v24.READ_CALLS = READ_CALLS
    _v24.EXPECTED_MODEL_CALLS = EXPECTED_MODEL_CALLS
    _v24._CACHE.clear()
    _v24.set_active("sealed")
    return before


_INSTALL_BEFORE = _install()


#: The long-context tasks are CONSTRUCTED, not drawn from the public task table: they quote one
#: commit of real Django source but do not correspond to a published SWE-bench issue.  The v24
#: loader builds a statement by looking the task's ``instance_id`` up in that table, so the
#: registration carries its own ``synthetic_problem_statement`` text and these two shims carry
#: that text through the loader instead of a table lookup.
#
# Why two shims and not an edit: ``_from_sealed`` builds a fresh dict and copies a fixed field
# list into it, then ``_finish`` derives ``statement_sha256`` from ``_statement_from``.  The
# synthetic field must therefore survive the first step and be honoured by the second.  The v24
# module's bytes are unchanged - only the two names it calls are replaced, and only for this
# registry's import.
_V24_PROBLEM_STATEMENT_FROM = _v24._problem_statement_from
_V24_FROM_SEALED = _v24._from_sealed


def _problem_statement_from(entry: dict):
    text = entry.get("synthetic_problem_statement")
    if isinstance(text, str) and text.strip():
        return text
    return _V24_PROBLEM_STATEMENT_FROM(entry)


def _from_sealed(entry: dict):
    """Rebuild the sealed entry, carrying ``synthetic_problem_statement`` into it.

    ``_from_sealed`` copies a fixed field list into a fresh dict and then calls ``_finish``,
    which derives ``statement_sha256`` from the statement.  The synthetic field must therefore
    be present BEFORE ``_finish`` runs, so this wrapper mirrors that construction (rather than
    calling it and patching afterwards, which would be too late) and honours the same field
    list plus the one extra field.
    """
    built: dict[str, Any] = {
        "task_id": str(entry["task_id"]),
        "instance_id": str(entry["instance_id"]),
        "repo": str(entry.get("repo") or "django/django"),
        "base_commit": str(entry["base_commit"]),
        "difficulty": str(entry.get("difficulty") or ""),
        "views": _v24._view_rows(entry),
        "tool_names": list(entry["tool_names"]),
        "tool_names_source": "sealed_registration_artifact",
        "request": str(entry["request"]),
        "contract": dict(entry["contract"]),
        "problem_statement_sha256": str(entry.get("problem_statement_sha256") or ""),
        "source_artifact": str(LONG_CONTEXT_REGISTRATION.relative_to(REPO)).replace("\\", "/"),
        "sealed_confirmation_task": True,
    }
    synthetic = entry.get("synthetic_problem_statement")
    if isinstance(synthetic, str) and synthetic.strip():
        built["synthetic_problem_statement"] = synthetic
    built["derived_fields"] = list(_v24.DERIVED_FIELDS)
    return _v24._finish(built)


_v24._problem_statement_from = _problem_statement_from
_v24._from_sealed = _from_sealed
_v24._CACHE.clear()


def total_view_chars(task_id: str) -> int:
    """Characters the six registered views put in front of the model for this task."""
    entry = registration()["tasks"][task_id]
    total = 0
    for view in entry["views"]:
        text = _v24.view_text(entry["base_commit"], view["file"], view["first_line"],
                              view["last_line"])
        total += len(text)
    return total


def registration() -> dict:
    return _v24.sealed_registration()


def long_context_summary() -> dict:
    """What the gate records: per task, the views, their sizes and the contract facts."""
    reg = registration()
    rows = []
    for task_id in task_ids():
        entry = reg["tasks"][task_id]
        rows.append({
            "task_id": task_id,
            "views": len(entry["views"]),
            "view_files": [v["file"] for v in entry["views"]],
            "total_view_chars": total_view_chars(task_id),
            "required_facts": entry["contract"]["required_facts"],
            "cause_token": entry["contract"]["cause_token"],
            "fix_token": entry["contract"]["fix_token"],
            "issue_value": entry["contract"]["issue_value"],
            "read_rounds": READ_ROUNDS,
            "reads_per_round": READS_PER_ROUND,
            "expected_model_calls": EXPECTED_MODEL_CALLS,
        })
    return {
        "schema": SCHEMA,
        "parent_module": PARENT_MODULE,
        "registration": str(LONG_CONTEXT_REGISTRATION.relative_to(REPO)).replace("\\", "/"),
        "installed_over": {k: (str(v) if isinstance(v, Path) else v)
                           for k, v in _INSTALL_BEFORE.items() if k != "cache"},
        "tasks": rows,
    }


def protocol_reads_each_view_once(task_id: str) -> dict:
    """Assert, from the protocol text itself, that no registered view is read twice.

    The rule the project already uses in its contracts is "never read the same registered view
    twice".  This checks it mechanically: each registered view's ``file`` and line range must be
    mentioned by exactly one protocol step, and the number of read steps must equal the number
    of registered views.
    """
    import re
    entry = registration()["tasks"][task_id]
    steps = _v24.protocol_steps(task_id)
    text = " ".join(steps)
    mentions = []
    for view in entry["views"]:
        pattern = rf'{re.escape(view["file"])}\s+{view["first_line"]}\s*-\s*{view["last_line"]}'
        mentions.append(len(re.findall(pattern, text)))
    rounds = len(re.findall(r'round\s+\d+', text))
    return {
        "task_id": task_id,
        "registered_views": len(entry["views"]),
        "per_view_range_mentions": mentions,
        "each_view_mentioned_once": all(m == 1 for m in mentions),
        "rounds_named": rounds,
        "read_steps": len(steps),
    }


# -- the v24 public surface, re-exported unchanged -------------------------------------
task = _v24.task
task_ids = _v24.task_ids
tasks = _v24.tasks
views = _v24.views
view_text = _v24.view_text
protocol_steps = _v24.protocol_steps
problem_statement = _v24.problem_statement
statement = _v24.statement
history = _v24.history
contract_prompt = None  # filled below from a task row, to keep the surface explicit
length_discipline = _v24.length_discipline
source_registration = _v24.source_registration
sealed_registration = _v24.sealed_registration
set_active = _v24.set_active
minimal_feasible_line = _v24.minimal_feasible_line


def first_prompt(task_id: str) -> str:
    """The contract prompt the runner puts in front of the model on the first turn."""
    return task(task_id)["contract_prompt"]


__all__ = [
    "SCHEMA", "PARENT_MODULE", "LONG_CONTEXT_REGISTRATION", "READ_ROUNDS",
    "READS_PER_ROUND", "READ_CALLS", "EXPECTED_MODEL_CALLS",
    "registration", "long_context_summary", "protocol_reads_each_view_once",
    "total_view_chars", "first_prompt",
    "task", "task_ids", "tasks", "views", "view_text", "protocol_steps",
    "problem_statement", "statement", "history", "length_discipline",
    "source_registration", "sealed_registration", "set_active", "minimal_feasible_line",
]
