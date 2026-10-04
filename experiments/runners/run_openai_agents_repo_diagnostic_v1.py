"""OpenAI Agents development diagnostics from public repository baselines.

Only issue statements and fixed ranges of baseline source are exposed through
tools. Reference patches and host test patches are never read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents import function_tool

from experiments.runners import run_openai_agents_api_experiment as base


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / ".tooling" / "upstream"
DATASET = ROOT / ".tooling" / "swebench-verified"
TASKS = ("pytest_mro", "pylint_regex_csv", "django_count_annotations")

# File ranges are fixed before any model run. These are baseline files, never
# reference patches or host tests. Source and issue hashes are checked by the
# zero-API gate and will be frozen with the confirmation protocol.
SOURCES = {
    "pytest_mro": (
        ("pytest-10356", "src/_pytest/mark/structures.py", 338, 363),
        ("pytest-10356", "src/_pytest/mark/structures.py", 365, 392),
        ("pytest-10356", "testing/test_mark.py", 540, 589),
    ),
    "pylint_regex_csv": (
        ("pylint-8898", "pylint/checkers/base/name_checker/checker.py", 225, 237),
        ("pylint-8898", "pylint/config/argument.py", 45, 150),
        ("pylint-8898", "pylint/utils/utils.py", 205, 267),
    ),
    "django_count_annotations": (
        ("django-16263", "django/db/models/query.py", 610, 632),
        ("django-16263", "django/db/models/sql/query.py", 438, 527),
        ("django-16263", "django/db/models/sql/query.py", 542, 560),
    ),
}

_META = {
    "pytest_mro": {
        "dataset": "pytest-10356", "id": "pytest-dev__pytest-10356",
        "request": "Diagnose why a class inheriting two marked bases misses a mark. Read the three baseline source views in order; identify the read mechanism and necessary ownership rule.",
        "names": ("read_pytest_mark_decorator", "read_pytest_mark_storage", "read_pytest_mark_context"),
        "terms": ("getattr", "__dict__", "MRO"),
        "contract": "RESULT issue=pytest-10356 cause=<mechanism> fix=<ownership_rule>",
        "pattern": r"RESULT issue=pytest-10356 cause=.*getattr.* fix=.*__dict__.*MRO.*",
    },
    "pylint_regex_csv": {
        "dataset": "pylint-8898", "id": "pylint-dev__pylint-8898",
        "request": "Diagnose why a valid regex quantifier containing a comma is split. Read the option, transformer, and CSV helper views in order; identify where the split happens and its required boundary.",
        "names": ("read_pylint_option", "read_pylint_transformer", "read_pylint_csv_helper"),
        "terms": ("_splitstrip", "quantifier"),
        "contract": "RESULT issue=pylint-8898 cause=<split_location> fix=<comma_boundary_rule>",
        "pattern": r"RESULT issue=pylint-8898 cause=.*_splitstrip.* fix=.*(?:quantifier|brace).*",
    },
    "django_count_annotations": {
        "dataset": "django-16263", "id": "django__django-16263",
        "request": "Diagnose why count() retains an unused annotation. Read the count entry, aggregation decision, and count call views in order; state what must be preserved when pruning.",
        "names": ("read_django_count_entry", "read_django_aggregation", "read_django_count_call"),
        "terms": ("existing_annotations", "subquery", "referenced"),
        "contract": "RESULT issue=django-16263 cause=<decision> fix=<pruning_guard>",
        "pattern": r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
    },
}


def source_view(task: str, index: int) -> str:
    repo, relative, first, last = SOURCES[task][index]
    path = UPSTREAM / repo / relative
    lines = path.read_text(encoding="utf-8").splitlines()
    return f"{repo}/{relative} (baseline)\n" + "\n".join(
        f"{number}: {lines[number - 1]}" for number in range(first, min(last, len(lines)) + 1)
    )


@function_tool
def read_pytest_mark_decorator() -> str:
    """Read the public pytest baseline mark decorator and unpacking code."""
    return source_view("pytest_mro", 0)


@function_tool
def read_pytest_mark_storage() -> str:
    """Read the public pytest baseline mark storage code."""
    return source_view("pytest_mro", 1)


@function_tool
def read_pytest_mark_context() -> str:
    """Read existing public pytest baseline marker inheritance tests."""
    return source_view("pytest_mro", 2)


@function_tool
def read_pylint_option() -> str:
    """Read the public pylint baseline option declaration."""
    return source_view("pylint_regex_csv", 0)


@function_tool
def read_pylint_transformer() -> str:
    """Read the public pylint baseline regexp CSV transformer."""
    return source_view("pylint_regex_csv", 1)


@function_tool
def read_pylint_csv_helper() -> str:
    """Read the public pylint baseline CSV helper."""
    return source_view("pylint_regex_csv", 2)


@function_tool
def read_django_count_entry() -> str:
    """Read the public Django baseline QuerySet count entry."""
    return source_view("django_count_annotations", 0)


@function_tool
def read_django_aggregation() -> str:
    """Read the public Django baseline SQL aggregation decision."""
    return source_view("django_count_annotations", 1)


@function_tool
def read_django_count_call() -> str:
    """Read the public Django baseline count call."""
    return source_view("django_count_annotations", 2)


TOOLS = {
    "pytest_mro": [read_pytest_mark_decorator, read_pytest_mark_storage, read_pytest_mark_context],
    "pylint_regex_csv": [read_pylint_option, read_pylint_transformer, read_pylint_csv_helper],
    "django_count_annotations": [read_django_count_entry, read_django_aggregation, read_django_count_call],
}


def issue_text(task: str) -> str:
    dataset = _META[task]["dataset"]
    statement = json.loads((DATASET / dataset / "instance.json").read_text(encoding="utf-8"))["problem_statement"]
    # The pytest record repeats its issue text and includes a PR checklist.
    if task == "pytest_mro":
        statement = statement.split("\nConsider MRO when obtaining marks", 1)[0]
    return statement


def input_hashes() -> dict[str, str]:
    paths = {DATASET / _META[task]["dataset"] / "instance.json" for task in TASKS}
    paths.update(UPSTREAM / repo / relative for task in TASKS for repo, relative, _, _ in SOURCES[task])
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def build_case(task: str, repeat: int) -> base.ApiCase:
    meta = _META[task]
    return base.ApiCase(
        scenario=task, repeat=repeat, codename=meta["id"],
        history=[
            {"role": "user", "content": f"Public issue {meta['id']}:\n{issue_text(task)}"},
            {"role": "user", "content": meta["request"] + f" Issue: {meta['id']}."},
        ],
        tools=TOOLS[task], expected_terms=meta["terms"],
        expected_tool_names=meta["names"], expected_model_calls=4,
        final_contract=meta["contract"], answer_pattern=meta["pattern"],
        allow_repeat_tools=True, disable_thinking=True,
    )


def main(argv=None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if "--confirm-send-synthetic-data" in args:
        raise SystemExit("use --confirm-send-public-source for these public repository tasks")
    if "--confirm-send-public-source" in args:
        args[args.index("--confirm-send-public-source")] = "--confirm-send-synthetic-data"
    base.SCENARIOS = TASKS
    base.build_case = build_case
    base.SYNTHETIC_DISCLOSURE = (
        "Public SWE-bench issue statements and selected public baseline source "
        "ranges are sent through real SDK tools. No reference patches, host test "
        "patches, local secrets, or expected answers are sent."
    )
    return base.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
