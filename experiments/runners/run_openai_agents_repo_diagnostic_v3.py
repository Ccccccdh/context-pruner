"""Public-source task-anchor candidate with v2 input evidence."""

from __future__ import annotations

from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v2 as v2
from experiments.runners.openai_agents_task_anchor_v3 import TaskAnchorFilter
from experiments.runners.trigger_gate import BudgetTriggeredFilter


def build_anchored_filter(original, method: str, **kwargs):
    candidate = original(method, **kwargs)
    if method == 'pruner_v1':
        if isinstance(candidate, BudgetTriggeredFilter):
            candidate.inner = TaskAnchorFilter(candidate.inner, count=2)
        else:
            candidate = TaskAnchorFilter(candidate, count=2)
    return candidate


def main(argv=None) -> int:
    original = base._build_filter

    def anchored_filter(method: str, **kwargs):
        return build_anchored_filter(original, method, **kwargs)

    base._build_filter = anchored_filter
    try:
        return v2.main(argv)
    finally:
        base._build_filter = original


if __name__ == '__main__':
    raise SystemExit(main())
