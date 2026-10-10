"""Stage A payload acquisition for the held-out `requests-1766` task.

One arm (`none`), one task, one repeat, at most 12 API requests.  It exists to record the
task's **real model inputs** - per boundary structure, bytes, hashes and per-output source
hashes - so that the zero-API replay gate has something recorded to reproduce.  It carries no
plugin arm, so it produces no cost or quality evidence for the mechanism, and its manifest
must say so in the three keys the freeze requires.

The runner deliberately *rewrites* the arm/matrix/limit arguments to the frozen values: the
batch shape is not a caller decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from agents import function_tool

from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_evidence_requests_v13 import RequestsRecorder

FREEZE_ENV = "DSH_V13_FREEZE"
DEFAULT_FREEZE = "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
BATCH_ID = "openai-repo-diagnostic-v13-requests1766-acquisition-01"
MAX_API_REQUESTS = 12
MAX_TURNS = 10
MAX_OUTPUT_TOKENS = 1024
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000

KIND = "openai_agents_requests1766_acquisition_v13"
DISCLOSURE = (
    "The public issue text of psf__requests-1766 and three read-only ranges of the public "
    "baseline requests source at the pinned base commit are sent through real SDK tools. No "
    "reference patch, evaluation test patch, local secret or expected answer is sent."
)


@function_tool
def read_requests_digest_auth() -> str:
    """Read the public baseline Digest auth header builder (requests/auth.py)."""
    return registry.source_view(0)


@function_tool
def read_requests_prepare_auth() -> str:
    """Read the public baseline prepared-request auth step (requests/models.py)."""
    return registry.source_view(1)


@function_tool
def read_requests_session_auth() -> str:
    """Read the public baseline session auth merge (requests/sessions.py)."""
    return registry.source_view(2)


TOOLS = [read_requests_digest_auth, read_requests_prepare_auth, read_requests_session_auth]


class _CaseWithTaskStatement:
    """The case plus the registered statement the retention mechanism reads."""

    def __init__(self, case: Any, task_statement: str) -> None:
        self.__dict__["_wrapped"] = case
        self.__dict__["task_statement"] = str(task_statement)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_wrapped"], name)


def build_case(scenario: str, repeat: int):
    case = base.ApiCase(
        scenario=registry.TASK_ID,
        repeat=repeat,
        codename=registry.ISSUE_ID,
        history=registry.history(),
        tools=TOOLS,
        expected_terms=registry.REQUIRED_TERMS,
        expected_tool_names=registry.TOOL_NAMES,
        expected_model_calls=registry.EXPECTED_MODEL_CALLS,
        final_contract=registry.FINAL_CONTRACT,
        answer_pattern=registry.ANSWER_PATTERN,
        allow_repeat_tools=True,
        disable_thinking=True,
    )
    return _CaseWithTaskStatement(case, registry.statement())


def freeze_path() -> Path:
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    return root / os.environ.get(FREEZE_ENV, DEFAULT_FREEZE)


def forced_args(args: list[str]) -> list[str]:
    """Rewrite the caller's arguments to the frozen batch shape."""
    cleaned: list[str] = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item in (
            "--methods", "--repeats", "--scenarios", "--experiment-id",
            "--max-api-requests", "--max-turns", "--max-output-tokens",
            "--provider-soft", "--provider-target", "--provider-hard",
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
        "--scenarios", registry.TASK_ID,
        "--experiment-id", BATCH_ID,
        "--max-api-requests", str(MAX_API_REQUESTS),
        "--max-turns", str(MAX_TURNS),
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS),
        "--provider-soft", str(PROVIDER_SOFT),
        "--provider-target", str(PROVIDER_TARGET),
        "--provider-hard", str(PROVIDER_HARD),
    ]


def amend_manifest(root: Path | None) -> None:
    """Write the frozen manifest keys, including the two citation flags."""
    if root is None:
        return
    path = root / "manifest.json"
    if not path.is_file():
        return
    freeze = freeze_path()
    if not freeze.is_file():
        raise RuntimeError(f"{FREEZE_ENV} does not identify the frozen protocol file")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["kind"] = KIND
    manifest["purpose"] = (
        "payload acquisition for the held-out psf__requests-1766 task: record the real model "
        "inputs at every boundary. This batch is single-arm and is neither saving evidence "
        "nor quality-equivalence evidence."
    )
    manifest["citable_as_saving"] = False
    manifest["citable_as_quality_equivalence"] = False
    manifest["data_class"] = "public_source_diagnostic"
    manifest["freeze_path"] = str(freeze)
    manifest["freeze_sha256"] = hashlib.sha256(freeze.read_bytes()).hexdigest()
    manifest["v13_registry"] = registry.manifest_block()
    manifest["task_registration_problems"] = registry.verify()
    manifest["held_out_task"] = {
        "instance_id": registry.ISSUE_ID,
        "repo": registry.REPO,
        "base_commit": registry.BASE_COMMIT,
        "views": [entry[1] for entry in registry.sources()],
        "read_protocol": list(registry.PROTOCOL_STEPS),
        "expected_model_calls": registry.EXPECTED_MODEL_CALLS,
    }
    manifest["quality_contract"] = {
        "track_A_pattern": registry.ANSWER_PATTERN,
        "track_A_required_terms": list(registry.REQUIRED_TERMS),
        "track_A_max_characters": registry.MAX_ANSWER_CHARS,
        "track_B_max_characters": registry.TRACK_B_MAX_CHARS,
        "track_B_forbidden_literals": list(registry.TRACK_B_FORBIDDEN),
    }
    manifest["request_budget"] = {
        "max_api_requests": MAX_API_REQUESTS,
        "minimum_planned_requests": 1 * 1 * registry.EXPECTED_MODEL_CALLS,
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    problems = registry.verify()
    if problems:
        raise SystemExit("v13 registration did not verify: " + "; ".join(problems))
    if not freeze_path().is_file():
        raise SystemExit(f"the frozen protocol is missing: {freeze_path()}")
    cleaned = forced_args(args)
    output_root = None
    hard_bytes = 0
    try:
        parsed = base.build_parser().parse_args(cleaned)
        output_root = Path(parsed.out) / parsed.experiment_id
        budget, _thresholds = base.resolve_budget(parsed)
        # The frozen filter ceiling is the calibrated hard budget in bytes (16304 * 4), the
        # same convention the v5-v12 batches recorded.
        hard_bytes = int(budget.hard_limit_tokens) * 4
    except SystemExit:
        output_root = None

    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "build_report": base.build_report,
    }
    base.SCENARIOS = (registry.TASK_ID,)
    base.build_case = build_case
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    # The shared report step prints a paired comparison, which a single-arm acquisition
    # batch does not have: without this, a completed batch crashes in its own summary after
    # every artifact was already written.  The patch is runtime-only and fills the missing
    # keys with explicit zeros plus a note.
    def single_arm_report(samples):
        report = original["build_report"](samples)
        paired = report.get("paired")
        if isinstance(paired, dict) and "paired_n" not in paired:
            report["paired"] = {
                "paired_n": 0,
                "actual_input_savings_rate_vs_baseline": 0.0,
                "success_delta": 0.0,
                **paired,
            }
            report["paired_note"] = (
                "single-arm payload-acquisition batch: no paired comparison exists, and none "
                "may be reported from this batch"
            )
        return report

    base.build_report = single_arm_report
    try:
        if output_root is None:
            status = base.main(cleaned)
        else:
            with v8.evidence_wiring(
                output_root, hard_bytes, recorder_factory=RequestsRecorder
            ):
                status = base.main(cleaned)
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        base.build_report = original["build_report"]
    amend_manifest(output_root)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
