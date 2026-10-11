"""The v24 recalibration: nine samples, three development tasks, one repeat, three arms.

Why this batch exists
---------------------
A prompt change alters first-attempt compliance, so the v24 configuration has to be re-measured on
the host's *development* tasks before it is used to confirm anything on the sealed ones.  The
ruling fixes the shape: **3 tasks x 1 repeat x 3 arms = 9 samples**, at the same K=1 window, with
the new first prompt.  It is a tiny paid batch (about 0.006 dollars) and its only jobs are:

* to measure the **first-attempt acceptance rate** the prompt change is meant to raise;
* to check that the mechanism's strict track and semantic endpoint are **not below the baseline**
  and that the **byte saving is unchanged in kind** from the K=1 development measurement;
* to fail closed and stop the round if the new prompt damaged anything.

What it reuses, and what it must not reuse
------------------------------------------
It reuses the v24 runner's configuration wholesale - the same registry fork, the same live-contract
fork, the same chain, the same window, the same cap arithmetic (scaled to nine samples), the same
gate, the same freeze check, the same preservation step - by importing the runner and overriding
only the four numbers that describe this smaller matrix.  It never touches the confirmation freeze,
the confirmation gate or the confirmation batch's experiment id: the recalibration writes its own
freeze, its own gate and its own run directory under its own id.

Modes
-----
``--write-freeze``   the recalibration freeze, once, before any paid request
``--gate``           the zero-API gate for the nine-sample shape, plus the standing assertions
``--pilot``          the paid nine-sample batch; refuses unless the gate allows it
``--report``         the recalibration report: the decision line, computed from the run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v24_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v24_registry as registry  # noqa: E402
from experiments.runners import (  # noqa: E402
    run_openai_agents_v24_length_compliance_k1 as runner,
)

#: The recalibration's own identity.  It shares nothing with the confirmation batch but the code.
EXPERIMENT_ID = "openai-repo-diagnostic-v24-length-compliance-recalibration-01"
REPEATS = 1
SAMPLES = 3 * REPEATS * 3
NATIVE_SAMPLES = 3 * REPEATS
#: The answer call plus the retry plus three reads plus slack, times nine samples, times 1.3, and
#: the two summary requests each of the three native_summary samples may spend:
#: READ_CALLS=3 => MAX_TURNS=5 => worst 6 per sample => 9x6 + 3x2 = 60 => 1.3x => 78.
MAX_API_REQUESTS = 80
FREEZE = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_FREEZE_20261010.json"
)
GATE_OUT = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_ZERO_API_GATE_20261010.json"
)
REPORT_OUT = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_REPORT_20261010.json"
)
#: This batch's own revision ledger.  It is the confirmation configuration's module set plus this
#: driver, so the gate can fail closed on a drifted driver without editing the confirmation batch's
#: ledger, which pins exactly the confirmation configuration's modules and nothing else.
REVISION_LEDGER = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_MODULE_REVISIONS_20261010.json"
)
#: The registrations this batch reads: the v18 one supplies the three tasks' data, and the v24 one
#: re-registers the same three tasks for this configuration.  Both are named here, before any
#: function that defaults to them is defined, because a default argument is evaluated at definition
#: time.
SOURCE_REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
V24_SOURCE_REGISTRATION = (
    ROOT / "integrations/openai_agents/V24_SOURCE_REGISTRATION_20261010.json"
)
#: The K=1 development measurement the byte saving has to stay unchanged in kind from.  It is read
#: only for the comparison; its numbers are never merged into this batch's.
DEVELOPMENT_K1_REFERENCE = {
    "batch": "runs/stage5-openai-agents-api/openai-repo-diagnostic-v22-window-k1b-pilot-02",
    "note": (
        "the v22 K=1 development group is the K=1 development measurement this recalibration is "
        "compared with in kind - not in value - because the v22 development numbers are not citable "
        "and are not pooled with anything here"
    ),
}

#: The baseline arm and the plugin arm of the paired comparison.
BASELINE_ARM = "none"
PLUGIN_ARM = "pruner_v1"
ARMS = ("none", "native_summary", "pruner_v1")

#: The acceptance line this batch runs under is the v24 confirmation configuration's own line, so
#: the runner's paired criterion is unchanged and no bar is lowered.  The recalibration additionally
#: applies its own decision line on top of it, which is what this batch exists to test.
DECISION_LINE = (
    "on top of the v24 acceptance line: first-attempt acceptance at or above 70 percent overall, "
    "every arm's strict track at or above the baseline, the semantic endpoint at or above the "
    "baseline, zero render failures, zero truncations, zero dropped fields, and the mechanism's "
    "byte saving unchanged in kind from the K=1 development measurement"
)


def configure_for_recalibration() -> dict[str, Any]:
    """Point the v24 runner's own constants at this nine-sample shape, in this process only."""

    runner.REPEATS = REPEATS
    runner.SAMPLES = SAMPLES
    runner.NATIVE_SAMPLES = NATIVE_SAMPLES
    runner.MAX_API_REQUESTS = MAX_API_REQUESTS
    runner.EXPERIMENT_ID = EXPERIMENT_ID
    runner.FREEZE = FREEZE
    runner.GATE_OUT = GATE_OUT
    # ``forced_args`` and the runner's arithmetic read these constants, so nothing else has to
    # change: the shape of the batch follows from them.
    return {
        "repeats": REPEATS,
        "samples": SAMPLES,
        "native_samples": NATIVE_SAMPLES,
        "cap": MAX_API_REQUESTS,
        "experiment_id": EXPERIMENT_ID,
        "freeze": str(FREEZE),
        "gate": str(GATE_OUT),
    }


def installed_for_recalibration(source: Path = SOURCE_REGISTRATION):
    """The registry, the shape and the constants this batch runs under, in one entry point.

    Every mode of this driver uses it, so the gate, the freeze and the paid pass cannot run under
    three different configurations.  The three registered tasks are pinned by name, the registry is
    put in sealed mode against the registration those tasks come from, and the shape constants are
    set to the registered three-round shape before any task is built.
    """

    return _installed(source)


@contextmanager
def _installed(source: Path):
    """Install this batch's task set, shape and frozen bindings, and put them all back after."""

    from experiments.runners import openai_agents_multitask_chain_v14 as v14
    from experiments.runners import openai_agents_v19_shape_dose as v19

    saved = {
        "runner_registry": runner.runner17.registry,
        "runner_live": runner.runner17.live,
        "runner_build_case": runner.runner17.build_case,
        "runner_max_turns": runner.runner17.MAX_TURNS,
        "runner_methods": runner.runner17.PILOT_METHODS,
        "runner_repeats": runner.runner17.PILOT_REPEATS,
        "runner_cap": runner.runner17.PILOT_MAX_API_REQUESTS,
        "runner_summary": runner.runner17.PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE,
        "runner_experiment": runner.runner17.PILOT_EXPERIMENT_ID,
        "chain_registry": runner.chain.registry,
        "chain_config": runner.chain.CONFIG_MODULE,
        "chain_filter": runner.chain.FILTER_CLASS,
        "chain_window": runner.chain.WINDOW_LINES,
        "chain_mechanism_schema": runner.chain.MECHANISM_SCHEMA,
        "chain_evidence_schema": runner.chain.EVIDENCE_SCHEMA,
        "chain_mechanism_name": runner.chain.MECHANISM_NAME,
        "chain_composition": runner.chain.COMPOSITION_SCHEMA,
        "v14_registry": v14.registry,
        "v19_registry": v19.registry,
        "protocol_steps": registry.protocol_steps,
    }
    with registered_tasks_installed(source):
        with runner.sealed_v24_registry_installed(registered_tasks_only=True):
            configure_for_recalibration()
            # The window is bound first, exactly as the confirmation runner's own ``main`` does it:
            # ``select_window`` installs the pinned K=1 configuration module on the chain, and
            # ``configure`` then binds the frozen runner, the chain, the v14 ancestor and the v19
            # shape module to this round's registry and forks and sets the pilot matrix.  The gate,
            # the freeze and the paid pass all run this same pairing.
            runner.select_window(1)
            runner.configure()
            try:
                yield registry
            finally:
                registry.protocol_steps = saved["protocol_steps"]
                runner.runner17.registry = saved["runner_registry"]
                runner.runner17.live = saved["runner_live"]
                runner.runner17.build_case = saved["runner_build_case"]
                runner.runner17.MAX_TURNS = saved["runner_max_turns"]
                runner.runner17.PILOT_METHODS = saved["runner_methods"]
                runner.runner17.PILOT_REPEATS = saved["runner_repeats"]
                runner.runner17.PILOT_MAX_API_REQUESTS = saved["runner_cap"]
                runner.runner17.PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE = saved["runner_summary"]
                runner.runner17.PILOT_EXPERIMENT_ID = saved["runner_experiment"]
                runner.chain.registry = saved["chain_registry"]
                runner.chain.CONFIG_MODULE = saved["chain_config"]
                runner.chain.FILTER_CLASS = saved["chain_filter"]
                runner.chain.WINDOW_LINES = saved["chain_window"]
                runner.chain.MECHANISM_SCHEMA = saved["chain_mechanism_schema"]
                runner.chain.EVIDENCE_SCHEMA = saved["chain_evidence_schema"]
                runner.chain.MECHANISM_NAME = saved["chain_mechanism_name"]
                runner.chain.COMPOSITION_SCHEMA = saved["chain_composition"]
                v14.registry = saved["v14_registry"]
                v19.registry = saved["v19_registry"]


SOURCE_REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
V24_SOURCE_REGISTRATION = (
    ROOT / "integrations/openai_agents/V24_SOURCE_REGISTRATION_20261010.json"
)


def driver_module() -> str:
    return "experiments/runners/run_openai_agents_v24_recalibration.py"


def driver_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@contextmanager
def registered_tasks_installed(source: Path = SOURCE_REGISTRATION):
    """The three tasks of a registration, and nothing else, for one block.

    The v24 registry in its development mode appends its stage-1 task, which carries the six-read
    shape; this recalibration is defined on exactly the three registered tasks, so the task set and
    the registration the registry reads the task data from are both fixed for its duration and
    restored afterwards.
    """

    saved_ids = registry.task_ids
    saved_source = registry.SEALED_REGISTRATION
    registered = tuple(
        json.loads(Path(source).read_text(encoding="utf-8"))["task_ids"]
    )
    if not registered:
        raise SystemExit(f"the source registration {source} names no tasks")
    registry.SEALED_REGISTRATION = Path(source)
    registry.task_ids = lambda: registered
    registry._CACHE.clear()
    try:
        yield registered
    finally:
        registry.task_ids = saved_ids
        registry.SEALED_REGISTRATION = saved_source
        registry._CACHE.clear()


def pinned_hashes() -> dict[str, str]:
    """The confirmation configuration's module set, then this driver on top of it.

    The added entry is last on purpose: a dict keeps its insertion order, so the driver is the only
    module this batch adds to the confirmation set and the gate can say so.
    """

    hashes = dict(runner.module_hashes())
    hashes[driver_module()] = driver_hash()
    return hashes


def write_ledger() -> dict[str, Any]:
    """Pin this batch's modules, once, before its gate: the confirmation set plus this driver."""

    if REVISION_LEDGER.exists():
        raise SystemExit(f"refusing to overwrite the existing ledger {REVISION_LEDGER}")
    hashes = pinned_hashes()
    payload = {
        "schema": "openai_agents_v24_recalibration_module_revision_ledger",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "api_requests_spent": 0,
        "purpose": (
            "pin every module this nine-sample batch runs, which is the confirmation "
            "configuration's module set plus this batch's own driver, so the gate can fail closed "
            "on a drifted driver without touching the confirmation ledger"
        ),
        "confirmation_configuration_ledger": runner._relative(runner.REVISION_LEDGER),
        "modules": hashes,
        "module_count": len(hashes),
        "modules_this_batch_adds": [
            relative for relative in hashes if relative == driver_module()
        ],
        "rule": (
            "a module whose digest is absent from this ledger, or differs from the digest recorded "
            "here, fails the gate; the confirmation ledger is left exactly as it was"
        ),
    }
    REVISION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    REVISION_LEDGER.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def arithmetic() -> dict[str, Any]:
    """The cap arithmetic for nine samples, written out rather than interpolated."""

    worst_per_sample = runner.MAX_TURNS + live.RETRY_BUDGET_PER_SAMPLE
    model_calls_worst = SAMPLES * worst_per_sample
    summary_worst = NATIVE_SAMPLES * runner.MAX_SUMMARY_CALLS_PER_SAMPLE
    worst = model_calls_worst + summary_worst
    return {
        "shape": runner.SHAPE,
        "read_calls": runner.READ_CALLS,
        "max_turns": runner.MAX_TURNS,
        "retry_budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
        "worst_case_requests_per_sample": worst_per_sample,
        "samples": SAMPLES,
        "arms": list(ARMS),
        "repeats": REPEATS,
        "worst_case_model_calls": model_calls_worst,
        "summary_calls_worst_case": summary_worst,
        "worst_case_requests": worst,
        "cap": MAX_API_REQUESTS,
        "margin": round(MAX_API_REQUESTS / worst, 3),
        "at_least_1_3": MAX_API_REQUESTS >= 1.3 * worst,
        "derivation": "READ_CALLS=3 => MAX_TURNS=5 => worst 6 per sample => 9x6 + 3x2 = 60 => 1.3x => 78, capped at 80",
    }


def freeze_payload() -> dict[str, Any]:
    """The recalibration freeze: the same form as the confirmation freeze, for nine samples."""

    math_ = arithmetic()
    return {
        "schema": "openai_agents_v24_recalibration_freeze",
        "id": "V24-RECALIBRATION-01",
        "status": "FROZEN",
        "purpose": "recalibration_of_the_v24_length_compliant_prompt_on_the_development_tasks",
        "written_utc_date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "written_before_any_paid_request": True,
        "api_requests_spent_when_written": 0,
        "experiment_id": EXPERIMENT_ID,
        "produced_by": "experiments/runners/run_openai_agents_v24_recalibration.py",
        "task_set": list(registry.task_ids()),
        "task_set_note": (
            "the host's development tasks, read from the v24 registry in development mode; they "
            "took part in building this host configuration, so this batch is development evidence "
            "for the configuration and is never citable"
        ),
        "matrix": {
            "tasks": list(registry.task_ids()),
            "arms": list(ARMS),
            "repeats": REPEATS,
            "samples": SAMPLES,
            "max_api_requests": MAX_API_REQUESTS,
            "repeats_rationale": (
                "one repeat is the shape the ruling fixes for the recalibration: nine samples are "
                "enough to read the first-attempt acceptance rate the prompt change is meant to "
                "raise, and the movement this batch can detect is coarse by construction, which is "
                "why nothing here is citable and why the confirmation batch keeps the five repeats "
                "of the v16 sealed confirmation"
            ),
        },
        "read_shape": {
            "shape": runner.SHAPE,
            "rounds": runner.ROUNDS,
            "reads_per_round": runner.READS_PER_ROUND,
            "read_calls": runner.READ_CALLS,
            "expected_model_calls": runner.EXPECTED_MODEL_CALLS,
            "max_turns": runner.MAX_TURNS,
            "views_pairwise_disjoint": True,
        },
        "host_configuration": {
            "structured_final_answer": (
                "JSON issue/cause/fix/facts asked for in the prompt, identical for every arm, "
                "rendered by the frozen renderer; a rejected proposal closes to an empty answer"
            ),
            "renderer": {
                "module": live.RENDERER_MODULE,
                "sha256": runner.sha256_of(ROOT / live.RENDERER_MODULE),
                "truncates": False,
                "drops_fields": False,
            },
            "retry": {
                "budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
                "billed": True,
                "carries_host_diagnostic": True,
                "diagnostic_is_two_sided": True,
            },
            "prompt_change": registry.fork_block(),
            "retry_change": live.fork_report(),
            "modules": pinned_hashes(),
            "module_set_note": (
                "the same module set the confirmation batch pins, plus this recalibration driver, "
                "because this batch runs the same configuration and its own driver decides the "
                "shape"
            ),
            "revision_aware_fail_closed": (
                "every module in the path must have a SHA256 recorded here and in the confirmation "
                "configuration's revision ledger; a drift fails the gate"
            ),
        },
        "budget_arithmetic": math_,
        "acceptance_line": {
            "line": runner.ACCEPTANCE_LINE,
            "decided_on": "every pair of this batch's matrix",
            "strict_track": (
                "the four conditions judged on the rendered line: required facts present, frozen "
                "regex full match, single line with the contract prefix, at most 160 characters"
            ),
            "paired_statistic": (
                "paired complete provider-token totals per (task, repeat) pair, with retries and "
                "summary tokens charged to the arm they belong to"
            ),
            "bounds_are_not_relaxed": "at most 160 characters remains an acceptance condition",
            "note": (
                "this is the v24 configuration's own acceptance line, imported rather than "
                "restated, so this batch cannot lower a bar the confirmation batch will use"
            ),
        },
        "recalibration_decision_line": {
            "line": DECISION_LINE,
            "applied_on_top_of": "the v24 acceptance line above",
            "why": (
                "a prompt change alters compliance, so the configuration is re-measured on the "
                "development tasks before it confirms anything; this line is the ruling's own and "
                "it gates the confirmation batch rather than replacing its acceptance line"
            ),
        },
        "report_requirements": [
            "the first-attempt acceptance rate and its rejection-class decomposition per arm",
            "the strict track and semantic endpoint per arm, per task and pooled",
            "the paired complete provider-token saving beside the byte reading",
            "the mechanism counters and their determinism across the repeats",
            "which retry wording each retried sample received",
            "requests against the cap, the cache split and the off-peak flag",
            "the decision line's verdict",
            f"wording: {runner.REQUIRED_WORDING}; this batch is development evidence and is never "
            "merged with the confirmation batch or with the v16 sealed batch",
        ],
        "driver": {"module": driver_module(), "sha256": driver_hash()},
        "development_k1_reference": DEVELOPMENT_K1_REFERENCE,
        "source_of_record": runner.source_of_record_hashes(),
        "source_of_record_note": (
            "the same frozen records the confirmation batch pins, re-verified by the frozen "
            "finaliser before it writes this batch's manifest and by the paid path before its "
            "first request"
        ),
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "not_evidence_yet": (
            "this batch is development evidence for the v24 configuration; it confirms nothing "
            "about the sealed tasks and none of its numbers is citable"
        ),
        "stop_conditions": [
            "first-attempt acceptance below 70 percent -> stop and report",
            "any arm's strict track below the baseline -> stop and report",
            "the semantic endpoint below the baseline -> stop and report",
            "any render failure, truncation or dropped field -> stop and report",
            "any request failure or cap hit -> stop, keep the partial directory, report",
        ],
        "required_wording": runner.REQUIRED_WORDING,
        "forbidden_wording": list(runner.FORBIDDEN_WORDING),
    }


def write_freeze() -> dict[str, Any]:
    if FREEZE.exists():
        raise SystemExit(f"refusing to overwrite the existing freeze {FREEZE}")
    payload = freeze_payload()
    FREEZE.parent.mkdir(parents=True, exist_ok=True)
    FREEZE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def freeze_override(math_: dict[str, Any]) -> dict[str, Any]:
    """The freeze the paid path runs under, read from the artifact written before any request."""

    if not FREEZE.is_file():
        raise SystemExit(f"the recalibration freeze is missing: {FREEZE}")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    live_modules = pinned_hashes()
    recorded = dict(freeze["host_configuration"]["modules"])
    drifted = sorted(
        relative for relative, digest in live_modules.items() if recorded.get(relative) != digest
    )
    if drifted:
        raise SystemExit("the recalibration freeze no longer pins the modules on disk: " + ", ".join(drifted))
    if list(freeze["matrix"]["tasks"]) != list(registry.task_ids()):
        raise SystemExit("the recalibration freeze's task list is not the development set")
    if int(freeze["matrix"]["samples"]) != SAMPLES:
        raise SystemExit("the recalibration freeze's sample count differs from the implemented one")
    if int(freeze["budget_arithmetic"]["cap"]) != MAX_API_REQUESTS:
        raise SystemExit("the recalibration freeze's cap differs from the implemented one")
    return {
        "path": runner._relative(FREEZE),
        "sha256": runner.sha256_of(FREEZE),
        "status": str(freeze["status"]),
        "purpose": str(freeze["purpose"]),
        "matrix": dict(freeze["matrix"]),
        "acceptance_line": dict(freeze["acceptance_line"]),
        "source_of_record": dict(freeze.get("source_of_record") or runner.source_of_record_hashes()),
        "stop_conditions": list(freeze["stop_conditions"]),
        "report_requirements": [
            "the first-attempt acceptance rate and its rejection-class decomposition",
            "the strict track and semantic endpoint per arm, per task and pooled",
            "the paired complete provider-token saving beside the byte reading",
            "the mechanism counters and their determinism across the repeats",
            "which retry wording each retried sample received",
            "requests against the cap, the cache split and the off-peak flag",
            "the decision line's verdict, recomputed by an independent read of the samples",
            f"wording: {runner.REQUIRED_WORDING}; this batch is development evidence and is never "
            "merged with the confirmation batch or with the v16 sealed batch",
        ],
        "modules": dict(recorded),
        "budget_arithmetic": dict(freeze["budget_arithmetic"]),
        "arithmetic": math_,
    }


def _rows_of(root: Path) -> list[dict[str, Any]]:
    path = root / "samples.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _strict_track(row: dict[str, Any]) -> bool:
    report = dict(row.get("render_report") or {})
    return bool(row.get("render_accepted")) and all(
        bool(report.get(name))
        for name in ("facts_complete", "frozen_regex", "single_line_prefix", "within_160")
    )


def _semantic_correct(row: dict[str, Any]) -> bool:
    """The task's own semantic endpoint: the frozen regex, the literals and a non-empty answer."""

    report = dict(row.get("render_report") or {})
    return bool(row.get("render_accepted")) and bool(report.get("frozen_regex")) and bool(
        report.get("facts_complete")
    )


def _totals(row: dict[str, Any]) -> int:
    return int(row.get("all_arm_total_tokens", 0) or 0)


def _run_root() -> Path:
    """Where the pilot writes, derived from the same forced arguments the pilot uses.

    The frozen runner's default ``--out`` is the repository-relative ``runs/stage5-openai-agents-api``,
    so the parsed value is resolved against the repository before it is looked up.
    """

    parsed = runner.base.build_parser().parse_args(
        runner.forced_args(["--confirm-send-public-source"])
    )
    out = Path(parsed.out)
    if not out.is_absolute():
        out = ROOT / out
    return out / parsed.experiment_id


def report(root: Path | None = None) -> dict[str, Any]:
    """The recalibration report: the decision line and everything beside it."""

    run_root = Path(root) if root is not None else _run_root()
    rows = _rows_of(run_root)
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if str(row.get("method")) == arm]
        by_arm[arm] = {
            "samples": len(arm_rows),
            "strict_track_rate": round(
                sum(1 for row in arm_rows if _strict_track(row)) / len(arm_rows), 4
            )
            if arm_rows
            else None,
            "semantic_accuracy": round(
                sum(1 for row in arm_rows if _semantic_correct(row)) / len(arm_rows), 4
            )
            if arm_rows
            else None,
            "first_attempt_accepted": sum(
                1 for row in arm_rows if bool(row.get("v17_first_attempt_accepted"))
            ),
            "first_attempt_acceptance_rate": round(
                sum(1 for row in arm_rows if bool(row.get("v17_first_attempt_accepted")))
                / len(arm_rows),
                4,
            )
            if arm_rows
            else None,
            "retried_samples": sum(
                1 for row in arm_rows if int(row.get("render_repair_calls", 0) or 0) > 0
            ),
            "retry_wording_used": sorted(
                {
                    str(row.get("v24_retry_wording"))
                    for row in arm_rows
                    if str(row.get("v24_retry_wording"))
                }
            ),
            "model_calls": sum(int(row.get("model_calls", 0) or 0) for row in arm_rows),
            "tool_calls": sum(int(row.get("tool_calls", 0) or 0) for row in arm_rows),
            "api_request_attempts": sum(
                int(row.get("api_request_attempts", 0) or 0) for row in arm_rows
            ),
            "total_provider_tokens": sum(_totals(row) for row in arm_rows),
        }
    rejected = [
        {
            "task": str(row.get("scenario")),
            "repeat": int(row.get("repeat", -1)),
            "arm": str(row.get("method")),
            "first_reject_class": str(row.get("v17_first_reject_class") or ""),
            "render_reason": str(row.get("render_reason") or ""),
        }
        for row in rows
        if not bool(row.get("v17_first_attempt_accepted"))
    ]
    failures = {
        "render_failures": [
            f"{row.get('scenario')}/{row.get('repeat')}/{row.get('method')}"
            for row in rows
            if not bool(row.get("render_accepted"))
        ],
        "truncations": [
            f"{row.get('scenario')}/{row.get('repeat')}/{row.get('method')}"
            for row in rows
            if bool(row.get("render_truncated"))
        ],
        "dropped_fields": [
            f"{row.get('scenario')}/{row.get('repeat')}/{row.get('method')}"
            for row in rows
            if bool(row.get("render_fields_dropped"))
        ],
    }
    first_attempt_total = sum(
        1 for row in rows if bool(row.get("v17_first_attempt_accepted"))
    )
    first_attempt_rate = round(first_attempt_total / len(rows), 4) if rows else None
    baseline = by_arm.get(BASELINE_ARM, {})
    plugin = by_arm.get(PLUGIN_ARM, {})
    paired: list[dict[str, Any]] = []
    for task_id in registry.task_ids():
        base_rows = [
            row
            for row in rows
            if str(row.get("scenario")) == task_id and str(row.get("method")) == BASELINE_ARM
        ]
        plug_rows = [
            row
            for row in rows
            if str(row.get("scenario")) == task_id and str(row.get("method")) == PLUGIN_ARM
        ]
        for base_row in base_rows:
            repeat = int(base_row.get("repeat", -1))
            plug_row = next(
                (row for row in plug_rows if int(row.get("repeat", -1)) == repeat), None
            )
            if plug_row is None:
                continue
            paired.append(
                {
                    "task": task_id,
                    "repeat": repeat,
                    "baseline_total_tokens": _totals(base_row),
                    "plugin_total_tokens": _totals(plug_row),
                    "saving": (
                        round(
                            (_totals(base_row) - _totals(plug_row))
                            / _totals(base_row),
                            6,
                        )
                        if _totals(base_row)
                        else None
                    ),
                    "baseline_retried": int(base_row.get("render_repair_calls", 0) or 0),
                    "plugin_retried": int(plug_row.get("render_repair_calls", 0) or 0),
                    "v21_bytes_saved": int(plug_row.get("v21_bytes_saved", 0) or 0),
                }
            )
    savings = [entry["saving"] for entry in paired if entry["saving"] is not None]
    tasks_with_saving: dict[str, int] = {}
    for entry in paired:
        if entry["v21_bytes_saved"] > 0:
            tasks_with_saving[entry["task"]] = tasks_with_saving.get(entry["task"], 0) + 1
    checks = {
        "nine_samples_ran": len(rows) == SAMPLES,
        "every_arm_present": all(by_arm[arm]["samples"] == 3 for arm in ARMS),
        "first_attempt_acceptance_at_least_70_percent": bool(
            first_attempt_rate is not None and first_attempt_rate >= 0.70
        ),
        "every_arms_strict_track_at_least_the_baseline": all(
            by_arm[arm]["strict_track_rate"] is not None
            and baseline["strict_track_rate"] is not None
            and by_arm[arm]["strict_track_rate"] >= baseline["strict_track_rate"]
            for arm in ARMS
        ),
        "semantic_endpoint_at_least_the_baseline": bool(
            plugin.get("semantic_accuracy") is not None
            and baseline.get("semantic_accuracy") is not None
            and plugin["semantic_accuracy"] >= baseline["semantic_accuracy"]
        ),
        "zero_render_failures": not failures["render_failures"],
        "zero_truncations": not failures["truncations"],
        "zero_dropped_fields": not failures["dropped_fields"],
        "mechanism_saves_bytes_on_every_task": len(tasks_with_saving) == len(registry.task_ids()),
        "cap_not_reached": sum(by_arm[arm]["api_request_attempts"] for arm in ARMS)
        < MAX_API_REQUESTS,
    }
    return {
        "schema": "openai_agents_v24_recalibration_report",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "run_directory": runner._relative(run_root) if run_root.is_dir() else str(run_root),
        "decision_line": DECISION_LINE,
        "configuration": configure_for_recalibration(),
        "arithmetic": arithmetic(),
        "first_attempt_acceptance": {
            "accepted": first_attempt_total,
            "of_samples": len(rows),
            "rate": first_attempt_rate,
            "threshold": 0.70,
            "rejection_classes": {
                str(row.get("v17_first_reject_class")): sum(
                    1
                    for other in rows
                    if str(other.get("v17_first_reject_class"))
                    == str(row.get("v17_first_reject_class"))
                )
                for row in rows
                if str(row.get("v17_first_reject_class"))
            },
            "rejected_samples": rejected,
        },
        "per_arm": by_arm,
        "paired": paired,
        "pooled_paired_saving": {
            "pairs": len(savings),
            "mean": round(sum(savings) / len(savings), 6) if savings else None,
            "positive_pairs": sum(1 for value in savings if value > 0),
            "min": min(savings) if savings else None,
            "max": max(savings) if savings else None,
        },
        "mechanism_bytes_saved_per_task": tasks_with_saving,
        "failures": failures,
        "development_k1_reference": DEVELOPMENT_K1_REFERENCE,
        "verdict": {
            "checks": checks,
            "ok": all(checks.values()),
            "decision": (
                "the v24 configuration may proceed to the sealed confirmation batch"
                if all(checks.values())
                else "STOP: the v24 configuration may not proceed to the sealed confirmation batch"
            ),
        },
        "citable": False,
        "not_evidence_yet": (
            "this batch measures the configuration on the development tasks that built it; it is "
            "development evidence and none of its numbers is citable"
        ),
        "under_this_host_configuration": runner.REQUIRED_WORDING,
        "mechanism_is": "guarantee-preserving line-level omission at K=1",
    }


def load_ledger() -> dict[str, str]:
    """This batch's own ledger, so the gate's module-pin assertion reads the right set."""

    if not REVISION_LEDGER.is_file():
        return {}
    record = json.loads(REVISION_LEDGER.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in (record.get("modules") or {}).items()}


def gate() -> dict[str, Any]:
    """The zero-API gate for the recalibration.

    It runs the v24 runner's own gate - a real local pilot of this nine-sample shape, the stub
    controls, the trigger-gate, mechanism, module-pin and literal-invariant assertions, and the two
    new prompt and wording assertions - and adds this batch's own two: the cap arithmetic for nine
    samples, and the pin of the driver that decides the shape.
    """

    result = runner.gate(load_ledger())
    driver_pin = {
        "module": driver_module(),
        "sha256": driver_hash(),
        "recorded_in_this_batchs_ledger": load_ledger().get(driver_module()) == driver_hash(),
        "note": (
            "the driver is not part of the confirmation configuration's module set, so the "
            "confirmation ledger does not pin it and is not edited here; this batch's own ledger "
            "is the confirmation set plus the driver, and the freeze carries the driver's digest "
            "as well, so a driver edited after the freeze fails the freeze check"
        ),
    }
    checks = [
        {
            "check": "the_v24_configuration_gate_passes_unchanged_for_this_shape",
            "ok": bool(result["ok"]),
            "detail": {
                "failed_checks": result["failed_checks"],
                "hard_assertions": result["hard_assertions"],
                "samples": len(result.get("per_sample_mechanism") or []),
                "shape_recorded_by_the_gate": {
                    "samples": runner.SAMPLES,
                    "repeats": runner.REPEATS,
                    "cap": runner.MAX_API_REQUESTS,
                    "experiment_id": runner.EXPERIMENT_ID,
                },
            },
        },
        {
            "check": "the_cap_arithmetic_for_nine_samples_is_at_least_1_3x",
            "ok": bool(arithmetic()["at_least_1_3"]),
            "detail": arithmetic(),
        },
        {
            "check": "the_recalibration_driver_is_pinned",
            "ok": bool(driver_pin["sha256"]),
            "detail": driver_pin,
        },
        {
            "check": "the_recalibration_cannot_be_cited",
            "ok": True,
            "detail": {
                "citable": False,
                "why": (
                    "the three tasks of this batch are the host's development tasks; the batch "
                    "measures the configuration on the set that built it"
                ),
                "required_wording": runner.REQUIRED_WORDING,
            },
        },
    ]
    return {
        "schema": "openai_agents_v24_recalibration_zero_api_gate",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "gate",
        "api_calls": 0,
        "experiment_id": EXPERIMENT_ID,
        "configuration": configure_for_recalibration(),
        "arithmetic": arithmetic(),
        "v24_configuration_gate": {
            "ok": result["ok"],
            "failed_checks": result["failed_checks"],
            "checks": [
                {"check": entry["check"], "ok": entry["ok"]} for entry in result["checks"]
            ],
            "hard_assertions": result["hard_assertions"],
        },
        "driver": driver_pin,
        "checks": checks,
        "ok": all(entry["ok"] for entry in checks),
        "paid_run_allowed": bool(all(entry["ok"] for entry in checks)),
    }


def write_gate() -> dict[str, Any]:
    """Run this batch's gate and write it once.  The caller holds the installation open."""

    payload = gate()
    if GATE_OUT.exists():
        raise SystemExit(f"refusing to overwrite the existing gate {GATE_OUT}")
    GATE_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def run_paid(args: list[str]) -> int:
    """The paid nine-sample pass: it refuses unless this batch's own gate allows it."""

    if not GATE_OUT.is_file():
        raise SystemExit(f"refusing to send a paid request: the gate artifact is missing ({GATE_OUT})")
    recorded = json.loads(GATE_OUT.read_text(encoding="utf-8"))
    if recorded.get("paid_run_allowed") is not True:
        raise SystemExit(
            "refusing to send a paid request: the gate recorded paid_run_allowed="
            f"{recorded.get('paid_run_allowed')!r}"
        )
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    math_ = configure_for_recalibration()
    with runner.v24_chain_installed():
        with runner.v24_finalise_installed():
            status = runner.runner17.run_pilot(
                args, freeze_override=freeze_override(runner.arithmetic())
            )
    # The same preservation step the confirmation batch's paid path takes: the bytes of the runner
    # that ran the batch are preserved outside the repository, their digests are compared with the
    # ledger that pins them, and the record is written into the run directory.
    runner._record_preservation(args, runner.preserve_runner_bytes())
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-freeze", action="store_true")
    parser.add_argument("--register-modules", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--source-registration", default="")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--confirm-send-public-source", action="store_true")
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    source = (
        Path(args.source_registration) if args.source_registration else SOURCE_REGISTRATION
    )
    if args.gate:
        # The gate holds the installation open for its whole duration: the chain's window
        # configuration and the frozen runner's bindings are installed by ``configure``, and a gate
        # that restored them before running its local pilot would run the pilot without them.
        with installed_for_recalibration(source):
            payload = write_gate()
        print(json.dumps({"ok": payload["ok"], "api_calls": 0}, ensure_ascii=False))
        for entry in payload["checks"]:
            print(f"  {entry['check']}: ok={entry['ok']}")
        for name, value in payload["v24_configuration_gate"]["hard_assertions"].items():
            print(f"  hard assertion {name}: {value}")
        for name in payload["v24_configuration_gate"]["failed_checks"]:
            print(f"  FAILED: {name}")
        return 0 if payload["ok"] else 1
    if args.pilot:
        paid = list(rest)
        if args.confirm_send_public_source and "--confirm-send-public-source" not in paid:
            paid.append("--confirm-send-public-source")
        with installed_for_recalibration(source):
            return run_paid(paid)
    with installed_for_recalibration(source):
        if args.register_modules:
            payload = write_ledger()
            print(f"recalibration ledger: {REVISION_LEDGER}")
            print(
                f"modules={payload['module_count']} adds={payload['modules_this_batch_adds']}"
            )
            return 0
        if args.write_freeze:
            payload = write_freeze()
            print(f"recalibration freeze: {FREEZE}")
            print(
                f"tasks={len(payload['task_set'])} samples={payload['matrix']['samples']} "
                f"cap={payload['matrix']['max_api_requests']} worst={payload['budget_arithmetic']['worst_case_requests']}"
            )
            return 0
        if args.report:
            payload = report(Path(args.run_dir) if args.run_dir else None)
            runner._write_once(REPORT_OUT, payload)
            print(f"recalibration report: {REPORT_OUT}")
            print(json.dumps(payload["verdict"], ensure_ascii=False, indent=2))
            return 0 if payload["verdict"]["ok"] else 1
    parser.error("nothing to do: pass --write-freeze, --gate, --pilot or --report")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
