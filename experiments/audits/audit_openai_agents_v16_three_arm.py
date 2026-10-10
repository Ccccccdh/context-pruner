"""Independent audit of the v16 three-arm pilot (new id, recomputes every hard field).

It reads the batch's own rows, the two evidence streams and the frozen pilot freeze, and it
recomputes rather than copies:

* the freeze digest, the host-configuration module hashes and the renderer hash;
* the four conditions per sample, recomputed from the recorded rendered line;
* tool pairing from the recorded model inputs;
* the request ledger, including retries and summary calls, against the frozen summary cap;
* the cache ledger, with missing fields counted as unknown rather than zero;
* the acceptance verdict: per-task plugin-versus-baseline strict tracks, the paired
  complete-token savings, the count of positive pairs and the batch mean.

``errors`` non-empty means the batch may not be cited as an effective saving.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v16_registry as registry  # noqa: E402
from experiments.runners import run_openai_agents_v16_acquisition as runner  # noqa: E402

AUDIT_ID = "openai-agents-v16-sealed-confirmation-audit-01"
BATCH = ROOT / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v16-sealed-confirmation-01"
OUT = ROOT / "integrations/openai_agents/V16_SEALED_CONFIRMATION_AUDIT_20261006.json"
NOT_CITABLE = "errors 非空：不得当作有效节省"
#: The audited batch runs the sealed confirmation set, so every registry lookup in
#: this audit - tasks, contracts, fingerprints - resolves that set.
registry.set_active("sealed")
MISS_USD_PER_TOKEN = 0.15 / 1_000_000
HIT_USD_PER_TOKEN = 0.003 / 1_000_000
OUTPUT_USD_PER_TOKEN = 0.60 / 1_000_000
OFF_PEAK_MULTIPLIER = 0.5


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _conditions(line: str, issue: str, pattern: str, facts: list[str]) -> dict[str, bool]:
    text = str(line)
    return {
        "facts_complete": all(fact in text for fact in facts),
        "frozen_regex": bool(re.fullmatch(pattern, text, flags=re.IGNORECASE)) if text else False,
        "single_line_prefix": bool(text) and text.startswith("RESULT ") and "\n" not in text,
        "within_160": bool(text) and len(text) <= 160,
    }


def _usd(miss: int, hit: int, out: int, *, off_peak: bool) -> float:
    value = (
        miss * MISS_USD_PER_TOKEN + hit * HIT_USD_PER_TOKEN + out * OUTPUT_USD_PER_TOKEN
    )
    return round(value * (OFF_PEAK_MULTIPLIER if off_peak else 1.0), 6)


def main() -> int:
    errors: list[str] = []
    freeze = json.loads(runner.CONFIRMATION_FREEZE.read_text(encoding="utf-8"))
    manifest = json.loads((BATCH / "manifest.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(BATCH / "samples.jsonl")

    # ---------------------------------------------------------------- frozen configuration
    if manifest.get("freeze_sha256") != _sha(runner.CONFIRMATION_FREEZE):
        errors.append("the manifest's freeze digest does not match the freeze file")
    for relative, expected in freeze["host_configuration"]["modules"].items():
        current = _sha(ROOT / relative) if (ROOT / relative).is_file() else ""
        if current != str(expected):
            errors.append(f"host configuration module changed: {relative}")
    renderer = freeze["host_configuration"]["renderer"]
    if _sha(ROOT / renderer["module"]) != renderer["sha256"]:
        errors.append("the frozen renderer module changed")
    if manifest["criterion_sha256" if False else "freeze_sha256"] != _sha(runner.CONFIRMATION_FREEZE):
        errors.append("the batch did not run the frozen configuration")
    if int(freeze["matrix"]["samples"]) != len(rows):
        errors.append(f"sample count differs from the frozen matrix: {len(rows)}")

    # ------------------------------------------------------------------- per-sample checks
    contracts = runner.contract_table()
    per_sample: list[dict[str, Any]] = []
    for row in rows:
        task_id = str(row["scenario"])
        contract = contracts[task_id]
        evidence = _read_jsonl(
            BATCH
            / "render-evidence"
            / f"{task_id}-{int(row['repeat'])}-{str(row['method'])}.jsonl"
        )
        rendered_line = ""
        for entry in evidence:
            if entry.get("accepted"):
                rendered_line = str(entry.get("output") or "")
        recomputed = _conditions(
            rendered_line,
            str(contract["issue"]),
            str(contract["pattern"]),
            [str(fact) for fact in contract["facts"]],
        )
        reported = dict(row.get("render_conditions") or {})
        if recomputed != reported:
            errors.append(f"{task_id} r{row['repeat']} {row['method']}: four conditions differ")
        if rendered_line != str(row.get("final_output") or ""):
            errors.append(f"{task_id} r{row['repeat']} {row['method']}: rendered line mismatch")
        if int(row.get("render_repair_calls", 0) or 0) > freeze["host_configuration"]["retry"][
            "budget_per_sample"
        ]:
            errors.append(f"{task_id} r{row['repeat']} {row['method']}: retry budget exceeded")
        if int(row.get("summary_calls", 0) or 0) > int(
            freeze["host_configuration"]["summary_cap_per_sample"]
        ):
            errors.append(f"{task_id} r{row['repeat']} {row['method']}: summary cap exceeded")
        attempts = int(row.get("api_request_attempts", 0) or 0)
        charged = int(row.get("model_calls", 0) or 0) + int(row.get("summary_calls", 0) or 0)
        if attempts < charged:
            errors.append(
                f"{task_id} r{row['repeat']} {row['method']}: charged less than its calls"
            )
        totals = int(row.get("all_arm_total_tokens", 0) or 0)
        direct = int(row.get("actual_input_tokens", 0) or 0) + int(
            row.get("actual_output_tokens", 0) or 0
        )
        if totals < direct:
            errors.append(
                f"{task_id} r{row['repeat']} {row['method']}: complete total below direct total"
            )
        # tool pairing, from the recorded model inputs
        pairing_records = _read_jsonl(
            BATCH / "input-evidence" / f"{task_id}-{int(row['repeat'])}-{str(row['method'])}.jsonl"
        )
        for record in pairing_records:
            items = list(record.get("input_manifest") or [])
            calls = {item.get("call_id") for item in items if item.get("type") == "function_call"}
            outputs = {
                item.get("call_id")
                for item in items
                if item.get("type") == "function_call_output"
            }
            if calls != outputs:
                errors.append(
                    f"{task_id} r{row['repeat']} {row['method']}: tool pairing inconsistent"
                )
        per_sample.append(
            {
                "task": task_id,
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "recomputed_conditions": recomputed,
                "render_accepted": bool(row.get("render_accepted")),
                "render_reason": str(row.get("render_reason", "")),
                "requests": attempts,
                "model_calls": int(row.get("model_calls", 0) or 0),
                "summary_calls": int(row.get("summary_calls", 0) or 0),
                "retries": int(row.get("render_repair_calls", 0) or 0),
                "complete_total_tokens": totals,
                "render_report": dict(row.get("render_report") or {}),
            }
        )

    # ----------------------------------------------------------------------- request ledger
    request_total = sum(entry["requests"] for entry in per_sample)
    if request_total != int(manifest.get("api_calls", -1)):
        errors.append("the request ledger does not add up to the manifest")
    if request_total > int(freeze["matrix"]["max_api_requests"]):
        errors.append("the frozen request cap was exceeded")
    summary_total = sum(entry["summary_calls"] for entry in per_sample)

    # ------------------------------------------------------------------------ cache ledger
    calls = [
        call
        for row in rows
        for call in (row.get("raw_provider_usage") or row.get("provider_calls") or [])
    ]
    with_fields = [call for call in calls if call.get("cache_fields_present")]
    without_fields = len(calls) - len(with_fields)
    hit = sum(int(call["prompt_cache_hit_tokens"]) for call in with_fields)
    miss = sum(int(call["prompt_cache_miss_tokens"]) for call in with_fields)
    output_tokens = sum(int(row.get("actual_output_tokens", 0) or 0) for row in rows)
    all_off_peak = all(bool(call.get("off_peak")) for call in calls) if calls else None
    aggregate = dict(manifest.get("metering", {}).get("prompt_cache") or {})
    if int(aggregate.get("calls", -1)) != len(calls):
        errors.append("the cache ledger's call count differs from the rows")
    if aggregate.get("prompt_cache_hit_tokens_total") not in (None, hit):
        errors.append("the cache ledger's hit total differs from the rows")
    if without_fields:
        errors.append(
            f"{without_fields} provider calls carry no cache fields: totals stay unknown, not zero"
        )

    # ------------------------------------------------------------------ acceptance recompute
    by_key = {
        (entry["task"], entry["repeat"], entry["method"]): entry for entry in per_sample
    }
    per_task: dict[str, Any] = {}
    pooled: list[float] = []
    positives = 0
    for task_id in registry.task_ids():
        savings: list[float] = []
        strict = {"none": 0, "pruner_v1": 0, "native_summary": 0}
        for repeat in range(int(freeze["matrix"]["repeats"])):
            baseline = by_key.get((task_id, repeat, "none"))
            plugin = by_key.get((task_id, repeat, "pruner_v1"))
            if baseline is None or plugin is None:
                errors.append(f"{task_id} r{repeat}: an arm is missing")
                continue
            for entry in (baseline, plugin):
                method = entry["method"]
                if entry["render_accepted"] and all(entry["recomputed_conditions"].values()):
                    strict[method] += 1
            base_tokens = baseline["complete_total_tokens"]
            plugin_tokens = plugin["complete_total_tokens"]
            saving = (base_tokens - plugin_tokens) / base_tokens if base_tokens else 0.0
            savings.append(round(saving, 4))
            pooled.append(saving)
            if saving > 0:
                positives += 1
        per_task[task_id] = {
            "paired_n": len(savings),
            "paired_savings": savings,
            "mean_saving": round(sum(savings) / len(savings), 4) if savings else None,
            "min_saving": min(savings) if savings else None,
            "max_saving": max(savings) if savings else None,
            "positive_pairs": sum(1 for value in savings if value > 0),
            "strict_track_none": strict["none"],
            "strict_track_pruner_v1": strict["pruner_v1"],
            "plugin_at_least_baseline": strict["pruner_v1"] >= strict["none"],
        }
    mean = sum(pooled) / len(pooled) if pooled else 0.0
    variance = (
        sum((value - mean) ** 2 for value in pooled) / (len(pooled) - 1)
        if len(pooled) > 1
        else 0.0
    )
    track_a = all(entry["plugin_at_least_baseline"] for entry in per_task.values())
    majority = positives * 2 > len(pooled) if pooled else False
    met = bool(track_a and majority and mean > 0)
    if manifest["pilot_report"]["met"] != met:
        errors.append("the runner's acceptance verdict differs from the audit's recomputation")

    verdict = {
        "track_a_all_tasks": track_a,
        "majority_positive": majority,
        "positive_pairs": positives,
        "paired_n": len(pooled),
        "pooled_mean_saving": round(mean, 4),
        "pooled_stdev_saving": round(variance**0.5, 4),
        "pooled_min_saving": min(pooled) if pooled else None,
        "pooled_max_saving": max(pooled) if pooled else None,
        "met": met,
        "acceptance_line": freeze["acceptance_line"]["line"],
        "recomputed_by": AUDIT_ID,
    }
    payload = {
        "schema": "openai_agents_v16_three_arm_pilot_audit",
        "audit_id": AUDIT_ID,
        "date": "20261006",
        "batch": str(BATCH.relative_to(ROOT)).replace("\\", "/"),
        "batch_manifest_sha256": _sha(BATCH / "manifest.json"),
        "freeze_sha256": _sha(runner.CONFIRMATION_FREEZE),
        "complete": not errors,
        "errors": errors,
        "usable_as_saving": not errors,
        "not_citable_statement": NOT_CITABLE if errors else "",
        "recomputed_acceptance": verdict,
        "per_task": per_task,
        "request_ledger": {
            "requests": request_total,
            "cap": int(freeze["matrix"]["max_api_requests"]),
            "summary_calls": summary_total,
            "summary_cap_per_sample": freeze["host_configuration"]["summary_cap_per_sample"],
            "retries": sum(entry["retries"] for entry in per_sample),
            "model_calls": sum(entry["model_calls"] for entry in per_sample),
        },
        "cache_ledger": {
            "calls": len(calls),
            "calls_with_fields": len(with_fields),
            "calls_without_fields": without_fields,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "output_tokens": output_tokens,
            "all_calls_off_peak": all_off_peak,
            "estimated_cost_usd": _usd(
                miss, hit, output_tokens, off_peak=bool(all_off_peak)
            ),
            "basis": (
                "official rates per million tokens: input miss 0.15, input hit 0.003, output "
                "0.60; off-peak applies the published half-price multiplier"
            ),
            "unknown_fields_stay_null": without_fields > 0,
        },
        "failure_decomposition": {
            method: _decomposition(per_sample, method)
            for method in ("none", "pruner_v1", "native_summary")
        },
        "per_sample": per_sample,
        "discipline": {
            "recomputed_not_copied": [
                "four conditions",
                "tool pairing",
                "request ledger",
                "cache ledger",
                "acceptance verdict",
            ],
            "old_batches_untouched": True,
            "paid_requests_by_the_audit": 0,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"audit artifact: {OUT}")
    print(f"complete={payload['complete']} errors={len(errors)}")
    for error in errors:
        print("  -", error)
    print("verdict", json.dumps(verdict, ensure_ascii=False))
    print("request_ledger", json.dumps(payload["request_ledger"], ensure_ascii=False))
    print("cache_ledger", json.dumps(payload["cache_ledger"], ensure_ascii=False))
    for task_id, entry in per_task.items():
        print("task", task_id, json.dumps(entry, ensure_ascii=False))
    for method, entry in payload["failure_decomposition"].items():
        print("arm", method, json.dumps(entry, ensure_ascii=False))
    return 0 if payload["complete"] else 2


def _decomposition(per_sample: list[dict[str, Any]], method: str) -> dict[str, Any]:
    arm = [entry for entry in per_sample if entry["method"] == method]
    classes: dict[str, int] = {}
    for entry in arm:
        if entry["render_accepted"] and all(entry["recomputed_conditions"].values()):
            continue
        reason = entry["render_reason"]
        if entry["render_accepted"]:
            keys = [
                name
                for name, value in entry["recomputed_conditions"].items()
                if not value
            ]
            key = "rejected_after_acceptance:" + ",".join(sorted(keys))
        elif reason.startswith("retry_rejected:"):
            key = reason.split(":", 1)[1]
        elif reason:
            key = reason
        else:
            key = "no_answer_turn"
        classes[key] = classes.get(key, 0) + 1
    return {
        "samples": len(arm),
        "strict_pass": sum(
            1
            for entry in arm
            if entry["render_accepted"] and all(entry["recomputed_conditions"].values())
        ),
        "render_accepted": sum(1 for entry in arm if entry["render_accepted"]),
        "failures": classes,
        "retries": sum(entry["retries"] for entry in arm),
        "retry_samples": sum(1 for entry in arm if entry["retries"] > 0),
        "summary_calls": sum(entry["summary_calls"] for entry in arm),
        "requests": sum(entry["requests"] for entry in arm),
        "complete_total_tokens": sum(entry["complete_total_tokens"] for entry in arm),
    }


if __name__ == "__main__":
    raise SystemExit(main())
