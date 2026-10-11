"""Record why the v23 K=1 sealed batch stopped, from the partial rows themselves.

The batch hit the freeze's own stop condition - a sample whose answer could not be rendered is a
render failure, and it is reported, not absorbed into a mean.  This script recomputes every number
it records from the persisted partial rows: how many samples exist, which task could not render in
which arm, what the host's rejection reason was for each, what the partial paired comparison looks
like (recorded so the stop is explicable, and explicitly not a result), what the mechanism did on
the samples that did run, and what was spent.

It writes once and refuses to overwrite.  It spends nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EXPERIMENT_ID = "openai-repo-diagnostic-v23-v18-sealed-window-k1-01"
BATCH = ROOT / "runs/stage5-openai-agents-api" / EXPERIMENT_ID
OUT = ROOT / "integrations/openai_agents/V18_SEALED_K1_HARD_STOP_RECORD_20261010.json"

FREEZE = ROOT / "integrations/openai_agents/V18_SEALED_CONFIRMATION_FREEZE_20261010.json"
REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
GATE = ROOT / "integrations/openai_agents/V18_SEALED_K1_ZERO_API_GATE_20261010.json"
PLAN = ROOT / "integrations/openai_agents/V18_SEALED_K1_LIVE_PLAN_20261010.json"
STUB = ROOT / "integrations/openai_agents/V18_SEALED_K1_LIVE_STUB_20261010.json"
REGRESSION = ROOT / "integrations/openai_agents/V18_SEALED_K1_FOCUSED_REGRESSION_RUN2_20261010.json"

ARMS = ("none", "native_summary", "pruner_v1")
PLUGIN = "pruner_v1"
BASELINE = "none"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_of() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build() -> dict[str, Any]:
    rows = rows_of()
    manifest = json.loads((BATCH / "manifest.json").read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    task_ids = list(registration["task_ids"])
    index = {(str(r["scenario"]), int(r["repeat"]), str(r["method"])): r for r in rows}

    accepted: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rejections: Counter[str] = Counter()
    for row in rows:
        task = str(row["scenario"])
        arm = str(row["method"])
        totals[task][arm] += 1
        if row.get("render_accepted"):
            accepted[task][arm] += 1
        else:
            rejections[f"{task}|{arm}|{row.get('render_reason')}"] += 1

    failed_tasks = sorted(
        task for task in task_ids if accepted[task][PLUGIN] < totals[task][PLUGIN]
    )
    strict = {arm: {"pass": 0, "n": 0} for arm in ARMS}
    semantic = {arm: {"pass": 0, "n": 0} for arm in ARMS}
    mechanism = {field: 0 for field in (
        "v21_outputs_considered",
        "v21_outputs_elided",
        "v21_lines_elided",
        "v21_bytes_saved",
        "v21_literal_guard_fallbacks",
        "v21_note_bytes",
    )}
    for row in rows:
        arm = str(row["method"])
        conditions = dict(row.get("render_conditions") or {})
        strict[arm]["n"] += 1
        semantic[arm]["n"] += 1
        if row.get("render_accepted") and all(conditions.values()):
            strict[arm]["pass"] += 1
        if row.get("answer_correct"):
            semantic[arm]["pass"] += 1
        if arm == PLUGIN:
            for field in mechanism:
                mechanism[field] += int(row.get(field, 0) or 0)

    pairs: list[dict[str, Any]] = []
    for task in task_ids:
        repeats = sorted({int(r["repeat"]) for r in rows if str(r["scenario"]) == task})
        for repeat in repeats:
            if (task, repeat, BASELINE) not in index or (task, repeat, PLUGIN) not in index:
                continue
            base_row = index[(task, repeat, BASELINE)]
            plugin_row = index[(task, repeat, PLUGIN)]
            base_total = int(base_row.get("actual_input_tokens", 0) or 0) + int(
                base_row.get("actual_output_tokens", 0) or 0
            )
            plugin_total = int(plugin_row.get("actual_input_tokens", 0) or 0) + int(
                plugin_row.get("actual_output_tokens", 0) or 0
            )
            pairs.append(
                {
                    "task": task,
                    "repeat": repeat,
                    "baseline_accepted": bool(base_row.get("render_accepted")),
                    "plugin_accepted": bool(plugin_row.get("render_accepted")),
                    "baseline_tokens": base_total,
                    "plugin_tokens": plugin_total,
                    "as_paired_tokens": round(
                        (base_total - plugin_total) / base_total if base_total else 0.0, 6
                    ),
                }
            )
    complete_pairs = [
        pair for pair in pairs if pair["baseline_accepted"] and pair["plugin_accepted"]
    ]
    savings = [pair["as_paired_tokens"] for pair in complete_pairs]
    requests = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    cache_calls = [call for row in rows for call in (row.get("raw_provider_usage") or [])]
    off_peak = [bool(call.get("off_peak")) for call in cache_calls if "off_peak" in call]

    return {
        "schema": "openai_agents_v18_sealed_k1_hard_stop_record",
        "record_id": "openai-agents-v18-sealed-k1-hard-stop-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "produced_by": "experiments/audits/v18_sealed_k1_hard_stop_record.py",
        "api_requests_spent": 0,
        "outcome": "STOPPED_BY_A_HARD_STOP_CONDITION",
        "what_happened": (
            "the paid batch was stopped by the operator as soon as the persisted rows showed that "
            "one registered task's answers could not be rendered in any arm, which is the freeze's "
            "own stop condition 'any render failure, truncation or dropped field -> stop and "
            "report'; the partial directory is kept and no manifest was finalised"
        ),
        "the_stop_condition_it_hit": {
            "source": str(FREEZE.relative_to(ROOT)).replace("\\", "/"),
            "text": "any render failure, truncation or dropped field -> stop and report",
            "how_it_was_read": (
                "a sample whose final answer was closed to empty because the host rejected it is a "
                "render failure and not a measured endpoint; the citable reference batch of this "
                "project (v16 sealed confirmation) recorded 0 of 45 rejected, so this project's own "
                "practice is that a rejected answer stops the batch rather than entering a mean. "
                "The narrower reading - render failure means only truncation, a dropped field or a "
                "harness exception - would not trigger, and both readings are stated here so the "
                "stop can be judged rather than assumed"
            ),
        },
        "evidence": {
            "samples_persisted": len(rows),
            "samples_planned": int(freeze["matrix"]["samples"]),
            "matrix_incomplete": len(rows) != int(freeze["matrix"]["samples"]),
            "present_per_task": {task: sum(totals[task].values()) for task in task_ids},
            "render_accepted_per_task_and_arm": {
                task: {arm: f"{accepted[task][arm]}/{totals[task][arm]}" for arm in ARMS}
                for task in task_ids
            },
            "tasks_with_a_plugin_render_failure": failed_tasks,
            "rejection_reasons": dict(rejections),
            "strict_track_per_arm": strict,
            "semantic_accuracy_per_arm": semantic,
            "truncations": sum(1 for row in rows if row.get("render_truncated")),
            "drops": sum(1 for row in rows if row.get("render_fields_dropped")),
            "provider_errors": sum(1 for row in rows if str(row.get("error_type") or "")),
            "requests_spent": requests,
            "cap": int(freeze["matrix"]["max_api_requests"]),
            "cache": {
                "calls": len(cache_calls),
                "calls_with_cache_fields": sum(
                    1 for call in cache_calls if call.get("cache_fields_present")
                ),
                "prompt_cache_hit_tokens_total": sum(
                    int(call.get("prompt_cache_hit_tokens", 0) or 0) for call in cache_calls
                ),
                "prompt_cache_miss_tokens_total": sum(
                    int(call.get("prompt_cache_miss_tokens", 0) or 0) for call in cache_calls
                ),
                "all_calls_off_peak": all(off_peak) if off_peak else None,
            },
            "mechanism_on_the_samples_that_ran": mechanism,
        },
        "the_partial_matrix_is_not_a_result": {
            "complete_pairs": len(complete_pairs),
            "complete_pairs_note": (
                "pairs whose two arms both rendered an answer; the stopped task contributes none"
            ),
            "partial_as_paired_savings": savings,
            "partial_as_paired_mean": (
                round(sum(savings) / len(savings), 6) if savings else None
            ),
            "why_it_may_not_be_cited": (
                "the frozen matrix is 45 samples and only "
                f"{len(rows)} exist; the pre-registered reading is decided on every pair of the "
                "matrix, and a partially completed matrix is a stopped batch, so these numbers are "
                "recorded only to make the stop explicable"
            ),
        },
        "diagnosis": {
            "task_that_could_not_render": (
                "autoreload_template_changed (django__django-15572, base commit "
                "0b31e024873681e187b574fe1c4afe5e48aeeecf)"
            ),
            "host_rejection_reason": "retry_rejected:over_160_chars",
            "what_the_host_saw": (
                "the first answer exceeded 160 characters, the diagnostic was carried into the one "
                "billed retry, and the retried answer also exceeded 160 characters, so the answer "
                "was closed to empty; the same pattern occurs in all three arms, at every repeat "
                "the batch reached"
            ),
            "raw_answer_lengths_seen": sorted(
                {
                    int(row.get("render_source_chars", 0) or 0)
                    for row in rows
                    if str(row.get("scenario")) == "autoreload_template_changed"
                }
            ),
            "the_contract_is_feasible": (
                "the minimal feasible rendered line for this task is 75 characters "
                "(RESULT issue=django-15572 cause=template_changed fix=template_dir file_path), so "
                "the bound is attainable; this is a model-compliance failure under this host "
                "configuration, not an infeasible contract"
            ),
            "it_is_symmetric": (
                "the failure hits all three arms identically, so it does not bias the paired "
                "comparison; it destroys the task's quality signal, which is why the batch stops "
                "instead of reporting a comparison over units that produced no answer"
            ),
            "the_other_two_tasks": (
                "sitemap_callable_lastmod and query_utils_unregister_lookup rendered every sample "
                "the batch reached, in every arm"
            ),
        },
        "what_this_record_does_not_claim": [
            "it does not report a saving for this round: no number from the partial matrix is citable",
            "it does not rejudge any earlier batch, and it does not touch v12 to v15, the v16 sealed "
            "confirmation batch, or any v17 to v22 group",
            "it does not relax any criterion: the freeze, the registration, the module pins, the "
            "gate and the audit are as they were before the batch started",
            "it does not authorise a replacement batch; that needs a new decision from the integrator",
        ],
        "what_is_kept": {
            "partial_batch_directory": str(BATCH.relative_to(ROOT)).replace("\\", "/"),
            "manifest_finalised": bool(manifest.get("v16_manifest_finalized")) is True,
            "manifest_note": (
                "the manifest on disk is the one the run wrote before its first request; the "
                "finaliser never ran, so no key was added after the stop and the batch cannot be "
                "mistaken for a complete one"
            ),
            "samples_jsonl_sha256": sha256_of(BATCH / "samples.jsonl"),
            "manifest_sha256": sha256_of(BATCH / "manifest.json"),
        },
        "zero_api_inputs_that_stand": {
            str(path.relative_to(ROOT)).replace("\\", "/"): {
                "sha256": sha256_of(path),
                "exists": path.is_file(),
            }
            for path in (FREEZE, REGISTRATION, GATE, PLAN, STUB, REGRESSION)
        },
        "gate_was_green_before_the_first_request": bool(
            json.loads(GATE.read_text(encoding="utf-8")).get("paid_run_allowed")
        )
        if GATE.is_file()
        else False,
        "citable": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    payload = build()
    evidence = payload["evidence"]
    print(f"outcome: {payload['outcome']}")
    print(f"samples persisted: {evidence['samples_persisted']} / {evidence['samples_planned']}")
    print(f"rejected per task and arm: {json.dumps(evidence['render_accepted_per_task_and_arm'], ensure_ascii=False)}")
    print(f"rejection reasons: {json.dumps(evidence['rejection_reasons'], ensure_ascii=False)}")
    print(f"requests spent: {evidence['requests_spent']} / {evidence['cap']}")
    print(
        "strict per arm: "
        + json.dumps({arm: f"{v['pass']}/{v['n']}" for arm, v in evidence["strict_track_per_arm"].items()})
    )
    if not args.probe:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"refusing to overwrite the existing artifact {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
