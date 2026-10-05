"""Registration for the v8 long-baseline task.

Why a new task id
-----------------
v7 measured only one side of the applicability boundary: on the frozen Django task the
baseline finishes in K = 2 model calls, so with ``RECENT_TURNS_KEPT = 1`` the plugin has
``max(0, K - 1 - N) = 0`` elidable turns and its payload is byte-identical to the
baseline's.  The other half of the proposition - *K > N + 1 should let the plugin win* -
needs a baseline with more model calls, and the only honest way to get one on this host
is to **change the task input**, not the mechanism.

``django_long_investigation`` is therefore the same public issue and the same three
public baseline source ranges as ``django_count_annotations``, with a pre-registered
read-only investigation protocol added to the user request: read the three views **in
order**, **one tool call per turn**, and only then answer.  The protocol is task *input*;
the compression mechanism, its thresholds and the budgets are v7's, unchanged.

Registration extends the frozen v5 literal set and the frozen v6 span table for the new
task id, with two checkable additions:

* ``literal_context_units`` - the multi-line reason sentences the issue statement
  already contains (they span lines, so a line-oriented registry could not register
  them);
* ``task_units`` - the concatenated task-statement text units that carry a registered
  literal, taken from the registered statement itself.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from experiments.runners import openai_agents_literal_registry_v6 as base6

#: Schema version of this registration.
REGISTRY_SCHEMA = "openai_agents_long_task_registry_v8"

#: Registered task ids: the new long task, plus the frozen short task it extends.
TASK_ID = "django_long_investigation"
SHORT_TASK_ID = "django_count_annotations"
FROZEN_TASKS = (SHORT_TASK_ID,)
TASKS = (TASK_ID, *FROZEN_TASKS)

#: The read-only investigation protocol appended to the request.  Frozen as one
#: constant so the manifest, the protocol and the audit all quote the same text.
INVESTIGATION_STEPS: tuple[str, ...] = (
    "STEP 1: call read_django_count_entry alone, then stop and wait for its result.",
    "STEP 2: after step 1 returns, call read_django_aggregation alone, then stop and wait.",
    "STEP 3: after step 2 returns, call read_django_count_call alone, then stop and wait.",
    "STEP 4: after step 3 returns, call read_django_count_entry once more alone, then stop and wait.",
    "STEP 5: after step 4 returns, call read_django_aggregation once more alone, then stop and wait.",
    "STEP 6: after step 5 returns, call read_django_count_call once more alone, then stop and wait.",
    "STEP 7: only after all six reads have returned, state the final diagnosis.",
)

#: Multi-line reason sentences the public issue statement already contains.  Registered
#: verbatim so a reduction can never remove the clause a frozen quality contract cites.
LITERAL_CONTEXT_UNITS: dict[str, tuple[str, ...]] = {
    TASK_ID: (
        "It produces the same results as:\nBook.objects.count()",
        (
            "Django could be more intelligent about what annotations to include in the "
            "query produced by queryset.count(), stripping out any annotations that are "
            "not referenced by filters, other annotations or ordering. This should speed "
            "up calls to count() with complex annotations."
        ),
        (
            "There seems to be precedent for this: select_related calls are ignored with "
            "count() queries."
        ),
    ),
    SHORT_TASK_ID: (),
}


def constraints(task: str) -> dict[str, dict[str, Any]]:
    """The registered literal set for one task id.

    The long task reuses the frozen Django literal registration unchanged, so a v8
    sample and a v5/v6/v7 sample protect exactly the same literals.
    """
    if str(task) == TASK_ID:
        return base6.base.CONSTRAINTS[SHORT_TASK_ID]
    return base6.base.CONSTRAINTS[str(task)]


def sources(task: str) -> tuple[Any, ...]:
    """The registered source ranges for one task id (same three ranges)."""
    if str(task) == TASK_ID:
        return base6.base.SOURCES[SHORT_TASK_ID]
    return base6.base.SOURCES[str(task)]


def spans(task: str) -> tuple[Any, ...]:
    """The registered literal line spans for one task id."""
    if str(task) == TASK_ID:
        return base6.spans(SHORT_TASK_ID)
    return base6.spans(str(task))


def span_for_tool(task: str, tool_name: str) -> Any | None:
    for span in spans(task):
        if span.tool == str(tool_name):
            return span
    return None


def literals_for(task: str) -> dict[str, tuple[str, ...]]:
    return {
        label: tuple(str(value) for value in spec["literals"])
        for label, spec in constraints(task).items()
    }


def labels_in_text(task: str, text: str) -> tuple[str, ...]:
    lowered = str(text).lower()
    return tuple(
        label
        for label, phrases in literals_for(task).items()
        if phrases and any(str(phrase).lower() in lowered for phrase in phrases)
    )


def constraint_presence(task: str, searchable: str) -> dict[str, bool]:
    lowered = str(searchable).lower()
    return {
        label: any(str(phrase).lower() in lowered for phrase in phrases)
        for label, phrases in literals_for(task).items()
    }


def literal_unit_fragments(task: str) -> dict[str, tuple[str, ...]]:
    return {
        label: tuple(str(value) for value in spec["units"])
        for label, spec in constraints(task).items()
    }


def task_statement(task: str, issue_text: str, request_text: str = "") -> str:
    """The registered task statement: the public issue plus the frozen procedure."""
    parts = [str(issue_text), "\n".join(INVESTIGATION_STEPS)]
    if request_text:
        parts.append(str(request_text))
    return "\n".join(part for part in parts if part)


def literal_units(task: str, statement: str) -> list[str]:
    """Task-statement units (non-empty stripped lines) carrying a registered literal."""
    fragments = [
        fragment
        for values in literal_unit_fragments(task).values()
        for fragment in values
    ]
    units = []
    for line in str(statement).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(str(fragment).lower() in stripped.lower() for fragment in fragments):
            units.append(stripped)
    return units


def registry_fingerprint(task: str) -> str:
    """Digest of this registration for one task id (audit cross-check)."""
    payload = {
        "schema": REGISTRY_SCHEMA,
        "task": str(task),
        "base_schema": base6.REGISTRY_SCHEMA,
        "base_fingerprint": base6.registry_fingerprint(
            SHORT_TASK_ID if str(task) == TASK_ID else str(task)
        ),
        "constraints": {
            label: {
                "literals": list(spec["literals"]),
                "units": list(spec["units"]),
            }
            for label, spec in constraints(task).items()
        },
        "spans": [
            {
                "tool": span.tool,
                "repo": span.repo,
                "path": span.path,
                "first_line": span.first_line,
                "last_line": span.last_line,
                "literal_lines": list(span.literal_lines),
                "literal_labels": list(span.literal_labels),
            }
            for span in spans(task)
        ],
        "literal_context_units": list(LITERAL_CONTEXT_UNITS.get(str(task), ())),
        "investigation_steps": list(INVESTIGATION_STEPS),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def base_fingerprint(task: str) -> str:
    return base6.registry_fingerprint(SHORT_TASK_ID if str(task) == TASK_ID else str(task))


def verify(task: str, registered_statement: str | None = None) -> list[str]:
    """Registration problems for one task id (empty list means verified)."""
    problems: list[str] = []
    if str(task) == TASK_ID:
        problems.extend(base6.base.verify(SHORT_TASK_ID))
        problems.extend(base6.verify(SHORT_TASK_ID))
        if not spans(task):
            problems.append(f"{task}: no registered spans")
        for span in spans(task):
            if span.literal_labels and not span.literal_lines:
                problems.append(
                    f"{task}/{span.tool}: registered literals have no registered line span"
                )
        if registered_statement is not None:
            units = literal_units(task, registered_statement)
            if not units:
                problems.append(f"{task}: registered statement carries no literal unit")
            missing = [
                fragment
                for values in literal_unit_fragments(task).values()
                for fragment in values
                if str(fragment).lower() not in str(registered_statement).lower()
            ]
            if missing:
                problems.append(
                    f"{task}: registered statement is missing unit fragments {missing}"
                )
            for sentence in LITERAL_CONTEXT_UNITS.get(str(task), ()):
                if str(sentence) not in str(registered_statement):
                    problems.append(
                        f"{task}: registered statement is missing a registered context "
                        f"unit: {str(sentence)[:40]!r}..."
                    )
    else:
        problems.extend(base6.base.verify(str(task)))
        problems.extend(base6.verify(str(task)))
    return problems


def all_problems(tasks: Sequence[str] | None = None) -> list[str]:
    return [problem for task in (tasks or TASKS) for problem in verify(task)]


def manifest_block() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "tasks": list(TASKS),
        "frozen_registry_schema": base6.REGISTRY_SCHEMA,
        "frozen_v5_registry_schema": base6.base.REGISTRY_SCHEMA,
        "investigation_steps": list(INVESTIGATION_STEPS),
        "literal_context_units": {
            task: list(units) for task, units in LITERAL_CONTEXT_UNITS.items()
        },
        "fingerprints": {task: registry_fingerprint(task) for task in TASKS},
        "base_fingerprints": {task: base_fingerprint(task) for task in TASKS},
        "long_task_extends": SHORT_TASK_ID,
    }


__all__ = [
    "FROZEN_TASKS",
    "INVESTIGATION_STEPS",
    "LITERAL_CONTEXT_UNITS",
    "REGISTRY_SCHEMA",
    "SHORT_TASK_ID",
    "TASK_ID",
    "TASKS",
    "all_problems",
    "base6",
    "base_fingerprint",
    "constraint_presence",
    "constraints",
    "labels_in_text",
    "literal_unit_fragments",
    "literal_units",
    "literals_for",
    "manifest_block",
    "registry_fingerprint",
    "span_for_tool",
    "sources",
    "spans",
    "task_statement",
    "verify",
]
