"""Frozen acceptance criteria for the r20 order-free task family (zero API).

These are the criterion ids the pre-registration names.  They are deliberately **not**
quality gates: on these tasks the decision depends on the evidence SET, so "did it read
everything" is a quality question while "how often / in what order" is an experimental
control quantity.

* ``CRIT_R20_COVERAGE``  -- every frozen evidence source appears at least once in the host's
  recorded tool trace.  This one **is** part of the quality side: a source that was never
  read cannot have contributed its facts.
* ``CRIT_R20_REPEAT_REPORT`` -- per-unit repeat counts and call counts, reported, never a
  gate.
* ``CRIT_R20_SAME_WORK_REPORT`` -- the same-work subset (equal call count and equal trace),
  reported, never a gate.

Everything is computed from the recorded host trace (``first_role_trace``), never from model
prose, and the module imports nothing from a runner.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

CRIT_COVERAGE = "CRIT_R20_COVERAGE"
CRIT_REPEAT = "CRIT_R20_REPEAT_REPORT"
CRIT_SAME_WORK = "CRIT_R20_SAME_WORK_REPORT"
CRITERION_IDS = (CRIT_COVERAGE, CRIT_REPEAT, CRIT_SAME_WORK)

#: Criterion ids that participate in a quality decision.  Only coverage does; the other two
#: are report items, and a batch that turned them into gates would recreate the r17 failure.
QUALITY_CRITERIA = (CRIT_COVERAGE,)


def coverage(expected: Sequence[str], executed: Sequence[str]) -> dict:
    """At-least-once reading of every frozen evidence source."""
    counts = Counter(str(x) for x in executed)
    missing = [str(name) for name in expected if counts.get(str(name), 0) < 1]
    return {
        "criterion": CRIT_COVERAGE,
        "satisfied": not missing,
        "missing_sources": missing,
        "read_counts": {str(name): counts.get(str(name), 0) for name in expected},
    }


def repeat_report(expected: Sequence[str], executed: Sequence[str]) -> dict:
    """Repeats and call counts: a report item, never a gate."""
    counts = Counter(str(x) for x in executed)
    repeats = {name: n for name, n in counts.items() if n > 1}
    unexpected = [name for name in counts if name not in set(map(str, expected))]
    return {
        "criterion": CRIT_REPEAT,
        "call_count": len(list(executed)),
        "expected_count": len(list(expected)),
        "repeat_counts": repeats,
        "repeat_calls": sum(n - 1 for n in repeats.values()),
        "unexpected_tools": unexpected,
        "gate": False,
    }


def same_work_report(baseline: Mapping[str, object], plugin: Mapping[str, object]) -> dict:
    """Whether the pair did the same work, by call count and trace.  Report only."""
    base_calls = int(baseline.get("api_request_attempts", 0) or 0)
    plugin_calls = int(plugin.get("api_request_attempts", 0) or 0)
    same_trace = list(baseline.get("first_role_trace") or []) == list(
        plugin.get("first_role_trace") or []
    )
    same_calls = base_calls == plugin_calls
    return {
        "criterion": CRIT_SAME_WORK,
        "baseline_calls": base_calls,
        "plugin_calls": plugin_calls,
        "same_call_count": same_calls,
        "same_tool_trace": same_trace,
        "same_work": same_calls or same_trace,
        "resend_overrun_calls": max(0, plugin_calls - base_calls),
        "gate": False,
    }


def audit_units(rows: Sequence[Mapping[str, object]]) -> dict:
    """Every criterion applied to a recorded batch, per unit plus batch totals."""
    units = []
    for row in rows:
        expected = [str(x) for x in (row.get("expected_first_role_trace") or [])]
        executed = [str(x) for x in (row.get("first_role_trace") or [])]
        units.append(
            {
                "task_id": str(row.get("task_id")),
                "repeat": int(row.get("repeat", 0)),
                "arm": str(row.get("method")),
                CRIT_COVERAGE: coverage(expected, executed),
                CRIT_REPEAT: repeat_report(expected, executed),
            }
        )
    covered = sum(1 for unit in units if unit[CRIT_COVERAGE]["satisfied"])
    return {
        "criterion_ids": list(CRITERION_IDS),
        "quality_criteria": list(QUALITY_CRITERIA),
        "units": units,
        "batch": {
            "units": len(units),
            "coverage_satisfied": covered,
            "coverage_satisfied_percent": (
                round(100.0 * covered / len(units), 2) if units else None
            ),
            "repeat_calls_total": sum(
                unit[CRIT_REPEAT]["repeat_calls"] for unit in units
            ),
        },
    }


__all__ = [
    "CRITERION_IDS",
    "CRIT_COVERAGE",
    "CRIT_REPEAT",
    "CRIT_SAME_WORK",
    "QUALITY_CRITERIA",
    "audit_units",
    "coverage",
    "repeat_report",
    "same_work_report",
]
