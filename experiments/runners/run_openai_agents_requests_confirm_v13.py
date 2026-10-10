"""Stage C held-out confirmation for `requests-1766` (three arms, three repeats).

Shape is the frozen one: `none` / `pruner_v1` / `native_summary`, one task, three repeats,
nine samples, at most 66 API requests.  The plugin arm installs this task's duplicate-output
filter, which applies the frozen v12 rule to the held-out registration; every other arm and
every budget is the frozen one.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_evidence_requests_v13 import RequestsRecorder
from experiments.runners.openai_agents_requests_duplicate_filter_v13 import (
    RequestsDuplicateFilter,
    mechanism_fingerprint,
)
from experiments.runners.trigger_gate import BudgetTriggeredFilter

FREEZE_ENV = "DSH_V13_FREEZE"
DEFAULT_FREEZE = "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
BATCH_ID = "openai-repo-diagnostic-v13-requests1766-confirm-01"
METHODS = ("none", "pruner_v1", "native_summary")
REPEATS = 3
MAX_API_REQUESTS = 66
MAX_TURNS = 10
MAX_OUTPUT_TOKENS = 1024
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000
KIND = "openai_agents_requests1766_confirmation_v13"

from experiments.runners.run_openai_agents_requests_acquisition_v13 import (  # noqa: E402
    DISCLOSURE,
    _CaseWithTaskStatement,
    build_case as _build_acquisition_case,
)

build_case = _build_acquisition_case


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install this task's duplicate-output filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    wrapper = original(method, **kwargs)
    retention = RequestsDuplicateFilter(
        task=str(case.scenario),
        task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    if isinstance(wrapper, BudgetTriggeredFilter):
        wrapper.inner = retention
        return v8._GateAware(wrapper, retention)
    return retention


def freeze_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / os.environ.get(FREEZE_ENV, DEFAULT_FREEZE)


def forced_args(args: list[str]) -> list[str]:
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
            "--max-summary-calls",
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
        "--scenarios", registry.TASK_ID,
        "--experiment-id", BATCH_ID,
        "--max-api-requests", str(MAX_API_REQUESTS),
        "--max-turns", str(MAX_TURNS),
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS),
        "--provider-soft", str(PROVIDER_SOFT),
        "--provider-target", str(PROVIDER_TARGET),
        "--provider-hard", str(PROVIDER_HARD),
        # The harness refuses to start when its own worst-case bound
        # (63 model calls + 3 repeats x max_summary_calls) exceeds the frozen 66-request
        # cap.  With the default 16 the bound is 111, so the native arm's summary budget is
        # set to one call per sample, which makes the bound exactly 66 - the frozen cap.
        # Consequence, recorded in the batch manifest and the report: the native arm's cost
        # on this batch is a bounded lower bound of its unbounded cost, and the acceptance
        # line (Track A: plugin vs baseline) does not involve the native arm.
        "--max-summary-calls", "1",
    ]


def amend_manifest(root: Path | None) -> None:
    if root is None:
        return
    path = root / "manifest.json"
    if not path.is_file():
        return
    freeze = freeze_path()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["kind"] = KIND
    manifest["purpose"] = (
        "held-out confirmation of the duplicate-output mechanism on psf__requests-1766: "
        "one task, three repeats, three arms. Track A is the acceptance track; Track B is a "
        "diagnostic wording track and never a quality-equivalence claim."
    )
    manifest["citable_as_saving"] = False
    manifest["citable_as_quality_equivalence"] = False
    manifest["citable_note"] = (
        "one task and three repeats are not multi-task stability; the citation flags stay "
        "false until the frozen acceptance line is met and confirmed on more tasks"
    )
    manifest["data_class"] = "public_source_diagnostic"
    repo_root = Path(__file__).resolve().parents[2]
    resolved_freeze = freeze if freeze.is_absolute() else (repo_root / freeze)
    manifest["freeze_path"] = (
        str(resolved_freeze.relative_to(repo_root)).replace("\\", "/")
        if resolved_freeze.is_relative_to(repo_root)
        else str(resolved_freeze)
    )
    manifest["freeze_sha256"] = hashlib.sha256(freeze.read_bytes()).hexdigest()
    manifest["mechanism"] = {
        "arm": "pruner_v1",
        "name": "exact_duplicate_output_v13_requests1766",
        "schema": "exact_duplicate_requests_v13",
        "rule": (
            "the frozen v12 rule: an older tool output whose text is byte-identical to a "
            "newer one is replaced by a pointer naming the newest call id and the source "
            "sha256; the newest full copy and every call/output item stay"
        ),
        "frozen_rule_module": "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
        "integration_module": (
            "experiments/runners/openai_agents_requests_duplicate_filter_v13.py"
        ),
        "mechanism_fingerprint": mechanism_fingerprint(),
    }
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
        "acceptance_line": (
            "track A plugin strict success >= baseline AND paired complete-total provider "
            "token saving >= 3%"
        ),
    }
    manifest["request_budget"] = {
        "max_api_requests": MAX_API_REQUESTS,
        "requests_per_repeat": 24,
        "minimum_planned_requests": REPEATS * len(METHODS) * registry.EXPECTED_MODEL_CALLS,
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
        hard_bytes = int(budget.hard_limit_tokens) * 4
    except SystemExit:
        output_root = None

    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "build_retentive_filter": v8.build_retentive_filter,
    }
    base.SCENARIOS = (registry.TASK_ID,)
    base.build_case = build_case
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    v8.build_retentive_filter = build_retentive_filter
    try:
        with v8.evidence_wiring(
            output_root, hard_bytes, recorder_factory=RequestsRecorder
        ):
            status = base.main(cleaned)
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        v8.build_retentive_filter = original["build_retentive_filter"]
    amend_manifest(output_root)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
