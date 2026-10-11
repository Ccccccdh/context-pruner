"""The independent audit of the v24 length-compliance K=1 sealed batch: recompute, then judge.

This script shares no code with the runner that produced the batch.  It imports the frozen renderer's
*constants* it needs to re-derive the four conditions and nothing else, and it recomputes every number
it judges from the persisted sample rows and from the frozen artifacts on disk:

* the frozen contract: the freeze digest, the module digests, the source-of-record digests, the cap
  arithmetic, the acceptance line and the pre-registered manifest keys, each re-hashed here;
* the gate chain the amendment adds: the original v24 gate artifact, the amended gate artifact, the
  amendment pin and the delegate pin, each re-hashed and each compared with what the manifest says;
* the four conditions per sample, rebuilt from the rendered line, the registered regex, the
  registered facts and the 160 character bound - and compared with the verdict the host recorded, so
  a disagreement is an audit error rather than a silent pass;
* the primary criterion (``as-paired`` complete provider tokens) per task and pooled, with the strict
  track per task, the count of positive pairs, and a seeded bootstrap interval;
* the amendment's four operative clauses, each decided from the rows rather than from prose:
  zero render failures, truncations and dropped fields; every arm's strict track at or above the
  baseline; every arm's semantic correctness at or above the baseline; and the retry recovery rate
  against its 90 percent line;
* the first-attempt acceptance rate overall and per arm with its rejection-class decomposition, the
  retry count per arm and the retry label of every pair, which the amendment's residual risk makes
  mandatory reporting rather than commentary;
* the co-reported readings: the symmetric matched prefix in complete provider tokens and in context
  bytes - with the symmetry demonstrated from the two call counts rather than asserted - the strict
  track and semantic accuracy per arm, per-arm tool and model call counts, the mechanism counters,
  the determinism of the mechanism's byte saving across repeats, the defect-view round disclosure,
  the request ledger against its cap, and the provider cache split with the off-peak flag and the
  frozen price table.

It writes two artifacts: the audit (``complete``, ``errors``, ``usable_as_saving``) and the report the
amendment's ``required_in_the_confirmation_report`` asks for.  Nothing it writes may be merged with
the v16 sealed batch's number, and it says so in its own words.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RUNS = ROOT / "runs/stage5-openai-agents-api"
#: The id the batch actually ran under.  The delegate asked for ``...-02`` and the frozen runner's own
#: ``forced_args`` re-injected the id the confirmation freeze pre-registers, so the paid run landed
#: under the frozen id; this audit therefore reads the directory that exists rather than the one that
#: was asked for, and the report records the substitution in its own words.
EXPERIMENT_ID = "openai-repo-diagnostic-v24-length-compliance-k1-01"
REQUESTED_EXPERIMENT_ID = "openai-repo-diagnostic-v24-length-compliance-k1-02"
BATCH = RUNS / EXPERIMENT_ID
STOPPED_BATCH = (
    "runs/stage5-openai-agents-api/openai-repo-diagnostic-v23-v18-sealed-window-k1-01"
)

FREEZE = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_CONFIRMATION_FREEZE_20261010.json"
)
REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
V24_REGISTRATION = ROOT / "integrations/openai_agents/V24_SOURCE_REGISTRATION_20261010.json"
GATE = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_ZERO_API_GATE_20261010.json"
AMENDED_GATE = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_AMENDED_GATE_20261010.json"
)
LEDGER = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_MODULE_REVISIONS_20261010.json"
)
PLAN = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_LIVE_PLAN_20261010.json"
STUB = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_LIVE_STUB_20261010.json"
AMENDMENT = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_GATE_AMENDMENT_20261010.json"
)
DELEGATE = ROOT / "experiments/runners/run_openai_agents_v24_gate_amendment.py"
FROZEN_RUNNER = (
    ROOT / "experiments/runners/run_openai_agents_v24_length_compliance_k1.py"
)
RECALIBRATION_REPORT = (
    ROOT / "integrations/openai_agents/V24_RECALIBRATION_REPORT_20261010.json"
)
REGRESSION = ROOT / "integrations/openai_agents/V18_SEALED_K1_FOCUSED_REGRESSION_RUN2_20261010.json"
SEALED_V16_CITATION = (
    ROOT / "integrations/openai_agents/V16_SEALED_CONFIRMATION_CITATION_RECORD_20261006.json"
)
CITATION_AMENDMENT = (
    ROOT / "integrations/openai_agents/V16_SEALED_CONFIRMATION_CITATION_RECORD_AMENDMENT_20261010.json"
)

AUDIT_OUT = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_AUDIT_20261010.json"
REPORT_OUT = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_REPORT_20261010.json"

ARMS = ("none", "native_summary", "pruner_v1")
PLUGIN = "pruner_v1"
BASELINE = "none"
MAX_CHARS = 160
BOOTSTRAP_SEED = 20261010
BOOTSTRAP_ROUNDS = 10000
RETRY_RECOVERY_LINE = 0.90
FORBIDDEN_WORDING = (
    "compression alone saved X percent",
    "the four criteria are met",
    "the retry tax is gone",
    "the plugin saves about 12.5 percent of context",
)
REQUIRED_WORDING = "under this host configuration"
MECHANISM_IS = "guarantee-preserving line-level omission at K=1"
ALLOWED_EXISTING_FAILURE = (
    "tests/test_openai_agents_v16_live_runner_zero_api.py::RegistryTests::"
    "test_problem_statement_hash_reconciliation_is_recorded"
)

#: The frozen price table this batch's dollar reading is converted with.  The rates are read from the
#: table on disk rather than typed here; these are the field names the v16 round froze.
PRICE_TABLE = ROOT / "integrations/openai_agents/V16_CACHE_METERING_AND_COST_20261006.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def per_call_bytes(row: dict[str, Any]) -> list[int]:
    """The context bytes the recorder wrote for each model input, in boundary order."""

    staged: dict[int, list[int]] = defaultdict(list)
    for entry in row.get("evidence_input_bytes_by_boundary") or []:
        if entry.get("stage") == "model_input":
            staged[int(entry.get("index", 0))].append(int(entry.get("input_bytes", 0) or 0))
    return [max(values) for _key, values in sorted(staged.items())]


def per_call_tokens(row: dict[str, Any]) -> list[int]:
    return [int(value or 0) for value in row.get("actual_input_tokens_by_call") or []]


def recompute_conditions(line: str, issue: str, pattern: str, facts: list[str]) -> dict[str, bool]:
    """The four conditions, rebuilt from the rendered line and the registered contract."""

    lowered = str(line).lower()
    return {
        "facts_complete": all(str(fact).lower() in lowered for fact in facts),
        "frozen_regex": re.fullmatch(pattern, str(line), flags=re.IGNORECASE) is not None,
        "single_line_prefix": str(line).startswith("RESULT ") and "\n" not in str(line),
        "within_160": 0 < len(str(line)) <= MAX_CHARS,
    }


def statistics(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None, "positive": 0}
    mean = sum(values) / len(values)
    variance = (
        sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        if len(values) > 1
        else 0.0
    )
    return {
        "n": len(values),
        "mean": round(mean, 6),
        "sd": round(variance**0.5, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "positive": sum(1 for value in values if value > 0),
    }


def bootstrap(values: list[float]) -> dict[str, Any]:
    if len(values) < 2:
        return {
            "rounds": 0,
            "seed": BOOTSTRAP_SEED,
            "lower": None,
            "upper": None,
            "excludes_zero": None,
        }
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_ROUNDS))
    lower = means[int(0.025 * len(means))]
    upper = means[int(0.975 * len(means)) - 1]
    return {
        "rounds": BOOTSTRAP_ROUNDS,
        "seed": BOOTSTRAP_SEED,
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "excludes_zero": lower > 0 or upper < 0,
        "note": (
            "reported for explanation; the frozen verdict is the per-task strict track, the "
            "majority of positive pairs and the batch mean"
        ),
    }


def frozen_prices() -> dict[str, Any]:
    """The frozen price table's rates, read from the table rather than typed here."""

    table = _read(PRICE_TABLE)
    text = json.dumps(table, ensure_ascii=False)

    def rate(pattern: str) -> float | None:
        match = re.search(pattern, text)
        return float(match.group(1)) if match else None

    rates = {
        "source": str(PRICE_TABLE.relative_to(ROOT)).replace("\\", "/"),
        "input_cache_miss_usd_per_million": rate(r"cache_miss[^0-9]{0,40}?([0-9]*\.?[0-9]+)"),
        "input_cache_hit_usd_per_million": rate(r"cache_hit[^0-9]{0,40}?([0-9]*\.?[0-9]+)"),
        "output_usd_per_million": rate(r"output[^0-9]{0,40}?([0-9]*\.?[0-9]+)"),
        "off_peak_multiplier": rate(r"off_peak[^0-9]{0,40}?([0-9]*\.?[0-9]+)"),
        "read_from_the_table": True,
    }
    if any(rates[key] is None for key in rates if key.endswith(("million", "multiplier"))):
        rates["note"] = (
            "the table's rates could not all be re-read by field name; the dollar figure is "
            "reported only if every rate was found"
        )
    return rates


def strict_of(row: dict[str, Any], registration: dict[str, Any]) -> bool:
    """The strict track, recomputed from the rendered line, for one row."""

    task_id = str(row["scenario"])
    contract = registration["tasks"][task_id]["contract"]
    line = str(row.get("final_output", ""))
    if not line:
        return False
    return all(
        recompute_conditions(
            line,
            str(contract["issue_value"]),
            str(contract["frozen_regex"]),
            [str(fact) for fact in contract["required_facts"]],
        ).values()
    )


def semantic_of(row: dict[str, Any], registration: dict[str, Any]) -> bool:
    """The task's own semantic endpoint: the frozen regex, the literals and a non-empty answer."""

    task_id = str(row["scenario"])
    contract = registration["tasks"][task_id]["contract"]
    line = str(row.get("final_output", ""))
    if not line:
        return False
    conditions = recompute_conditions(
        line,
        str(contract["issue_value"]),
        str(contract["frozen_regex"]),
        [str(fact) for fact in contract["required_facts"]],
    )
    return bool(conditions["frozen_regex"]) and bool(conditions["facts_complete"])


def audit() -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not BATCH.is_dir():
        raise SystemExit(f"the batch directory is missing: {BATCH}")
    manifest = _read(BATCH / "manifest.json")
    rows = load_rows()
    freeze = _read(FREEZE)
    registration = _read(REGISTRATION)
    amendment = _read(AMENDMENT)
    amended_gate = _read(AMENDED_GATE)
    original_gate = _read(GATE)
    contract: dict[str, Any] = {"checks": []}

    def contract_check(name: str, ok: bool, detail: Any = None) -> None:
        contract["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append(name)

    # ------------------------------------------------------------------ the frozen contract
    contract_check(
        "the_manifest_cites_the_frozen_confirmation_freeze",
        str(manifest.get("freeze_path"))
        == str(FREEZE.relative_to(ROOT)).replace("\\", "/")
        and str(manifest.get("freeze_sha256")) == sha256_of(FREEZE),
        {
            "manifest_freeze_path": str(manifest.get("freeze_path")),
            "manifest_freeze_sha256": str(manifest.get("freeze_sha256")),
            "recomputed_freeze_sha256": sha256_of(FREEZE),
        },
    )
    live_modules = {
        relative: sha256_of(ROOT / relative) for relative in freeze["host_configuration"]["modules"]
    }
    drifted = sorted(
        relative
        for relative, digest in live_modules.items()
        if digest != freeze["host_configuration"]["modules"][relative]
    )
    contract_check(
        "every_pinned_module_still_matches_the_freeze",
        not drifted,
        {"drifted": drifted, "modules": len(live_modules)},
    )
    contract_check(
        "the_manifest_copies_the_frozen_module_hashes",
        dict(manifest.get("module_hashes") or {})
        == dict(manifest.get("implementation_hashes") or {})
        and all(
            dict(manifest.get("module_hashes") or {}).get(relative) == digest
            for relative, digest in live_modules.items()
        ),
        {"modules_in_manifest": len(manifest.get("module_hashes") or {})},
    )
    source_drift = sorted(
        relative
        for relative, digest in freeze["source_of_record"].items()
        if sha256_of(ROOT / relative) != digest
    )
    contract_check(
        "every_source_of_record_file_still_matches", not source_drift, {"drifted": source_drift}
    )
    contract_check(
        "the_manifest_verifies_every_source_of_record_file",
        bool(manifest.get("source_of_record_verified"))
        and all(
            entry.get("matches") is True
            for entry in (manifest.get("source_of_record_verified") or {}).values()
        ),
        {"entries": len(manifest.get("source_of_record_verified") or {})},
    )
    arithmetic = freeze["budget_arithmetic"]
    recomputed_worst = 45 * 6 + 15 * 2
    contract_check(
        "the_cap_arithmetic_recomputes",
        recomputed_worst == 300
        and int(arithmetic["worst_case_requests"]) == recomputed_worst
        and int(arithmetic["cap"]) == 390
        and 390 >= 1.3 * recomputed_worst,
        {
            "read_calls": freeze["read_shape"]["read_calls"],
            "max_turns": freeze["read_shape"]["max_turns"],
            "worst_case_requests": arithmetic["worst_case_requests"],
            "cap": arithmetic["cap"],
            "derivation": arithmetic["derivation"],
        },
    )
    missing_required = [
        name for name in freeze["manifest_contract"]["required_keys"] if name not in manifest
    ]
    missing_extra = [
        name
        for name in freeze["manifest_contract"]["extra_keys_this_round_adds"]
        if name not in manifest
    ]
    contract_check(
        "the_manifest_records_the_finalised_flag_and_every_pre_registered_key",
        manifest.get("v16_manifest_finalized") is True
        and not missing_required
        and not missing_extra,
        {"missing_required": missing_required, "missing_extra": missing_extra},
    )
    contract_check(
        "both_citation_flags_are_false_and_the_disclosures_are_present",
        manifest.get("citable_as_saving") is False
        and manifest.get("citable_as_quality_equivalence") is False
        and bool(manifest.get("tool_output_disclosure"))
        and bool(manifest.get("re_read_visibility"))
        and str(manifest.get("required_wording")) == REQUIRED_WORDING
        and list(manifest.get("forbidden_wording") or []) == list(FORBIDDEN_WORDING),
        {
            "citable_as_saving": manifest.get("citable_as_saving"),
            "citable_as_quality_equivalence": manifest.get("citable_as_quality_equivalence"),
            "required_wording": manifest.get("required_wording"),
        },
    )
    contract_check(
        "the_zero_api_gate_that_allowed_the_batch_is_on_record_and_green",
        original_gate.get("ok") is True
        and original_gate.get("paid_run_allowed") is True
        and original_gate.get("api_calls") == 0,
        {
            "gate": str(GATE.relative_to(ROOT)).replace("\\", "/"),
            "gate_sha256": sha256_of(GATE) if GATE.is_file() else "",
            "checks": len(original_gate.get("checks") or []),
            "hard_assertions": original_gate.get("hard_assertions"),
        },
    )
    ledger = _read(LEDGER)
    contract_check(
        "the_revision_ledger_pins_the_same_modules_the_freeze_does",
        dict(ledger.get("modules") or {}) == dict(freeze["host_configuration"]["modules"]),
        {
            "ledger_revision": ledger.get("revision"),
            "ledger_modules": len(ledger.get("modules") or {}),
        },
    )
    regression = _read(REGRESSION)
    contract_check(
        "the_focused_regression_is_on_record_with_only_the_allowed_existing_failure",
        regression.get("ok") is True
        and not regression.get("unexpected_failures")
        and regression.get("allowed_failures_observed") == [ALLOWED_EXISTING_FAILURE],
        {
            "recorded_run": str(REGRESSION.relative_to(ROOT)).replace("\\", "/"),
            "recorded_run_sha256": sha256_of(REGRESSION) if REGRESSION.is_file() else "",
            "counts": regression.get("summary_counts"),
        },
    )
    plan = _read(PLAN)
    stub = _read(STUB)
    contract_check(
        "the_plan_and_stub_artifacts_are_zero_api",
        plan.get("api_calls") == 0
        and stub.get("api_calls") == 0
        and bool((stub.get("frozen_control_replay") or {}).get("controls_ok"))
        and bool((stub.get("registered_task_controls") or {}).get("ok")),
        {
            "plan": str(PLAN.relative_to(ROOT)).replace("\\", "/"),
            "stub": str(STUB.relative_to(ROOT)).replace("\\", "/"),
            "frozen_controls": len((stub.get("frozen_control_replay") or {}).get("verdicts") or []),
            "registered_task_controls": len(
                (stub.get("registered_task_controls") or {}).get("detail") or []
            ),
        },
    )
    contract_check(
        "the_registration_that_produced_the_tasks_still_verifies",
        registration.get("registered_task_count") == 3
        and all(
            entry.get("problem_statement_sha256_matches_preselection")
            for entry in registration["tasks"].values()
        ),
        {
            "registration": str(REGISTRATION.relative_to(ROOT)).replace("\\", "/"),
            "registration_sha256": sha256_of(REGISTRATION),
            "task_ids": registration["task_ids"],
        },
    )
    v24_registration = _read(V24_REGISTRATION)
    contract_check(
        "the_v24_registration_repeats_the_same_three_tasks_and_the_prior_run_disclosure",
        list(v24_registration.get("registered_task_ids") or []) == list(registration["task_ids"])
        and (
            v24_registration.get("prior_batch_disclosure") or {}
        ).get("prior_outcome")
        == "stopped at 39 of 45 samples"
        and v24_registration.get("task_set_is_exhausted_after_this_batch") is True,
        {
            "path": str(V24_REGISTRATION.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_of(V24_REGISTRATION) if V24_REGISTRATION.is_file() else "",
            "task_ids": v24_registration.get("registered_task_ids"),
            "prior_outcome": (
                v24_registration.get("prior_batch_disclosure") or {}
            ).get("prior_outcome"),
        },
    )

    # --------------------------------------------------- the amendment and its two gate artifacts
    amendment_pin = {
        "path": str(AMENDMENT.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha256_of(AMENDMENT) if AMENDMENT.is_file() else "",
        "amendment_id": str(amendment.get("amendment_id", "")),
        "amends": str(amendment.get("amends", "")),
        "citable": amendment.get("citable"),
        "api_requests_spent_when_written": amendment.get("api_requests_spent_when_written"),
    }
    contract_check(
        "the_gate_amendment_the_batch_ran_under_is_present_and_uncited",
        AMENDMENT.is_file() and amendment_pin["citable"] is False,
        amendment_pin,
    )
    manifest_amendment = dict(manifest.get("v24_gate_amendment") or {})
    manifest_amendment_pin = dict(manifest_amendment.get("amendment") or {})
    contract_check(
        "the_manifest_records_the_amendments_path_and_sha256",
        str(manifest_amendment_pin.get("path", "")) == amendment_pin["path"]
        and str(manifest_amendment_pin.get("sha256", "")) == amendment_pin["sha256"]
        and manifest_amendment_pin.get("read_and_verified_before_the_first_request") is True,
        {
            "manifest": manifest_amendment_pin,
            "recomputed": amendment_pin,
        },
    )
    recorded_original = dict(amended_gate.get("recorded_frozen_gate") or {})
    delegate = _read_gate_delegate(amended_gate)
    contract_check(
        "the_amended_gate_is_on_record_green_and_references_both_pins",
        AMENDED_GATE.is_file()
        and amended_gate.get("paid_run_allowed") is True
        and amended_gate.get("api_calls") == 0
        and str(recorded_original.get("amendment_sha256", "")) == amendment_pin["sha256"]
        and str(recorded_original.get("sha256", "")) == sha256_of(GATE)
        and str(recorded_original.get("batch_experiment_id", "")) == REQUESTED_EXPERIMENT_ID,
        {
            "amended_gate": str(AMENDED_GATE.relative_to(ROOT)).replace("\\", "/"),
            "amended_gate_sha256": sha256_of(AMENDED_GATE) if AMENDED_GATE.is_file() else "",
            "recorded_frozen_gate": recorded_original,
            "checks": len(amended_gate.get("checks") or []),
            "criteria_evaluation": amended_gate.get("criteria_evaluation"),
            "the_recorded_batch_id_is_the_one_the_delegate_asked_for": (
                "the gate was written under the delegate's requested id; the id the work actually "
                "landed under is the frozen one, which is checked separately below rather than "
                "folded into this check"
            ),
        },
    )
    # The amended gate names the id the delegate asked for, and the run landed under the id the
    # confirmation freeze pre-registers.  That divergence is recorded rather than smoothed over, and
    # the question it raises - was the batch this gate allowed the batch that ran? - is answered from
    # the runtime chain instead of from the id string: the delegate that carries the pinning, the
    # amendment it pins, the original gate and the frozen runner are all compared with the digests the
    # gate recorded, and the frozen runner's own forced_args is what makes the frozen id inevitable.
    delegate_source = DELEGATE.read_text(encoding="utf-8") if DELEGATE.is_file() else ""
    delegate_has_the_id_override = (
        'paid = runner.runner17._replace_option(paid, "--experiment-id", EXPERIMENT_ID)'
        in delegate_source
    )
    ledger_runner_digest = str(
        (ledger.get("modules") or {}).get(
            "experiments/runners/run_openai_agents_v24_length_compliance_k1.py", ""
        )
    )
    delegate_pin = dict(manifest_amendment.get("delegate") or {})
    contract_check(
        "the_batch_that_ran_is_the_batch_the_amended_gate_allowed_under_the_frozen_id",
        str(recorded_original.get("batch_experiment_id", "")) == REQUESTED_EXPERIMENT_ID
        and str(recorded_original.get("delegate_sha256", "")) == sha256_of(DELEGATE)
        and str(delegate.get("sha256", "")) == sha256_of(DELEGATE)
        and bool(delegate_has_the_id_override)
        and str(manifest_amendment_pin.get("sha256", "")) == amendment_pin["sha256"]
        and str(delegate_pin.get("sha256", "")) == sha256_of(DELEGATE)
        and str((delegate_pin.get("the_frozen_runner_it_delegates_to") or {}).get("recorded_sha256", ""))
        == sha256_of(FROZEN_RUNNER)
        == ledger_runner_digest
        and str(manifest.get("freeze_sha256", "")) == sha256_of(FREEZE),
        {
            "gate_allowed_the_requested_id": recorded_original.get("batch_experiment_id"),
            "run_landed_under": EXPERIMENT_ID,
            "amendment_re_hashed": amendment_pin["sha256"],
            "delegate_re_hashed": sha256_of(DELEGATE) if DELEGATE.is_file() else "",
            "delegate_recorded_in_the_amended_gate": recorded_original.get("delegate_sha256"),
            "delegate_recorded_in_the_manifest": delegate_pin.get("sha256"),
            "delegate_contains_the_id_override": delegate_has_the_id_override,
            "frozen_runner_re_hashed": sha256_of(FROZEN_RUNNER),
            "frozen_runner_in_the_ledger": ledger_runner_digest,
            "manifest_cites_the_freeze": str(manifest.get("freeze_sha256", "")) == sha256_of(FREEZE),
            "why_the_id_strings_differ": (
                "the frozen runner's own ``forced_args`` replaces ``--experiment-id`` with the id "
                "the confirmation freeze pre-registers, so the paid path cannot run under any other "
                "id; the request was recorded, refused by the frozen resolver, and the batch ran "
                "under the frozen id as the freeze requires"
            ),
            "why_this_is_not_a_weakened_check": (
                "the pins the decision rests on - the amendment, the delegate that carries it, the "
                "frozen runner and the freeze - are all compared with what the gate recorded and "
                "with the files on disk, so an edited amendment or delegate fails here even though "
                "both id strings match"
            ),
        },
    )
    contract_check(
        "the_run_directory_is_the_frozen_id_the_freeze_pre_registers",
        EXPERIMENT_ID == str(freeze.get("experiment_id", ""))
        and BATCH.is_dir()
        and (BATCH / "manifest.json").is_file()
        and (BATCH / "samples.jsonl").is_file(),
        {
            "run_directory": str(BATCH.relative_to(ROOT)).replace("\\", "/"),
            "freeze_experiment_id": freeze.get("experiment_id"),
            "asked_for": REQUESTED_EXPERIMENT_ID,
            "first_and_only_paid_run_under_this_id": True,
            "no_resume": True,
        },
    )
    contract_check(
        "the_amended_gate_re_ran_the_frozen_gate_unchanged_and_reports_every_assertion",
        len(amended_gate.get("frozen_gate_re_run", {}).get("hard_assertions") or {}) == 8
        and all(
            value is True
            for value in (
                amended_gate.get("frozen_gate_re_run", {}).get("hard_assertions") or {}
            ).values()
        )
        and not (amended_gate.get("frozen_gate_re_run", {}).get("failed_checks") or [])
        and amended_gate.get("frozen_gate_re_run", {}).get("per_sample_mechanism_samples") == 45,
        {
            "hard_assertions": amended_gate.get("frozen_gate_re_run", {}).get("hard_assertions"),
            "failed_checks": amended_gate.get("frozen_gate_re_run", {}).get("failed_checks"),
            "per_sample_mechanism_samples": amended_gate.get("frozen_gate_re_run", {}).get(
                "per_sample_mechanism_samples"
            ),
            "checks_in_this_amended_gate": len(amended_gate.get("checks") or []),
        },
    )
    contract_check(
        "the_frozen_runner_on_disk_is_still_the_module_the_ledger_pins",
        str((amended_gate.get("frozen_runner_unchanged") or {}).get("live_sha256", ""))
        == sha256_of(FROZEN_RUNNER)
        == str(ledger.get("modules", {}).get(
            "experiments/runners/run_openai_agents_v24_length_compliance_k1.py", ""
        )),
        {
            "live_sha256": sha256_of(FROZEN_RUNNER),
            "recorded_in_amended_gate": (
                amended_gate.get("frozen_runner_unchanged") or {}
            ).get("live_sha256"),
            "recorded_in_ledger": ledger.get("modules", {}).get(
                "experiments/runners/run_openai_agents_v24_length_compliance_k1.py", ""
            ),
        },
    )
    contract_check(
        "the_delegate_that_carried_the_amendment_is_pinned_by_its_own_bytes",
        DELEGATE.is_file()
        and str(delegate_pin.get("sha256", "")) == sha256_of(DELEGATE)
        and str(delegate.get("sha256", "")) == sha256_of(DELEGATE),
        {
            "module": str(DELEGATE.relative_to(ROOT)).replace("\\", "/"),
            "live_sha256": sha256_of(DELEGATE) if DELEGATE.is_file() else "",
            "recorded_in_manifest": delegate_pin.get("sha256"),
            "recorded_in_amended_gate": delegate.get("sha256"),
        },
    )
    discipline = dict(manifest_amendment.get("discipline") or {})
    contract_check(
        "the_amendment_refusals_are_recorded_in_the_manifest",
        discipline.get("no_literal_was_weakened") is True
        and discipline.get("no_task_was_dropped_after_seeing_it_fail") is True
        and discipline.get("the_confirmation_is_not_reported_as_meeting_the_70_percent_line") is True
        and discipline.get("resume_was_refused") is True,
        discipline,
    )
    prior = dict(manifest.get("v24_prior_batch_disclosure") or {})
    exhaustion = dict(manifest.get("v24_task_set_exhaustion") or {})
    contract_check(
        "the_manifest_discloses_the_prior_run_of_the_same_task_set_verbatim",
        prior.get("prior_outcome") == "stopped at 39 of 45 samples"
        and prior.get("prior_batch") == STOPPED_BATCH
        and prior.get("this_batch_is_the_second_run_of_the_same_task_set") is True
        and exhaustion.get("exhausted_after_this_batch") is True
        and exhaustion.get("runs_written_down") == 2
        and "django-15572" in str((freeze.get("prior_batch") or {}).get("failure", "")),
        {
            "prior_outcome": prior.get("prior_outcome"),
            "prior_batch": prior.get("prior_batch"),
            "freeze_prior_failure": (freeze.get("prior_batch") or {}).get("failure"),
            "exhaustion": exhaustion.get("statement"),
        },
    )

    # ------------------------------------------------------------------------ the sample matrix
    if not rows:
        raise SystemExit(f"the batch wrote no samples: {BATCH / 'samples.jsonl'}")
    task_ids = list(registration["task_ids"])
    index = {(str(row["scenario"]), int(row["repeat"]), str(row["method"])): row for row in rows}
    repeats = sorted({int(row["repeat"]) for row in rows})
    matrix_complete = (
        len(rows) == 45
        and sorted({str(row["scenario"]) for row in rows}) == sorted(task_ids)
        and sorted({str(row["method"]) for row in rows}) == sorted(ARMS)
        and repeats == [0, 1, 2, 3, 4]
    )
    contract_check(
        "the_batch_is_the_frozen_matrix",
        matrix_complete,
        {
            "rows": len(rows),
            "tasks": sorted({str(row["scenario"]) for row in rows}),
            "arms": sorted({str(row["method"]) for row in rows}),
            "repeats": repeats,
        },
    )
    if not matrix_complete:
        errors.append(
            "the_frozen_matrix_is_incomplete: the batch is audited as incomplete, never as a result"
        )

    # ------------------------------------------------------- the four conditions, recomputed
    condition_detail: list[dict[str, Any]] = []
    disagreements: list[str] = []
    failures: list[str] = []
    for task_id in task_ids:
        view = registration["tasks"][task_id]["contract"]
        facts = [str(fact) for fact in view["required_facts"]]
        pattern = str(view["frozen_regex"])
        issue = str(view["issue_value"])
        for repeat in repeats:
            for arm in ARMS:
                row = index.get((task_id, repeat, arm))
                if row is None:
                    errors.append(f"missing sample {task_id}/{repeat}/{arm}")
                    continue
                line = str(row.get("final_output", ""))
                recomputed = (
                    recompute_conditions(line, issue, pattern, facts)
                    if line
                    else {
                        "facts_complete": False,
                        "frozen_regex": False,
                        "single_line_prefix": False,
                        "within_160": False,
                    }
                )
                recorded = {k: bool(v) for k, v in (row.get("render_conditions") or {}).items()}
                strict_recorded = bool(row.get("render_accepted")) and all(
                    recorded.get(name)
                    for name in ("facts_complete", "frozen_regex", "single_line_prefix", "within_160")
                )
                strict_recomputed = bool(line) and all(recomputed.values())
                if recorded and recorded != recomputed:
                    disagreements.append(f"{task_id}/{repeat}/{arm}")
                if strict_recorded != strict_recomputed:
                    disagreements.append(f"{task_id}/{repeat}/{arm}: strict track")
                if row.get("render_truncated") or row.get("render_fields_dropped"):
                    failures.append(f"{task_id}/{repeat}/{arm}: truncated or dropped a field")
                if str(row.get("error_type") or ""):
                    failures.append(f"{task_id}/{repeat}/{arm}: {row.get('error_type')}")
                condition_detail.append(
                    {
                        "task": task_id,
                        "repeat": repeat,
                        "arm": arm,
                        "line": line,
                        "line_chars": len(line),
                        "conditions_recomputed": recomputed,
                        "conditions_recorded": recorded,
                        "strict_recomputed": strict_recomputed,
                        "strict_recorded": strict_recorded,
                        "semantic_recomputed": semantic_of(row, registration),
                        "render_reason": str(row.get("render_reason", "")),
                        "render_repair_calls": int(row.get("render_repair_calls", 0) or 0),
                        "model_calls": int(row.get("model_calls", 0) or 0),
                        "tool_calls": int(row.get("tool_calls", 0) or 0),
                        "api_request_attempts": int(row.get("api_request_attempts", 0) or 0),
                        "total_provider_tokens": int(row.get("all_arm_total_tokens", 0) or 0),
                    }
                )
    contract_check(
        "the_recorded_four_conditions_are_reproduced_independently",
        not disagreements,
        {"disagreements": disagreements[:12], "samples_checked": len(condition_detail)},
    )
    contract_check(
        "no_render_failure_truncation_or_dropped_field", not failures, {"failures": failures}
    )

    # --------------------------------------------------------------- the primary criterion
    pairs: list[dict[str, Any]] = []
    per_task: dict[str, Any] = {}
    pooled: list[float] = []
    for task_id in task_ids:
        savings: list[float] = []
        strict = {arm: 0 for arm in ARMS}
        semantic = {arm: 0 for arm in ARMS}
        calls = {arm: 0 for arm in ARMS}
        tools = {arm: 0 for arm in ARMS}
        for repeat in repeats:
            base_row = index.get((task_id, repeat, BASELINE))
            plugin_row = index.get((task_id, repeat, PLUGIN))
            native_row = index.get((task_id, repeat, "native_summary"))
            if base_row is None or plugin_row is None or native_row is None:
                continue
            for arm, row in (
                (BASELINE, base_row),
                ("native_summary", native_row),
                (PLUGIN, plugin_row),
            ):
                strict[arm] += 1 if strict_of(row, registration) else 0
                semantic[arm] += 1 if semantic_of(row, registration) else 0
                calls[arm] += int(row.get("model_calls", 0) or 0)
                tools[arm] += int(row.get("tool_calls", 0) or 0)
            base_total = int(base_row.get("all_arm_total_tokens", 0) or 0)
            plugin_total = int(plugin_row.get("all_arm_total_tokens", 0) or 0)
            native_total = int(native_row.get("all_arm_total_tokens", 0) or 0)
            value = (base_total - plugin_total) / base_total if base_total else 0.0
            base_calls_tokens = per_call_tokens(base_row)
            plugin_calls_tokens = per_call_tokens(plugin_row)
            k = min(len(base_calls_tokens), len(plugin_calls_tokens))
            base_prefix = sum(base_calls_tokens[:k])
            plugin_prefix = sum(plugin_calls_tokens[:k])
            matched_tokens = (base_prefix - plugin_prefix) / base_prefix if base_prefix else 0.0
            base_bytes = per_call_bytes(base_row)
            plugin_bytes = per_call_bytes(plugin_row)
            kb = min(len(base_bytes), len(plugin_bytes))
            bytes_base_prefix = sum(base_bytes[:kb])
            matched_bytes = (
                (bytes_base_prefix - sum(plugin_bytes[:kb])) / bytes_base_prefix
                if bytes_base_prefix
                else 0.0
            )
            savings.append(round(value, 6))
            pooled.append(value)
            pairs.append(
                {
                    "task": task_id,
                    "repeat": repeat,
                    "baseline_tokens": base_total,
                    "plugin_tokens": plugin_total,
                    "native_tokens": native_total,
                    "as_paired_tokens": round(value, 6),
                    "matched_prefix_k": k,
                    "matched_prefix_baseline_tokens": base_prefix,
                    "matched_prefix_plugin_tokens": plugin_prefix,
                    "matched_prefix_tokens": round(matched_tokens, 6),
                    "matched_prefix_bytes": round(matched_bytes, 6),
                    "matched_prefix_is_symmetric": k
                    == min(len(base_calls_tokens), len(plugin_calls_tokens)),
                    "matched_prefix_symmetry_rule": (
                        "k = min(baseline calls, plugin calls); the same k first calls of each arm "
                        "are compared, so neither arm's own retry can hide inside its own sum"
                    ),
                    "baseline_calls": len(base_calls_tokens),
                    "plugin_calls": len(plugin_calls_tokens),
                    "baseline_retried": int(base_row.get("render_repair_calls", 0) or 0) > 0,
                    "plugin_retried": int(plugin_row.get("render_repair_calls", 0) or 0) > 0,
                    "native_retried": int(native_row.get("render_repair_calls", 0) or 0) > 0,
                    "retrying_arm": ", ".join(
                        arm
                        for arm, row in (
                            (BASELINE, base_row),
                            ("native_summary", native_row),
                            (PLUGIN, plugin_row),
                        )
                        if int(row.get("render_repair_calls", 0) or 0) > 0
                    )
                    or "none",
                    "retry_labels": {
                        arm: {
                            "v24_retry_wording": str(row.get("v24_retry_wording") or ""),
                            "v24_force_retry_wording": bool(row.get("v24_force_retry_wording")),
                            "render_repair_calls": int(row.get("render_repair_calls", 0) or 0),
                            "first_attempt_accepted": bool(row.get("v17_first_attempt_accepted")),
                            "first_reject_class": str(row.get("v17_first_reject_class") or ""),
                            "render_reason": str(row.get("render_reason") or ""),
                        }
                        for arm, row in (
                            (BASELINE, base_row),
                            ("native_summary", native_row),
                            (PLUGIN, plugin_row),
                        )
                    },
                    "baseline_strict": strict_of(base_row, registration),
                    "plugin_strict": strict_of(plugin_row, registration),
                    "baseline_semantic": semantic_of(base_row, registration),
                    "plugin_semantic": semantic_of(plugin_row, registration),
                    "baseline_tool_calls": int(base_row.get("tool_calls", 0) or 0),
                    "plugin_tool_calls": int(plugin_row.get("tool_calls", 0) or 0),
                }
            )
        per_task[task_id] = {
            "paired_n": len(savings),
            "savings": savings,
            "mean": round(sum(savings) / len(savings), 6) if savings else None,
            "positive_pairs": sum(1 for value in savings if value > 0),
            "strict_track": strict,
            "semantic_correct": semantic,
            "model_calls": calls,
            "tool_calls": tools,
            "plugin_strict_at_least_baseline": strict[PLUGIN] >= strict[BASELINE],
            "plugin_semantic_at_least_baseline": semantic[PLUGIN] >= semantic[BASELINE],
            "every_arm_strict_at_least_baseline": all(
                strict[arm] >= strict[BASELINE] for arm in ARMS
            ),
            "every_arm_semantic_at_least_baseline": all(
                semantic[arm] >= semantic[BASELINE] for arm in ARMS
            ),
        }

    positives = sum(1 for value in pooled if value > 0)
    mean = sum(pooled) / len(pooled) if pooled else 0.0
    strict_all_tasks = all(
        entry["plugin_strict_at_least_baseline"] for entry in per_task.values()
    )
    semantic_all_tasks = all(
        entry["plugin_semantic_at_least_baseline"] for entry in per_task.values()
    )
    every_arm_strict = all(
        entry["every_arm_strict_at_least_baseline"] for entry in per_task.values()
    )
    every_arm_semantic = all(
        entry["every_arm_semantic_at_least_baseline"] for entry in per_task.values()
    )
    criterion_met = (
        bool(pooled)
        and matrix_complete
        and strict_all_tasks
        and positives * 2 > len(pooled)
        and mean > 0
    )
    contract_check(
        "the_primary_criterion_recomputes_and_its_quality_guards_hold",
        criterion_met and semantic_all_tasks,
        {
            "pooled_mean": round(mean, 6),
            "positive_pairs": positives,
            "paired_n": len(pooled),
            "strict_all_tasks": strict_all_tasks,
            "semantic_all_tasks": semantic_all_tasks,
            "per_task_strict": {t: e["strict_track"] for t, e in per_task.items()},
            "per_task_semantic": {t: e["semantic_correct"] for t, e in per_task.items()},
        },
    )
    if mean <= 0:
        errors.append("the_batch_mean_is_not_positive")
    if not strict_all_tasks:
        errors.append("a_tasks_plugin_strict_track_is_below_its_baseline")
    if not semantic_all_tasks:
        errors.append("a_tasks_plugin_semantic_correctness_is_below_its_baseline")

    # ------------------------------------------------------- the amendment's four clauses, decided
    first_attempt = {
        "accepted": sum(1 for row in rows if bool(row.get("v17_first_attempt_accepted"))),
        "of_samples": len(rows),
    }
    first_attempt["rate"] = (
        round(first_attempt["accepted"] / first_attempt["of_samples"], 4)
        if first_attempt["of_samples"]
        else None
    )
    first_attempt["replaced_line"] = (
        "the recalibration's 70 percent first-attempt acceptance line is NOT met and is not claimed: "
        "the amendment replaced it as the gate"
    )
    first_attempt["rejection_classes"] = {
        str(row.get("v17_first_reject_class")): sum(
            1
            for other in rows
            if str(other.get("v17_first_reject_class")) == str(row.get("v17_first_reject_class"))
        )
        for row in rows
        if str(row.get("v17_first_reject_class"))
    }
    first_attempt["per_arm"] = {
        arm: {
            "accepted": sum(
                1
                for row in rows
                if str(row["method"]) == arm and bool(row.get("v17_first_attempt_accepted"))
            ),
            "of_samples": sum(1 for row in rows if str(row["method"]) == arm),
            "rate": round(
                sum(
                    1
                    for row in rows
                    if str(row["method"]) == arm and bool(row.get("v17_first_attempt_accepted"))
                )
                / max(1, sum(1 for row in rows if str(row["method"]) == arm)),
                4,
            ),
            "rejection_classes": {
                str(row.get("v17_first_reject_class")): sum(
                    1
                    for other in rows
                    if str(other["method"]) == arm
                    and str(other.get("v17_first_reject_class"))
                    == str(row.get("v17_first_reject_class"))
                )
                for row in rows
                if str(row["method"]) == arm and str(row.get("v17_first_reject_class"))
            },
        }
        for arm in ARMS
    }
    first_attempt["rejected_samples"] = [
        {
            "task": str(row.get("scenario")),
            "repeat": int(row.get("repeat", -1)),
            "arm": str(row.get("method")),
            "first_reject_class": str(row.get("v17_first_reject_class") or ""),
            "first_candidate_chars": row.get("render_ledger", [{}])[-1].get("first_candidate_chars")
            if row.get("render_ledger")
            else None,
            "repair_candidate_chars": row.get("render_ledger", [{}])[-1].get("repair_candidate_chars")
            if row.get("render_ledger")
            else None,
            "render_reason": str(row.get("render_reason") or ""),
            "v24_retry_wording": str(row.get("v24_retry_wording") or ""),
        }
        for row in rows
        if not bool(row.get("v17_first_attempt_accepted"))
    ]
    rejected = [entry for entry in first_attempt["rejected_samples"]]
    recovered = [entry for entry in rejected if entry["render_reason"] == "accepted_retry"]
    recovery_rate = (
        round(len(recovered) / len(rejected), 4) if rejected else None
    )
    retried_rows = [row for row in rows if int(row.get("render_repair_calls", 0) or 0) > 0]
    retry_counts = {
        "per_arm": {
            arm: {
                "retried_samples": sum(
                    1
                    for row in rows
                    if str(row["method"]) == arm and int(row.get("render_repair_calls", 0) or 0) > 0
                ),
                "of_samples": sum(1 for row in rows if str(row["method"]) == arm),
                "retry_wording_used": sorted(
                    {
                        str(row.get("v24_retry_wording"))
                        for row in rows
                        if str(row["method"]) == arm and str(row.get("v24_retry_wording"))
                    }
                ),
            }
            for arm in ARMS
        },
        "total": len(retried_rows),
        "of_samples": len(rows),
        "rate": round(len(retried_rows) / len(rows), 4) if rows else None,
        "retries_were_charged_to_their_arm": True,
    }
    clauses = {
        "zero_render_failures_zero_truncations_zero_dropped_fields": {
            "gate_clause": "zero render failures, zero truncations, zero dropped fields",
            "measured": {
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
            },
        },
        "every_arms_strict_track_at_or_above_the_baseline": {
            "gate_clause": "every arm's strict track at or above the baseline",
            "measured": {
                "per_task": {t: e["strict_track"] for t, e in per_task.items()},
                "every_task_every_arm_at_least_baseline": every_arm_strict,
            },
        },
        "every_arms_semantic_correctness_at_or_above_the_baseline": {
            "gate_clause": "every arm's semantic correctness at or above the baseline",
            "measured": {
                "per_task": {t: e["semantic_correct"] for t, e in per_task.items()},
                "every_task_every_arm_at_least_baseline": every_arm_semantic,
            },
        },
        "retry_recovery_rate_at_least_90_percent": {
            "gate_clause": "retry recovery rate at least 90 percent, measured at five of five",
            "measured": {
                "first_attempt_rejections": len(rejected),
                "repaired_by_the_single_retry": len(recovered),
                "rate": recovery_rate,
                "line": RETRY_RECOVERY_LINE,
            },
        },
    }
    for key, entry in clauses.items():
        if key == "zero_render_failures_zero_truncations_zero_dropped_fields":
            entry["met"] = not any(entry["measured"].values())
        elif key == "every_arms_strict_track_at_or_above_the_baseline":
            entry["met"] = every_arm_strict
        elif key == "every_arms_semantic_correctness_at_or_above_the_baseline":
            entry["met"] = every_arm_semantic
        else:
            entry["met"] = bool(recovery_rate is not None and recovery_rate >= RETRY_RECOVERY_LINE)
    contract_check(
        "every_operative_clause_of_the_amendment_is_decided_from_the_rows_and_holds",
        all(entry["met"] for entry in clauses.values()),
        {key: entry["met"] for key, entry in clauses.items()},
    )
    for key, entry in clauses.items():
        if not entry["met"]:
            errors.append(f"the_amendment_clause_is_not_met: {key}")

    # ------------------------------------------------------------------ request ledger and cache
    requests = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    model_calls = sum(int(row.get("model_calls", 0) or 0) for row in rows)
    summary_calls = sum(int(row.get("summary_calls", 0) or 0) for row in rows)
    repair_calls = sum(int(row.get("render_repair_calls", 0) or 0) for row in rows)
    cap = int(freeze["matrix"]["max_api_requests"])
    contract_check(
        "the_request_ledger_identifies_itself_and_stays_inside_the_cap",
        requests == model_calls + summary_calls and requests <= cap,
        {
            "requests": requests,
            "model_calls": model_calls,
            "summary_calls": summary_calls,
            "repair_retries": repair_calls,
            "cap": cap,
            "identity": f"{requests} = {model_calls} + {summary_calls}",
            "cap_not_reached": requests < cap,
        },
    )
    if requests > cap:
        errors.append("the_request_cap_was_exceeded")
    if int(manifest.get("api_calls", 0) or 0) != requests:
        errors.append("the_manifest_api_call_total_differs_from_the_rows")

    calls: list[dict[str, Any]] = [
        call for row in rows for call in (row.get("raw_provider_usage") or [])
    ]
    off_peak = [bool(call.get("off_peak")) for call in calls if "off_peak" in call]
    cache = {
        "calls": len(calls),
        "calls_with_cache_fields": sum(1 for call in calls if call.get("cache_fields_present")),
        "calls_without_cache_fields": sum(1 for call in calls if not call.get("cache_fields_present")),
        "prompt_cache_hit_tokens_total": sum(
            int(call.get("prompt_cache_hit_tokens", 0) or 0) for call in calls
        ),
        "prompt_cache_miss_tokens_total": sum(
            int(call.get("prompt_cache_miss_tokens", 0) or 0) for call in calls
        ),
        "all_calls_off_peak": all(off_peak) if off_peak else None,
    }
    contract_check(
        "the_cache_split_is_readable_from_the_rows",
        cache["calls"] > 0 and cache["calls_with_cache_fields"] == cache["calls"],
        cache,
    )

    # ---------------------------------------------------------------------- the mechanism
    mechanism_per_task: dict[str, Any] = {}
    determinism: dict[str, Any] = {}
    guard_fallbacks = 0
    for task_id in task_ids:
        plugin_rows = [
            index[(task_id, repeat, PLUGIN)]
            for repeat in repeats
            if (task_id, repeat, PLUGIN) in index
        ]
        mechanism_per_task[task_id] = {
            field: sum(int(row.get(field, 0) or 0) for row in plugin_rows)
            for field in (
                "v21_outputs_considered",
                "v21_outputs_elided",
                "v21_lines_elided",
                "v21_bytes_saved",
                "v21_literal_guard_fallbacks",
                "v21_note_bytes",
            )
        }
        guard_fallbacks += mechanism_per_task[task_id]["v21_literal_guard_fallbacks"]
        determinism[task_id] = {
            "per_repeat_bytes_saved": [
                int(row.get("v21_bytes_saved", 0) or 0) for row in plugin_rows
            ],
            "per_repeat_outputs_elided": [
                int(row.get("v21_outputs_elided", 0) or 0) for row in plugin_rows
            ],
            "per_repeat_lines_elided": [
                int(row.get("v21_lines_elided", 0) or 0) for row in plugin_rows
            ],
            "per_repeat_note_bytes": [
                int(row.get("v21_note_bytes", 0) or 0) for row in plugin_rows
            ],
            "bytes_saved_identical_across_repeats": len(
                {int(row.get("v21_bytes_saved", 0) or 0) for row in plugin_rows}
            )
            == 1,
        }
    contract_check(
        "the_mechanism_engaged_on_every_task_on_every_repeat",
        all(
            entry["v21_outputs_elided"] > 0 and entry["v21_bytes_saved"] > 0
            for entry in mechanism_per_task.values()
        ),
        mechanism_per_task,
    )
    defect_disclosure = dict(manifest.get("v24_defect_view_round_disclosure") or {})
    contract_check(
        "the_defect_view_round_disclosure_is_present_for_every_task",
        sorted(defect_disclosure) == sorted(task_ids)
        and all(
            entry.get("defect_view_round") in (1, 2, 3)
            for entry in defect_disclosure.values()
        ),
        {
            task: {
                "defect_view_round": entry.get("defect_view_round"),
                "is_the_newest_tool_group": entry.get("is_the_newest_tool_group"),
                "defect_symbol": entry.get("defect_symbol"),
            }
            for task, entry in defect_disclosure.items()
        },
    )

    # ------------------------------------------------------------------ quality endpoints per arm
    prices = frozen_prices()
    off_peak_all = bool(off_peak) and all(off_peak)
    multiplier = (
        float(prices["off_peak_multiplier"])
        if off_peak_all and prices.get("off_peak_multiplier") is not None
        else 1.0
    )
    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if str(row["method"]) == arm]
        hit = sum(
            int(call.get("prompt_cache_hit_tokens", 0) or 0)
            for row in arm_rows
            for call in (row.get("raw_provider_usage") or [])
        )
        miss = sum(
            int(call.get("prompt_cache_miss_tokens", 0) or 0)
            for row in arm_rows
            for call in (row.get("raw_provider_usage") or [])
        )
        output_tokens = sum(int(row.get("actual_output_tokens", 0) or 0) for row in arm_rows)
        cost = None
        if all(
            prices.get(key) is not None
            for key in (
                "input_cache_hit_usd_per_million",
                "input_cache_miss_usd_per_million",
                "output_usd_per_million",
            )
        ):
            cost = round(
                (
                    hit * float(prices["input_cache_hit_usd_per_million"])
                    + miss * float(prices["input_cache_miss_usd_per_million"])
                    + output_tokens * float(prices["output_usd_per_million"])
                )
                / 1_000_000
                * multiplier,
                6,
            )
        per_arm[arm] = {
            "samples": len(arm_rows),
            "strict_track": sum(1 for row in arm_rows if strict_of(row, registration)),
            "strict_rate": round(
                sum(1 for row in arm_rows if strict_of(row, registration)) / max(1, len(arm_rows)), 4
            ),
            "semantic_accuracy": sum(1 for row in arm_rows if semantic_of(row, registration)),
            "semantic_accuracy_rate": round(
                sum(1 for row in arm_rows if semantic_of(row, registration))
                / max(1, len(arm_rows)),
                4,
            ),
            "first_attempt_accepted": sum(
                1 for row in arm_rows if bool(row.get("v17_first_attempt_accepted"))
            ),
            "retried_samples": sum(
                1 for row in arm_rows if int(row.get("render_repair_calls", 0) or 0) > 0
            ),
            "success": sum(1 for row in arm_rows if row.get("success")),
            "model_calls_total": sum(int(row.get("model_calls", 0) or 0) for row in arm_rows),
            "model_calls_distribution": sorted(
                int(row.get("model_calls", 0) or 0) for row in arm_rows
            ),
            "tool_calls_total": sum(int(row.get("tool_calls", 0) or 0) for row in arm_rows),
            "tool_calls_distribution": sorted(
                int(row.get("tool_calls", 0) or 0) for row in arm_rows
            ),
            "requests_total": sum(int(row.get("api_request_attempts", 0) or 0) for row in arm_rows),
            "total_provider_tokens": sum(
                int(row.get("all_arm_total_tokens", 0) or 0) for row in arm_rows
            ),
            "input_tokens": sum(int(row.get("actual_input_tokens", 0) or 0) for row in arm_rows),
            "output_tokens": output_tokens,
            "input_cache_hit_tokens": hit,
            "input_cache_miss_tokens": miss,
            "cost_usd": cost,
        }
    batch_cost = (
        round(sum(entry["cost_usd"] for entry in per_arm.values()), 6)
        if all(entry["cost_usd"] is not None for entry in per_arm.values())
        else None
    )

    # ------------------------------------------------------------------ the v16 side-by-side
    v16_number = ""
    v16_source = ""

    def _walk(node: Any) -> None:
        nonlocal v16_number
        if v16_number:
            return
        if isinstance(node, str):
            match = re.search(r"is ([\d.]+) percent", node)
            if match:
                v16_number = match.group(1)
        elif isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    for candidate in (SEALED_V16_CITATION, CITATION_AMENDMENT):
        if candidate.is_file() and not v16_number:
            _walk(_read(candidate))
            if v16_number:
                v16_source = str(candidate.relative_to(ROOT)).replace("\\", "/")
    if not v16_number:
        warnings.append("the v16 sealed citation record's number could not be re-read for the side-by-side")

    recalibration = _read(RECALIBRATION_REPORT)
    calibration_context = {
        "batch": str(RECALIBRATION_REPORT.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha256_of(RECALIBRATION_REPORT) if RECALIBRATION_REPORT.is_file() else "",
        "first_attempt_acceptance": (
            recalibration.get("first_attempt_acceptance") or {}
        ).get("rate"),
        "per_arm_first_attempt_acceptance": {
            arm: entry.get("first_attempt_acceptance_rate")
            for arm, entry in (recalibration.get("per_arm") or {}).items()
        },
        "citable": recalibration.get("citable"),
        "rule": (
            "the recalibration's numbers are context for this batch and are never citable and never "
            "pooled with it; the amendment's own measurement of five of five retry recoveries comes "
            "from that batch and is disclosed as such"
        ),
    }

    report = {
        "schema": "openai_agents_v24_length_compliance_k1_report",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "batch_directory": str(BATCH.relative_to(ROOT)).replace("\\", "/"),
        "batch_identity_resolution": {
            "id_the_batch_ran_under": EXPERIMENT_ID,
            "id_the_delegate_asked_for": REQUESTED_EXPERIMENT_ID,
            "id_the_confirmation_freeze_pre_registers": EXPERIMENT_ID,
            "why_the_frozen_id_won": (
                "the frozen runner's own ``forced_args`` replaces ``--experiment-id`` with the id "
                "the confirmation freeze names, so no other id can reach the paid path; the run "
                "directory is therefore the frozen one and this audit reads it as it is rather than "
                "renaming an artifact that is already written down"
            ),
            "one_paid_run_under_this_id": True,
            "no_resume": True,
            "manifest_written_in_one_pass": bool(
                (manifest.get("metering") or {}).get("manifest_written_in_one_pass")
            ),
        },
        "under_this_host_configuration": REQUIRED_WORDING,
        "mechanism": {
            "name": MECHANISM_IS,
            "k": 1,
            "config_module": "experiments/runners/openai_agents_v22_window_k1.py",
            "chain": "experiments/runners/openai_agents_v22_chain.py",
            "rule": (
                "the frozen v12 exact duplicate rule, then the v5 lineage evidence-safe retention, "
                "then the literal-carrier line elision: every line carrying a registered literal is "
                "kept verbatim with one line before and after it, every other line is elided, each "
                "maximal omitted run becomes one interval note, and the most recent tool group is "
                "kept verbatim"
            ),
        },
        "task_set": task_ids,
        "matrix": {
            "tasks": len(task_ids),
            "repeats": len(repeats),
            "arms": list(ARMS),
            "samples": len(rows),
            "cap": cap,
            "complete": matrix_complete,
        },
        "gate_amendment": {
            "amendment": amendment_pin,
            "the_replaced_line": (
                "the recalibration's 70 percent first-attempt acceptance line is replaced as the "
                "gate by the amendment's four operative clauses; this batch does NOT report itself "
                "as having met the 70 percent line"
            ),
            "amended_gate": {
                "path": str(AMENDED_GATE.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(AMENDED_GATE) if AMENDED_GATE.is_file() else "",
                "paid_run_allowed": amended_gate.get("paid_run_allowed"),
                "checks": len(amended_gate.get("checks") or []),
            },
            "original_gate": {
                "path": str(GATE.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(GATE),
                "untouched": True,
            },
            "delegate": {
                "module": str(DELEGATE.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DELEGATE) if DELEGATE.is_file() else "",
                "frozen_runner_carries_the_paid_path": str(FROZEN_RUNNER.relative_to(ROOT)).replace(
                    "\\", "/"
                ),
            },
            "residual_risk_carried_into_this_batch": (
                "at 44 percent first-attempt acceptance about half of the samples were expected to "
                "spend the single billed retry; retries are charged to their arm, they add calls to "
                "a trajectory, and in earlier batches one asymmetric retry moved a pooled mean by 7 "
                "to 21 percentage points"
            ),
        },
        "operative_clauses": clauses,
        "first_attempt_acceptance": first_attempt,
        "retries": retry_counts,
        "pairs": pairs,
        "primary_criterion": {
            "name": "as-paired complete provider tokens",
            "definition": (
                "for every (task, repeat) pair: (baseline total provider tokens - plugin total "
                "provider tokens) / baseline total provider tokens, where a sample's total is its "
                "complete provider input tokens plus its complete provider output tokens, with "
                "retries and summary requests charged to the arm they belong to"
            ),
            "matrix_complete": matrix_complete,
            "per_task": {
                task_id: {
                    "paired_n": entry["paired_n"],
                    "mean": entry["mean"],
                    "savings": entry["savings"],
                    "positive_pairs": entry["positive_pairs"],
                    "strict_track": entry["strict_track"],
                    "semantic_correct": entry["semantic_correct"],
                }
                for task_id, entry in per_task.items()
            },
            "pooled": {
                **statistics(pooled),
                "mean_saving": round(mean, 6),
                "majority_positive": positives * 2 > len(pooled) if pooled else False,
                "bootstrap": bootstrap(pooled),
            },
            "strict_all_tasks_at_least_baseline": strict_all_tasks,
            "semantic_all_tasks_at_least_baseline": semantic_all_tasks,
            "met": criterion_met,
        },
        "co_reported": {
            "symmetric_matched_prefix_tokens": statistics(
                [pair["matched_prefix_tokens"] for pair in pairs]
            ),
            "symmetric_matched_prefix_bytes": statistics(
                [pair["matched_prefix_bytes"] for pair in pairs]
            ),
            "symmetric_matched_prefix_rule": (
                "per pair, k = min(baseline model calls, plugin model calls) and the same first k "
                "calls of each arm are compared; the truncation is symmetric by construction and "
                "each pair records the two call counts it was taken over"
            ),
            "per_arm_quality": per_arm,
            "per_pair_retrying_arm": [
                {
                    "task": pair["task"],
                    "repeat": pair["repeat"],
                    "retrying_arm": pair["retrying_arm"],
                    "baseline_calls": pair["baseline_calls"],
                    "plugin_calls": pair["plugin_calls"],
                    "retry_labels": pair["retry_labels"],
                }
                for pair in pairs
            ],
            "mechanism_per_task": mechanism_per_task,
            "mechanism_determinism_per_task": determinism,
            "guard_fallbacks_total": guard_fallbacks,
            "defect_view_round_disclosure": defect_disclosure,
            "requests": {
                "requests": requests,
                "model_calls": model_calls,
                "summary_calls": summary_calls,
                "repair_retries": repair_calls,
                "cap": cap,
                "identity": f"{requests} = {model_calls} + {summary_calls}",
                "cap_not_reached": requests < cap,
            },
            "cost": {
                "prices": prices,
                "multiplier_applied": multiplier,
                "all_calls_off_peak": off_peak_all,
                "per_arm_usd": {arm: entry["cost_usd"] for arm, entry in per_arm.items()},
                "batch_total_usd": batch_cost,
            },
            "cache": cache,
        },
        "side_by_side_with_the_v16_sealed_batch": {
            "v16_sealed_paired_mean_percent": v16_number,
            "v16_number_read_from": v16_source,
            "this_batch_pooled_mean_percent": round(mean * 100, 4),
            "rule": (
                "reported side by side and never merged and never pooled: the two batches differ in "
                "host configuration, in mechanism and in task set, and this batch's number is "
                "citable only under this host configuration and only for the mechanism it names"
            ),
            "v22_v23_v24_recalibration_numbers": (
                "the v22, v23 and v24 recalibration numbers are not citable anywhere in this report "
                "and are not used to support it; the recalibration appears only as the amendment's "
                "own measurement of the retry, alongside this batch's own retry count"
            ),
        },
        "calibration_context": calibration_context,
        "audit_outcome": {
            "complete": not errors,
            "errors": errors,
            "warnings": warnings,
            "usable_as_saving": not errors and criterion_met,
        },
        "cannot_show": [
            "it cannot show a general result across hosts, models or configurations: it is one host "
            "configuration, one mechanism at one window, and three tasks",
            "it cannot separate the window K's contribution from the mechanism's, because this "
            "batch runs at one window only; the dose was measured on the development tasks",
            "it cannot attribute the saving to the mechanism alone where the plugin arm's trajectory "
            "differs from the baseline's; that is why the symmetric matched-prefix reading is "
            "reported beside the primary one",
            "it cannot establish anything about the two tasks whose defect view is the first read "
            "beyond what the literal invariant shows, and for the task whose defect view is the "
            "newest tool group that view was never at risk of elision at all",
            "it cannot make the v16 sealed batch's 12.54 percent comparable: the two are reported "
            "side by side and are never merged",
            "it cannot rule out that a different sample size would move the pooled mean: five "
            "repeats per task is what the freeze pre-registered",
            "it cannot claim the 70 percent first-attempt line the recalibration set and missed: "
            "that line was replaced as the gate by the amendment, and this batch reports its own "
            "first-attempt rate as measured",
            "it cannot show that the mechanism is safe on content whose only carrier is a line that "
            "is not a registered literal: the elision is guarantee-preserving per the literal "
            "invariant, not per free text",
        ],
        "wording": {
            "required": REQUIRED_WORDING,
            "forbidden": list(FORBIDDEN_WORDING),
            "checked": "the report text contains the required wording and none of the forbidden phrases",
        },
        "citable": False,
    }

    scanned = json.loads(json.dumps(report, ensure_ascii=False))
    scanned["wording"] = {
        "required": REQUIRED_WORDING,
        "checked": report["wording"]["checked"],
        "forbidden_phrases_listed_below": "yes, and excluded from this scan",
    }
    scanning_text = json.dumps(scanned, ensure_ascii=False)
    found = [phrase for phrase in FORBIDDEN_WORDING if phrase in scanning_text]
    report["wording"]["scan_excludes_the_list_itself"] = True
    report["wording"]["forbidden_phrases_found_in_the_report_text"] = found
    if REQUIRED_WORDING not in scanning_text:
        errors.append("the_report_does_not_carry_the_required_wording")
    for phrase in found:
        errors.append(f"the_report_contains_the_forbidden_phrase: {phrase}")
    report["audit_outcome"]["complete"] = not errors
    report["audit_outcome"]["errors"] = list(errors)
    report["audit_outcome"]["usable_as_saving"] = not errors and criterion_met

    audit_payload = {
        "schema": "openai_agents_v24_length_compliance_k1_independent_audit",
        "audit_id": "openai-agents-v24-length-compliance-k1-audit-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/audit_openai_agents_v24_length_compliance_k1.py",
        "does_not_modify": [
            str(BATCH.relative_to(ROOT)).replace("\\", "/"),
            str(FREEZE.relative_to(ROOT)).replace("\\", "/"),
            str(REGISTRATION.relative_to(ROOT)).replace("\\", "/"),
            str(GATE.relative_to(ROOT)).replace("\\", "/"),
            str(AMENDED_GATE.relative_to(ROOT)).replace("\\", "/"),
            str(AMENDMENT.relative_to(ROOT)).replace("\\", "/"),
        ],
        "inputs": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256_of(path)
            for path in (
                FREEZE,
                REGISTRATION,
                V24_REGISTRATION,
                GATE,
                AMENDED_GATE,
                AMENDMENT,
                LEDGER,
                PLAN,
                STUB,
                DELEGATE,
                FROZEN_RUNNER,
                BATCH / "manifest.json",
                BATCH / "samples.jsonl",
            )
            if path.is_file()
        },
        "frozen_contract": contract,
        "recomputed": {
            "four_conditions": condition_detail,
            "primary_criterion": report["primary_criterion"],
            "operative_clauses": clauses,
            "first_attempt_acceptance": first_attempt,
            "retries": retry_counts,
            "co_reported": report["co_reported"],
        },
        "complete": not errors,
        "errors": errors,
        "warnings": warnings,
        "usable_as_saving": not errors and criterion_met,
        "not_citable_statement": (
            ""
            if (not errors and criterion_met)
            else "this batch may not be cited as an effective saving until the errors above are "
            "resolved"
        ),
        "recomputed_acceptance_matches_runner": criterion_met
        == bool((manifest.get("pilot_report") or {}).get("met")),
        "manifest_acceptance_met": bool((manifest.get("pilot_report") or {}).get("met")),
        "citable": False,
    }
    return audit_payload, report


def _read_gate_delegate(amended_gate: dict[str, Any]) -> dict[str, Any]:
    return dict(amended_gate.get("delegate") or {})


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite the existing artifact {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    audit_payload, report = audit()
    print(
        json.dumps(
            {
                "complete": audit_payload["complete"],
                "usable_as_saving": audit_payload["usable_as_saving"],
                "errors": audit_payload["errors"],
            },
            ensure_ascii=False,
        )
    )
    print(
        "pooled as-paired mean: {mean}  positive {pos}/{n}  strict all tasks {strict}  "
        "semantic {sem}  first attempt {fa}".format(
            mean=report["primary_criterion"]["pooled"]["mean_saving"],
            pos=report["primary_criterion"]["pooled"]["positive"],
            n=report["primary_criterion"]["pooled"]["n"],
            strict=report["primary_criterion"]["strict_all_tasks_at_least_baseline"],
            sem=report["primary_criterion"]["semantic_all_tasks_at_least_baseline"],
            fa=report["first_attempt_acceptance"]["rate"],
        )
    )
    print(
        "clauses: "
        + json.dumps(
            {key: entry["met"] for key, entry in report["operative_clauses"].items()},
            ensure_ascii=False,
        )
    )
    if not args.probe:
        _write_once(AUDIT_OUT, audit_payload)
        _write_once(REPORT_OUT, report)
        print(f"wrote {AUDIT_OUT.relative_to(ROOT)}")
        print(f"wrote {REPORT_OUT.relative_to(ROOT)}")
    return 0 if audit_payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
