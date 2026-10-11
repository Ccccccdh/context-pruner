"""The independent audit of the v23 K=1 sealed batch: recompute, then judge.

This script shares no code with the runner that produced the batch except the frozen renderer's
constants it needs to re-derive the four conditions, and it recomputes every number it judges from
the persisted sample rows:

* the frozen contract: the freeze digest, the module digests, the source-of-record digests, the cap
  arithmetic and the acceptance line, each re-hashed here;
* the four conditions per sample, rebuilt from the rendered line, the registered regex, the
  registered facts and the 160 character bound - and compared with the verdict the host recorded,
  so a disagreement is an audit error rather than a silent pass;
* the primary criterion (``as-paired`` complete provider tokens) per task and pooled, with the
  strict-track comparison, the count of positive pairs and a seeded bootstrap interval;
* the co-reported readings: the symmetric matched prefix in tokens and in context bytes, the strict
  track and semantic accuracy per arm, per-arm call counts, which arm retried in each pair, the
  mechanism counters, the determinism of the mechanism's byte saving across repeats, the request
  ledger against its cap, and the provider cache split with the off-peak flag.

It writes two artifacts: the audit (``complete``, ``errors``, ``usable_as_saving``) and the report
block the round's freeze pre-registered.  Nothing it writes may be merged with the v16 sealed
batch's number, and it says so in its own words.
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
EXPERIMENT_ID = "openai-repo-diagnostic-v23-v18-sealed-window-k1-01"
BATCH = RUNS / EXPERIMENT_ID

FREEZE = ROOT / "integrations/openai_agents/V18_SEALED_CONFIRMATION_FREEZE_20261010.json"
REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
GATE = ROOT / "integrations/openai_agents/V18_SEALED_K1_ZERO_API_GATE_20261010.json"
LEDGER = ROOT / "integrations/openai_agents/V18_SEALED_K1_MODULE_REVISIONS_20261010.json"
REGRESSION = ROOT / "integrations/openai_agents/V18_SEALED_K1_FOCUSED_REGRESSION_RUN2_20261010.json"
REGRESSION_RUN1 = (
    ROOT / "integrations/openai_agents/V18_SEALED_K1_FOCUSED_REGRESSION_20261010.json"
)
PLAN = ROOT / "integrations/openai_agents/V18_SEALED_K1_LIVE_PLAN_20261010.json"
STUB = ROOT / "integrations/openai_agents/V18_SEALED_K1_LIVE_STUB_20261010.json"
SEALED_V16_CITATION = (
    ROOT / "integrations/openai_agents/V16_SEALED_CONFIRMATION_CITATION_RECORD_20261006.json"
)

AUDIT_OUT = ROOT / "integrations/openai_agents/V18_SEALED_WINDOW_K1_AUDIT_20261010.json"
REPORT_OUT = ROOT / "integrations/openai_agents/V18_SEALED_WINDOW_K1_REPORT_20261010.json"

ARMS = ("none", "native_summary", "pruner_v1")
PLUGIN = "pruner_v1"
BASELINE = "none"
MAX_CHARS = 160
BOOTSTRAP_SEED = 20261010
BOOTSTRAP_ROUNDS = 10000
FORBIDDEN_WORDING = (
    "compression alone saved X percent",
    "the four criteria are met",
    "the retry tax is gone",
    "the plugin saves about 12.5 percent of context",
)
REQUIRED_WORDING = "under this host configuration"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def per_call_bytes(row: dict[str, Any]) -> list[int]:
    staged: dict[int, list[int]] = defaultdict(list)
    for entry in row.get("evidence_input_bytes_by_boundary") or []:
        if entry.get("stage") == "model_input":
            staged[int(entry.get("index", 0))].append(int(entry.get("input_bytes", 0) or 0))
    return [max(values) for _key, values in sorted(staged.items())]


def recompute_conditions(
    line: str, issue: str, pattern: str, facts: list[str]
) -> dict[str, bool]:
    """The four conditions, rebuilt from the rendered line and the registered contract."""

    lowered = str(line).lower()
    conditions = {
        "facts_complete": all(str(fact).lower() in lowered for fact in facts),
        "frozen_regex": re.fullmatch(pattern, str(line), flags=re.IGNORECASE) is not None,
        "single_line_prefix": str(line).startswith("RESULT ") and "\n" not in str(line),
        "within_160": 0 < len(str(line)) <= MAX_CHARS,
    }
    return conditions


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
        return {"rounds": 0, "seed": BOOTSTRAP_SEED, "lower": None, "upper": None, "excludes_zero": None}
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    n = len(values)
    for _ in range(BOOTSTRAP_ROUNDS):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lower = means[int(0.025 * len(means))]
    upper = means[int(0.975 * len(means)) - 1]
    return {
        "rounds": BOOTSTRAP_ROUNDS,
        "seed": BOOTSTRAP_SEED,
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "excludes_zero": lower > 0 or upper < 0,
        "note": "reported for explanation; the frozen verdict is the per-task strict track, the majority and the mean",
    }


def audit() -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not BATCH.is_dir():
        raise SystemExit(f"the batch directory is missing: {BATCH}")
    manifest = json.loads((BATCH / "manifest.json").read_text(encoding="utf-8"))
    rows = load_rows()
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- the frozen contract
    contract: dict[str, Any] = {"checks": []}

    def contract_check(name: str, ok: bool, detail: Any = None) -> None:
        contract["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append(name)

    contract_check(
        "the_manifest_cites_the_frozen_confirmation_freeze",
        str(manifest.get("freeze_path")) == str(FREEZE.relative_to(ROOT)).replace("\\", "/")
        and str(manifest.get("freeze_sha256")) == sha256_of(FREEZE),
        {
            "manifest_freeze_path": str(manifest.get("freeze_path")),
            "manifest_freeze_sha256": str(manifest.get("freeze_sha256")),
            "recomputed_freeze_sha256": sha256_of(FREEZE),
        },
    )
    live_modules = {
        relative: sha256_of(ROOT / relative)
        for relative in freeze["host_configuration"]["modules"]
    }
    drifted = sorted(
        relative
        for relative, digest in live_modules.items()
        if digest != freeze["host_configuration"]["modules"][relative]
    )
    contract_check("every_pinned_module_still_matches_the_freeze", not drifted, {"drifted": drifted})
    contract_check(
        "the_manifest_copies_the_frozen_module_hashes",
        dict(manifest.get("module_hashes") or {}) == dict(manifest.get("implementation_hashes") or {})
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
    contract_check("every_source_of_record_file_still_matches", not source_drift, {"drifted": source_drift})
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
    contract_check(
        "the_manifest_records_the_finalised_flag_and_the_pre_registered_keys",
        manifest.get("v16_manifest_finalized") is True
        and not [
            name for name in freeze["manifest_contract"]["required_keys"] if name not in manifest
        ]
        and not [
            name
            for name in freeze["manifest_contract"]["extra_keys_this_round_adds"]
            if name not in manifest
        ],
        {
            "missing_required": [
                name
                for name in freeze["manifest_contract"]["required_keys"]
                if name not in manifest
            ]
        },
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
            "required_wording": manifest.get("required_wording"),
        },
    )
    gate = json.loads(GATE.read_text(encoding="utf-8")) if GATE.is_file() else {}
    contract_check(
        "the_zero_api_gate_that_allowed_the_batch_is_on_record_and_green",
        gate.get("ok") is True and gate.get("paid_run_allowed") is True and gate.get("api_calls") == 0,
        {
            "gate": str(GATE.relative_to(ROOT)).replace("\\", "/"),
            "gate_sha256": sha256_of(GATE) if GATE.is_file() else "",
            "hard_assertions": gate.get("hard_assertions"),
        },
    )
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else {}
    contract_check(
        "the_revision_ledger_pins_the_same_modules_the_freeze_does",
        dict(ledger.get("modules") or {}) == dict(freeze["host_configuration"]["modules"]),
        {"ledger_revision": ledger.get("revision"), "ledger_modules": len(ledger.get("modules") or {})},
    )
    regression = json.loads(REGRESSION.read_text(encoding="utf-8")) if REGRESSION.is_file() else {}
    run1 = json.loads(REGRESSION_RUN1.read_text(encoding="utf-8")) if REGRESSION_RUN1.is_file() else {}
    contract_check(
        "the_focused_regression_is_on_record_with_only_the_allowed_existing_failure",
        regression.get("ok") is True
        and not regression.get("unexpected_failures")
        and regression.get("allowed_failures_observed")
        == [
            "tests/test_openai_agents_v16_live_runner_zero_api.py::RegistryTests::"
            "test_problem_statement_hash_reconciliation_is_recorded"
        ],
        {
            "recorded_run": str(REGRESSION.relative_to(ROOT)).replace("\\", "/"),
            "recorded_run_sha256": sha256_of(REGRESSION) if REGRESSION.is_file() else "",
            "counts": regression.get("summary_counts"),
            "superseded_run": str(REGRESSION_RUN1.relative_to(ROOT)).replace("\\", "/"),
            "superseded_run_unexpected_failures": run1.get("unexpected_failures"),
            "why_a_second_run": (
                "the first recorded run failed three tests in other rounds' files because the new "
                "test file left the frozen modules' bindings pointed at this round's task set; the "
                "fix was confined to the new test file, the frozen modules were not touched, and "
                "the second run is the one recorded as evidence"
            ),
        },
    )
    plan = json.loads(PLAN.read_text(encoding="utf-8")) if PLAN.is_file() else {}
    stub = json.loads(STUB.read_text(encoding="utf-8")) if STUB.is_file() else {}
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
        registration["registered_task_count"] == 3
        and all(
            entry["problem_statement_sha256_matches_preselection"]
            for entry in registration["tasks"].values()
        ),
        {
            "registration": str(REGISTRATION.relative_to(ROOT)).replace("\\", "/"),
            "registration_sha256": sha256_of(REGISTRATION),
            "task_ids": registration["task_ids"],
        },
    )

    # ---------------------------------------------------------------- the sample matrix
    index = {(str(row["scenario"]), int(row["repeat"]), str(row["method"])): row for row in rows}
    task_ids = list(registration["task_ids"])
    matrix = {"tasks": task_ids, "arms": list(ARMS), "repeats": sorted({int(r["repeat"]) for r in rows})}
    contract_check(
        "the_batch_is_the_frozen_matrix",
        len(rows) == 45
        and sorted({str(row["scenario"]) for row in rows}) == sorted(task_ids)
        and sorted({str(row["method"]) for row in rows}) == sorted(ARMS)
        and sorted({int(row["repeat"]) for row in rows}) == [0, 1, 2, 3, 4],
        {"rows": len(rows), "matrix": matrix},
    )

    # ------------------------------------------------- the four conditions, recomputed
    condition_detail: list[dict[str, Any]] = []
    disagreements: list[str] = []
    failures: list[str] = []
    for task_id in task_ids:
        contract_view = registration["tasks"][task_id]["contract"]
        facts = [str(fact) for fact in contract_view["required_facts"]]
        pattern = str(contract_view["frozen_regex"])
        issue = str(contract_view["issue_value"])
        for repeat in sorted({int(row["repeat"]) for row in rows}):
            for arm in ARMS:
                row = index.get((task_id, repeat, arm))
                if row is None:
                    errors.append(f"missing sample {task_id}/{repeat}/{arm}")
                    continue
                line = str(row.get("final_output", ""))
                recomputed = recompute_conditions(line, issue, pattern, facts) if line else {
                    "facts_complete": False,
                    "frozen_regex": False,
                    "single_line_prefix": False,
                    "within_160": False,
                }
                recorded = {k: bool(v) for k, v in (row.get("render_conditions") or {}).items()}
                strict_recorded = bool(row.get("render_accepted")) and all(
                    recorded.get(name) for name in
                    ("facts_complete", "frozen_regex", "single_line_prefix", "within_160")
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
                        "conditions_recomputed": recomputed,
                        "conditions_recorded": recorded,
                        "strict_recomputed": strict_recomputed,
                        "strict_recorded": strict_recorded,
                        "answer_correct": bool(row.get("answer_correct")),
                        "final_format_correct": bool(row.get("final_format_correct")),
                        "structure_safe": bool(row.get("structure_safe")),
                        "constraint_preserved": bool(row.get("constraint_preserved")),
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
    contract_check("no_render_failure_truncation_or_dropped_field", not failures, {"failures": failures})

    # ------------------------------------------------------- the primary criterion
    pairs: list[dict[str, Any]] = []
    per_task: dict[str, Any] = {}
    pooled: list[float] = []
    missing_pairs: list[str] = []
    for task_id in task_ids:
        savings: list[float] = []
        strict = {arm: 0 for arm in ARMS}
        correct = {arm: 0 for arm in ARMS}
        calls = {arm: 0 for arm in ARMS}
        tools = {arm: 0 for arm in ARMS}
        for repeat in sorted({int(row["repeat"]) for row in rows}):
            if any((task_id, repeat, arm) not in index for arm in ARMS):
                missing_pairs.append(f"{task_id}/{repeat}")
                continue
            base_row = index[(task_id, repeat, BASELINE)]
            plugin_row = index[(task_id, repeat, PLUGIN)]
            native_row = index[(task_id, repeat, "native_summary")]
            for arm, row in ((BASELINE, base_row), (PLUGIN, plugin_row), ("native_summary", native_row)):
                strict[arm] += 1 if _strict_of(row, registration, task_ids) else 0
                correct[arm] += 1 if row.get("answer_correct") else 0
                calls[arm] += int(row.get("model_calls", 0) or 0)
                tools[arm] += int(row.get("tool_calls", 0) or 0)
            base_total = int(base_row.get("actual_input_tokens", 0) or 0) + int(
                base_row.get("actual_output_tokens", 0) or 0
            )
            plugin_total = int(plugin_row.get("actual_input_tokens", 0) or 0) + int(
                plugin_row.get("actual_output_tokens", 0) or 0
            )
            native_total = int(native_row.get("actual_input_tokens", 0) or 0) + int(
                native_row.get("actual_output_tokens", 0) or 0
            )
            value = (base_total - plugin_total) / base_total if base_total else 0.0
            tokens_base_calls = [int(v) for v in base_row.get("actual_input_tokens_by_call") or []]
            tokens_plugin_calls = [int(v) for v in plugin_row.get("actual_input_tokens_by_call") or []]
            k = min(len(tokens_base_calls), len(tokens_plugin_calls))
            matched_tokens = (
                (sum(tokens_base_calls[:k]) - sum(tokens_plugin_calls[:k]))
                / sum(tokens_base_calls[:k])
                if sum(tokens_base_calls[:k])
                else 0.0
            )
            bytes_base = per_call_bytes(base_row)
            bytes_plugin = per_call_bytes(plugin_row)
            kb = min(len(bytes_base), len(bytes_plugin))
            matched_bytes = (
                (sum(bytes_base[:kb]) - sum(bytes_plugin[:kb])) / sum(bytes_base[:kb])
                if sum(bytes_base[:kb])
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
                    "matched_prefix_tokens": round(matched_tokens, 6),
                    "matched_prefix_bytes": round(matched_bytes, 6),
                    "matched_prefix_is_symmetric": True,
                    "baseline_calls": len(tokens_base_calls),
                    "plugin_calls": len(tokens_plugin_calls),
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
                    "baseline_strict": _strict_of(base_row, registration, task_ids),
                    "plugin_strict": _strict_of(plugin_row, registration, task_ids),
                    "baseline_answer_correct": bool(base_row.get("answer_correct")),
                    "plugin_answer_correct": bool(plugin_row.get("answer_correct")),
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
            "answer_correct": correct,
            "model_calls": calls,
            "tool_calls": tools,
            "plugin_strict_at_least_baseline": strict[PLUGIN] >= strict[BASELINE],
            "plugin_answer_correct_at_least_baseline": correct[PLUGIN] >= correct[BASELINE],
        }

    positives = sum(1 for value in pooled if value > 0)
    mean = sum(pooled) / len(pooled) if pooled else 0.0
    matrix_complete = not missing_pairs and len(rows) == 45
    if missing_pairs:
        errors.append(
            "the_frozen_matrix_is_incomplete: "
            + ", ".join(missing_pairs)
            + " - a stopped batch is audited as incomplete, never as a result"
        )
    strict_all_tasks = all(
        entry["plugin_strict_at_least_baseline"] for entry in per_task.values()
    )
    correct_all_tasks = all(
        entry["plugin_answer_correct_at_least_baseline"] for entry in per_task.values()
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
        criterion_met and correct_all_tasks,
        {
            "pooled_mean": round(mean, 6),
            "positive_pairs": positives,
            "paired_n": len(pooled),
            "strict_all_tasks": strict_all_tasks,
            "semantic_accuracy_all_tasks": correct_all_tasks,
            "per_task_strict": {t: e["strict_track"] for t, e in per_task.items()},
            "per_task_answer_correct": {t: e["answer_correct"] for t, e in per_task.items()},
        },
    )
    if mean <= 0:
        errors.append("the_batch_mean_is_not_positive")
    if not strict_all_tasks:
        errors.append("a_tasks_plugin_strict_track_is_below_its_baseline")
    if not correct_all_tasks:
        errors.append("a_tasks_plugin_semantic_accuracy_is_below_its_baseline")

    # ------------------------------------------------------------------ request ledger
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
        },
    )
    if int(manifest.get("api_calls", 0) or 0) != requests:
        errors.append("the_manifest_api_call_total_differs_from_the_rows")

    # ------------------------------------------------------------------ cache metering
    calls: list[dict[str, Any]] = [
        call for row in rows for call in (row.get("raw_provider_usage") or [])
    ]
    hit = sum(int(call.get("prompt_cache_hit_tokens", 0) or 0) for call in calls)
    miss = sum(int(call.get("prompt_cache_miss_tokens", 0) or 0) for call in calls)
    off_peak = [bool(call.get("off_peak")) for call in calls if "off_peak" in call]
    cache = {
        "calls_with_cache_fields": sum(1 for call in calls if call.get("cache_fields_present")),
        "calls": len(calls),
        "prompt_cache_hit_tokens_total": hit,
        "prompt_cache_miss_tokens_total": miss,
        "all_calls_off_peak": all(off_peak) if off_peak else None,
        "calls_without_cache_fields": sum(1 for call in calls if not call.get("cache_fields_present")),
    }
    contract_check(
        "the_cache_split_is_readable_from_the_rows",
        cache["calls"] > 0 and cache["calls_with_cache_fields"] == cache["calls"],
        cache,
    )

    # ------------------------------------------------------------------ the mechanism
    mechanism_per_task: dict[str, Any] = {}
    determinism: dict[str, Any] = {}
    for task_id in task_ids:
        plugin_rows = [
            index[(task_id, repeat, PLUGIN)]
            for repeat in sorted({int(r["repeat"]) for r in rows})
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
        determinism[task_id] = {
            "per_repeat_bytes_saved": [int(row.get("v21_bytes_saved", 0) or 0) for row in plugin_rows],
            "per_repeat_lines_elided": [int(row.get("v21_lines_elided", 0) or 0) for row in plugin_rows],
            "identical_across_repeats": len(
                {int(row.get("v21_bytes_saved", 0) or 0) for row in plugin_rows}
            )
            == 1,
        }
    contract_check(
        "the_mechanism_engaged_on_every_task_on_every_repeat",
        all(
            entry["v21_outputs_elided"] >= 5 and entry["v21_bytes_saved"] > 0
            for entry in mechanism_per_task.values()
        ),
        mechanism_per_task,
    )

    # ------------------------------------------------------------------ quality endpoints per arm
    prices = {
        "source": "integrations/openai_agents/V16_CACHE_METERING_AND_COST_20261006.json",
        "input_cache_miss_usd_per_million": 0.15,
        "input_cache_hit_usd_per_million": 0.003,
        "output_usd_per_million": 0.6,
        "off_peak_multiplier": 0.5,
        "note": (
            "the frozen price table's rates; the batch reports tokens and requests and converts "
            "them with these rates, applying the off-peak multiplier when every call of the batch "
            "is off peak"
        ),
    }
    off_peak_all = all(
        bool(call.get("off_peak"))
        for row in rows
        for call in (row.get("raw_provider_usage") or [])
        if "off_peak" in call
    )
    multiplier = 0.5 if off_peak_all else 1.0
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
        cost = (
            hit * 0.003 + miss * 0.15 + output_tokens * 0.6
        ) / 1_000_000 * multiplier
        per_arm[arm] = {
            "samples": len(arm_rows),
            "strict_track": sum(1 for row in arm_rows if _strict_of(row, registration, task_ids)),
            "strict_rate": round(
                sum(1 for row in arm_rows if _strict_of(row, registration, task_ids)) / len(arm_rows), 4
            ),
            "semantic_accuracy": sum(1 for row in arm_rows if row.get("answer_correct")),
            "semantic_accuracy_rate": round(
                sum(1 for row in arm_rows if row.get("answer_correct")) / len(arm_rows), 4
            ),
            "final_format_correct": sum(1 for row in arm_rows if row.get("final_format_correct")),
            "structure_safe": sum(1 for row in arm_rows if row.get("structure_safe")),
            "constraint_preserved": sum(1 for row in arm_rows if row.get("constraint_preserved")),
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
            "retries": sum(int(row.get("render_repair_calls", 0) or 0) for row in arm_rows),
            "total_provider_tokens": sum(
                int(row.get("all_arm_total_tokens", 0) or 0) for row in arm_rows
            ),
            "input_tokens": sum(int(row.get("actual_input_tokens", 0) or 0) for row in arm_rows),
            "output_tokens": output_tokens,
            "input_cache_hit_tokens": hit,
            "input_cache_miss_tokens": miss,
            "cost_usd": round(cost, 6),
        }
    batch_cost = round(sum(entry["cost_usd"] for entry in per_arm.values()), 6)

    v16_citation = (
        json.loads(SEALED_V16_CITATION.read_text(encoding="utf-8"))
        if SEALED_V16_CITATION.is_file()
        else {}
    )
    v16_number = ""

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

    _walk(v16_citation)
    if not v16_number:
        warnings.append("the v16 sealed citation record's number could not be re-read for the side-by-side")

    report = {
        "schema": "openai_agents_v18_sealed_window_k1_report",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "batch_directory": str(BATCH.relative_to(ROOT)).replace("\\", "/"),
        "window": {
            "k": 1,
            "config_module": "experiments/runners/openai_agents_v22_window_k1.py",
            "mechanism": "guaranteed-evidence line-level elision (literal-carrier elision), K=1",
            "chain": "experiments/runners/openai_agents_v22_chain.py",
        },
        "task_set": task_ids,
        "matrix": {
            "tasks": len(task_ids),
            "repeats": len(sorted({int(r["repeat"]) for r in rows})),
            "arms": list(ARMS),
            "samples": len(rows),
            "cap": cap,
        },
        "primary_criterion": {
            "name": "as-paired complete provider tokens",
            "matrix_complete": matrix_complete,
            "missing_pairs": missing_pairs,
            "partial_matrix_only": not matrix_complete,
            "partial_matrix_note": (
                "the batch stopped before the frozen matrix was complete, so these numbers describe "
                "a partial matrix; they are recorded to make the stop explicable and they may not be "
                "cited as this round's result under any reading"
                if not matrix_complete
                else "the frozen matrix is complete"
            ),
            "per_task": {
                task_id: {
                    "paired_n": entry["paired_n"],
                    "mean": entry["mean"],
                    "savings": entry["savings"],
                    "positive_pairs": entry["positive_pairs"],
                    "strict_track": entry["strict_track"],
                    "semantic_accuracy": entry["answer_correct"],
                }
                for task_id, entry in per_task.items()
            },
            "pooled": {
                **statistics(pooled),
                "mean_saving": round(mean, 6),
                "majority_positive": positives * 2 > len(pooled),
                "bootstrap": bootstrap(pooled),
            },
            "strict_all_tasks_at_least_baseline": strict_all_tasks,
            "semantic_accuracy_all_tasks_at_least_baseline": correct_all_tasks,
            "met": criterion_met,
            "pairs": pairs,
        },
        "co_reported": {
            "symmetric_matched_prefix_tokens": statistics(
                [pair["matched_prefix_tokens"] for pair in pairs]
            ),
            "symmetric_matched_prefix_bytes": statistics(
                [pair["matched_prefix_bytes"] for pair in pairs]
            ),
            "per_arm_quality": per_arm,
            "per_pair_retrying_arm": [
                {
                    "task": pair["task"],
                    "repeat": pair["repeat"],
                    "retrying_arm": pair["retrying_arm"],
                    "baseline_calls": pair["baseline_calls"],
                    "plugin_calls": pair["plugin_calls"],
                }
                for pair in pairs
            ],
            "mechanism_per_task": mechanism_per_task,
            "mechanism_determinism_per_task": determinism,
            "requests": {
                "requests": requests,
                "model_calls": model_calls,
                "summary_calls": summary_calls,
                "repair_retries": repair_calls,
                "cap": cap,
                "identity": f"{requests} = {model_calls} + {summary_calls}",
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
        "audit_outcome": {
            "complete": not errors,
            "errors": errors,
            "warnings": warnings,
            "usable_as_saving": not errors and criterion_met,
        },
        "side_by_side_with_the_v16_sealed_batch": {
            "v16_sealed_paired_mean_percent": v16_number,
            "this_batch_pooled_mean_percent": round(mean * 100, 4),
            "rule": (
                "reported side by side and never merged and never pooled: the two batches differ in "
                "host configuration, in mechanism and in task set, and this batch's number is "
                "citable only under this host configuration and only for the mechanism it names"
            ),
            "v22_development_numbers": (
                "the v22 development groups' numbers are not citable anywhere in this report and "
                "are not used to support it"
            ),
        },
        "cannot_show": [
            "it cannot show a general result across hosts, models or configurations: it is one host "
            "configuration, one mechanism at one window, and three tasks",
            "it cannot separate the window K's contribution from the mechanism's, because this "
            "batch runs at one window only; the dose was measured on the development tasks",
            "it cannot attribute the saving to the mechanism alone where the plugin arm's "
            "trajectory differs from the baseline's; that is why the symmetric matched-prefix "
            "reading is reported beside the primary one",
            "it cannot establish anything about the two tasks whose defect view is the first read "
            "beyond what the literal invariant shows; and for django-15572 the defect view is the "
            "newest tool group, so that view was never at risk",
            "it cannot make the v16 sealed batch's 12.54 percent comparable: the two are reported "
            "side by side and are never merged",
            "it cannot rule out that a different sample size would move the pooled mean: five "
            "repeats per task is what the freeze pre-registered",
        ],
        "wording": {
            "required": REQUIRED_WORDING,
            "forbidden": list(FORBIDDEN_WORDING),
            "checked": "the report text contains the required wording and none of the forbidden phrases",
        },
        "citable": False,
    }
    # The forbidden phrases are *listed* in the report on purpose (the freeze requires the list to
    # be on record), so the scan below removes the list itself before searching: a check that
    # tripped on its own record of the ban would be a check that can never pass.
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
        "schema": "openai_agents_v18_sealed_window_k1_independent_audit",
        "audit_id": "openai-agents-v18-sealed-window-k1-audit-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/audit_openai_agents_v18_sealed_window_k1.py",
        "does_not_modify": [
            str(BATCH.relative_to(ROOT)).replace("\\", "/"),
            str(FREEZE.relative_to(ROOT)).replace("\\", "/"),
            str(REGISTRATION.relative_to(ROOT)).replace("\\", "/"),
            str(GATE.relative_to(ROOT)).replace("\\", "/"),
        ],
        "inputs": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256_of(path)
            for path in (
                FREEZE,
                REGISTRATION,
                GATE,
                LEDGER,
                PLAN,
                STUB,
                REGRESSION,
                BATCH / "manifest.json",
                BATCH / "samples.jsonl",
            )
            if path.is_file()
        },
        "frozen_contract": contract,
        "recomputed": {
            "four_conditions": condition_detail,
            "primary_criterion": report["primary_criterion"],
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
        "recomputed_acceptance_matches_runner": criterion_met == bool(
            (manifest.get("pilot_report") or {}).get("met")
        ),
        "manifest_acceptance_met": bool((manifest.get("pilot_report") or {}).get("met")),
        "citable": False,
    }
    return audit_payload, report


def _strict_of(row: dict[str, Any], registration: dict[str, Any], task_ids: list[str]) -> bool:
    """The strict track, recomputed from the rendered line, for one row."""

    task_id = str(row["scenario"])
    contract_view = registration["tasks"][task_id]["contract"]
    line = str(row.get("final_output", ""))
    if not line:
        return False
    conditions = recompute_conditions(
        line,
        str(contract_view["issue_value"]),
        str(contract_view["frozen_regex"]),
        [str(fact) for fact in contract_view["required_facts"]],
    )
    return all(conditions.values())


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
    print(json.dumps({"complete": audit_payload["complete"], "errors": audit_payload["errors"]}, ensure_ascii=False))
    print(
        "pooled as-paired mean: {mean}  positive {pos}/{n}  strict all tasks {strict}  semantic {sem}".format(
            mean=report["primary_criterion"]["pooled"]["mean_saving"],
            pos=report["primary_criterion"]["pooled"]["positive"],
            n=report["primary_criterion"]["pooled"]["n"],
            strict=report["primary_criterion"]["strict_all_tasks_at_least_baseline"],
            sem=report["primary_criterion"]["semantic_accuracy_all_tasks_at_least_baseline"],
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
