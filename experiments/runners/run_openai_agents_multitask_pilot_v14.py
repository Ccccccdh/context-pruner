"""Multi-task confirmation pilot for the v14 held-out tasks.

The task set is the admitted set from the pre-registered admission record, and the per-sample
summary-call cap is the pre-registered value (3), whose worst case is inside the frozen request
cap.  The manifest is written by this runner in one pass: purpose, both citation flags, the
freeze hash, the mechanism identity, the registry block, the admission record and the budgets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agents import function_tool

from experiments.runners import openai_agents_multitask_chain_v14 as chain
from experiments.runners import openai_agents_multitask_registry_v14 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.run_openai_agents_multitask_acquisition_v14 import (
    DISCLOSURE,
    _CaseWithTaskStatement,
    tools_for,
)

REPO = registry.REPO
ADMISSION = REPO / "integrations/openai_agents/V14_ADMISSION_20261005.json"
FREEZE = REPO / "integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json"
BATCH_ID = "openai-repo-diagnostic-v14-multitask-confirm-01"
METHODS = ("none", "pruner_v1", "native_summary")
REPEATS = 3
MAX_API_REQUESTS = 300
MAX_SUMMARY_CALLS = 3
MAX_TURNS = 10
MAX_OUTPUT_TOKENS = 1024
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000
KIND = "openai_agents_multitask_confirmation_v14"


def admitted_tasks() -> list[str]:
    record = json.loads(ADMISSION.read_text(encoding="utf-8"))
    admitted = list(record["admitted_task_ids"])
    if len(admitted) < 3:
        raise SystemExit(
            f"refusing to run the pilot: only {len(admitted)} tasks were admitted "
            f"(the rules require at least three); report the shortfall instead of relaxing"
        )
    return admitted


def build_case(task_id: str, repeat: int):
    entry = registry.task(task_id)
    case = base.ApiCase(
        scenario=task_id,
        repeat=int(repeat),
        codename=entry["instance_id"],
        history=registry.history(task_id),
        tools=tools_for(task_id),
        expected_terms=tuple(entry["required_terms"]),
        expected_tool_names=tuple(entry["tool_names"]),
        expected_model_calls=int(entry["expected_model_calls"]),
        final_contract=entry["contract"],
        answer_pattern=entry["answer_pattern"],
        allow_repeat_tools=True,
        disable_thinking=True,
    )
    return _CaseWithTaskStatement(case, registry.statement(task_id))


def forced_args(args: list[str], tasks: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item in (
            "--methods", "--repeats", "--scenarios", "--experiment-id",
            "--max-api-requests", "--max-turns", "--max-output-tokens",
            "--provider-soft", "--provider-target", "--provider-hard", "--max-summary-calls",
        ):
            skip = True
            continue
        cleaned.append(item)
    if "--confirm-send-synthetic-data" in cleaned:
        raise SystemExit("use --confirm-send-public-source for these public repository tasks")
    cleaned = [
        "--confirm-send-synthetic-data" if item == "--confirm-send-public-source" else item
        for item in cleaned
    ]
    return cleaned + [
        "--methods", ",".join(METHODS),
        "--repeats", str(REPEATS),
        "--scenarios", ",".join(tasks),
        "--experiment-id", BATCH_ID,
        "--max-api-requests", str(MAX_API_REQUESTS),
        "--max-turns", str(MAX_TURNS),
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS),
        "--provider-soft", str(PROVIDER_SOFT),
        "--provider-target", str(PROVIDER_TARGET),
        "--provider-hard", str(PROVIDER_HARD),
        "--max-summary-calls", str(MAX_SUMMARY_CALLS),
    ]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    tasks = admitted_tasks()
    problems = {task_id: registry.verify(task_id) for task_id in tasks}
    if any(problems.values()):
        raise SystemExit(f"v14 registration did not verify: {problems}")
    cleaned = forced_args(args, tasks)
    parsed = base.build_parser().parse_args(cleaned)
    output_root = Path(parsed.out) / parsed.experiment_id
    budget, _thresholds = base.resolve_budget(parsed)
    hard_bytes = int(budget.hard_limit_tokens) * 4

    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
    }
    base.SCENARIOS = tuple(tasks)
    base.build_case = build_case
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    try:
        with chain.chain_wiring():
            with v8.evidence_wiring(
                output_root, hard_bytes, recorder_factory=chain.MultitaskRecorder
            ):
                status = base.main(cleaned)
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
    chain.write_manifest(
        output_root,
        kind=KIND,
        purpose=(
            "multi-task confirmation of the duplicate-output mechanism on the admitted "
            "held-out tasks; Track A is the acceptance track and Track B is diagnostic only"
        ),
        batch_id=BATCH_ID,
        freeze=FREEZE,
        task_ids=tasks,
        methods=METHODS,
        repeats=REPEATS,
        max_api_requests=MAX_API_REQUESTS,
        max_summary_calls_per_sample=MAX_SUMMARY_CALLS,
        admission=json.loads(ADMISSION.read_text(encoding="utf-8")),
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
