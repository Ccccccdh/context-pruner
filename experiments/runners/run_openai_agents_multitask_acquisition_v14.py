"""Bounded per-task payload acquisition for the v14 held-out tasks.

One task, one arm (`none`), one repeat, at most 12 API requests per batch, one batch per task.
The batch exists to record the task's real model inputs so the zero-API replay gate can
reproduce them, and its manifest says so and carries the two false citation flags - written by
this runner in a single pass, with no post-run amendment tool.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agents import function_tool

from experiments.runners import openai_agents_multitask_chain_v14 as chain
from experiments.runners import openai_agents_multitask_registry_v14 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

KIND = "openai_agents_multitask_acquisition_v14"
MAX_API_REQUESTS = 12
MAX_TURNS = 10
MAX_OUTPUT_TOKENS = 1024
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000
DEFAULT_FREEZE = "integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json"
DISCLOSURE = (
    "Public SWE-bench issue statements and three read-only ranges of public baseline source "
    "at the pinned base commits are sent through real SDK tools. No reference patch, "
    "evaluation test patch, local secret or expected answer is sent."
)


class _CaseWithTaskStatement:
    def __init__(self, case: Any, task_statement: str) -> None:
        self.__dict__["_wrapped"] = case
        self.__dict__["task_statement"] = str(task_statement)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_wrapped"], name)


def tools_for(task_id: str) -> list[Any]:
    built = []
    for index, name in enumerate(registry.task(task_id)["tool_names"]):
        doc = registry.task(task_id)["tool_docs"][index]

        def make(index: int = index, doc: str = doc, name: str = name):
            @function_tool(name_override=name, description_override=doc)
            def reader() -> str:
                return registry.source_view(task_id, index)

            return reader

        built.append(make())
    return built


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


def task_option(args: list[str]) -> str:
    if "--task" not in args:
        raise SystemExit("--task <task_id> is required for an acquisition batch")
    index = args.index("--task")
    if index + 1 >= len(args):
        raise SystemExit("--task needs a value")
    task_id = args[index + 1]
    if task_id not in registry.tasks():
        raise SystemExit(f"unknown task id: {task_id}")
    return task_id


def forced_args(args: list[str], task_id: str, batch_id: str) -> list[str]:
    cleaned: list[str] = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item in (
            "--task", "--methods", "--repeats", "--scenarios", "--experiment-id",
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
        "--methods", "none",
        "--repeats", "1",
        "--scenarios", task_id,
        "--experiment-id", batch_id,
        "--max-api-requests", str(MAX_API_REQUESTS),
        "--max-turns", str(MAX_TURNS),
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS),
        "--provider-soft", str(PROVIDER_SOFT),
        "--provider-target", str(PROVIDER_TARGET),
        "--provider-hard", str(PROVIDER_HARD),
        "--max-summary-calls", "1",
    ]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    task_id = task_option(args)
    problems = registry.verify(task_id)
    if problems:
        raise SystemExit("v14 registration did not verify: " + "; ".join(problems))
    batch_id = f"openai-repo-diagnostic-v14-{task_id}-acquisition-01"
    freeze = Path(DEFAULT_FREEZE)
    cleaned = forced_args(args, task_id, batch_id)
    parsed = base.build_parser().parse_args(cleaned)
    output_root = Path(parsed.out) / parsed.experiment_id
    budget, _thresholds = base.resolve_budget(parsed)
    hard_bytes = int(budget.hard_limit_tokens) * 4

    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
    }
    base.SCENARIOS = (task_id,)
    base.build_case = lambda scenario, repeat: build_case(task_id, repeat)
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
            f"payload acquisition for the held-out task {task_id}: record the real model "
            "inputs at every boundary. This batch is single-arm and is neither saving evidence "
            "nor quality-equivalence evidence."
        ),
        batch_id=batch_id,
        freeze=freeze,
        task_ids=(task_id,),
        methods=("none",),
        repeats=1,
        max_api_requests=MAX_API_REQUESTS,
        max_summary_calls_per_sample=1,
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
