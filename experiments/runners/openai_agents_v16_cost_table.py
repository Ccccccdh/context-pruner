"""Batch cost accounting in USD for the recorded v14 and v15 tokens (zero API).

Prices (DeepSeek official, ``deepseek-flash`` = the ``deepseek-v4-flash`` endpoint used here):

* cache-miss input  $0.15 per 1M tokens
* cache-hit input   $0.003 per 1M tokens (50x cheaper)
* output            $0.60 per 1M tokens (200x the cache-hit input price)
* off-peak         = 50% discount; peak = UTC Mon-Fri 01:00-04:00 and 06:00-10:00

v14 and v15 were run **before** cache fields were recorded, so their input tokens cannot be
split into hit and miss from the records.  Both bounds are therefore reported - every input
token at the miss price and every input token at the hit price - and the real number for a
future batch lies between them; the per-call split is what the new metering records.

The plugin-versus-baseline **output-token** difference is reported separately, because output
is the most expensive unit by two orders of magnitude: a longer answer is a cost event, not
only a quality-format event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs/stage5-openai-agents-api"
OUT = REPO / "integrations/openai_agents/V16_CACHE_METERING_AND_COST_20261006.json"

MISS_USD_PER_TOKEN = 0.15 / 1_000_000
HIT_USD_PER_TOKEN = 0.003 / 1_000_000
OUTPUT_USD_PER_TOKEN = 0.60 / 1_000_000
OFF_PEAK_MULTIPLIER = 0.5

V14_BATCH = "openai-repo-diagnostic-v14-multitask-confirm-01"
V15_BATCHES = (
    "openai-repo-diagnostic-v15-django_field_error_messages_copy-acquisition-02",
    "openai-repo-diagnostic-v15-django_method_decorator_partial-acquisition-02",
    "openai-repo-diagnostic-v15-django_textchoices_string_value-acquisition-02",
)
OVER_LENGTH = (
    {"batch": "v12", "sample": "django_long_investigation r0 pruner_v1", "chars": 208},
    {"batch": "v14", "sample": "xarray_copy_dtype r0 pruner_v1", "chars": 179},
    {"batch": "v14", "sample": "xarray_copy_dtype r1 pruner_v1", "chars": 179},
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def usd(tokens: int, per_token: float, *, off_peak: bool = False) -> float:
    value = tokens * per_token
    return value * OFF_PEAK_MULTIPLIER if off_peak else value


def arm_costs(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_all_miss_usd": round(usd(input_tokens, MISS_USD_PER_TOKEN), 6),
        "input_all_hit_usd": round(usd(input_tokens, HIT_USD_PER_TOKEN), 6),
        "output_usd": round(usd(output_tokens, OUTPUT_USD_PER_TOKEN), 6),
        "input_all_miss_off_peak_usd": round(usd(input_tokens, MISS_USD_PER_TOKEN, off_peak=True), 6),
        "output_off_peak_usd": round(usd(output_tokens, OUTPUT_USD_PER_TOKEN, off_peak=True), 6),
        "total_all_miss_usd": round(
            usd(input_tokens, MISS_USD_PER_TOKEN) + usd(output_tokens, OUTPUT_USD_PER_TOKEN), 6
        ),
        "total_hypothetical_all_hit_usd": round(
            usd(input_tokens, HIT_USD_PER_TOKEN) + usd(output_tokens, OUTPUT_USD_PER_TOKEN), 6
        ),
    }


def batch_costs(batch: str) -> dict[str, Any]:
    rows = read_jsonl(RUNS / batch / "samples.jsonl")
    arms: dict[str, dict[str, Any]] = {}
    for method in sorted({str(row["method"]) for row in rows}):
        selected = [row for row in rows if str(row["method"]) == method]
        input_tokens = sum(
            int(row.get("actual_input_tokens", 0) or 0)
            + int(row.get("summary_input_tokens", 0) or 0)
            for row in selected
        )
        output_tokens = sum(
            int(row.get("actual_output_tokens", 0) or 0)
            + int(row.get("summary_output_tokens", 0) or 0)
            for row in selected
        )
        arms[method] = {
            "samples": len(selected),
            "requests": sum(int(row.get("api_request_attempts", 0) or 0) for row in selected),
            **arm_costs(input_tokens, output_tokens),
        }
    total_input = sum(arm["input_tokens"] for arm in arms.values())
    total_output = sum(arm["output_tokens"] for arm in arms.values())
    return {
        "batch": batch,
        "arms": arms,
        "all_arms": arm_costs(total_input, total_output),
        "cache_fields_recorded": any(
            "prompt_cache_hit_tokens" in row for row in rows
        ),
        "cache_note": (
            "run before cache metering existed, so only the all-miss and all-hit bounds are "
            "available for this batch"
        ),
    }


def output_delta(batch: str) -> dict[str, Any]:
    rows = read_jsonl(RUNS / batch / "samples.jsonl")
    by_key = {(str(r["scenario"]), int(r["repeat"]), str(r["method"])): r for r in rows}
    pairs = []
    for task, repeat, method in sorted(by_key):
        if method != "pruner_v1":
            continue
        baseline = by_key.get((task, repeat, "none"))
        arm = by_key[(task, repeat, method)]
        if baseline is None:
            continue
        base_out = int(baseline.get("actual_output_tokens", 0) or 0)
        arm_out = int(arm.get("actual_output_tokens", 0) or 0)
        pairs.append(
            {
                "task": task,
                "repeat": repeat,
                "baseline_output_tokens": base_out,
                "plugin_output_tokens": arm_out,
                "delta_output_tokens": arm_out - base_out,
                "delta_usd": round(usd(arm_out - base_out, OUTPUT_USD_PER_TOKEN), 6),
                "answer_chars": len(str(arm.get("final_output", ""))),
                "strict_success": bool(arm.get("success")),
            }
        )
    total_delta = sum(pair["delta_output_tokens"] for pair in pairs)
    return {
        "batch": batch,
        "pairs": pairs,
        "total_delta_output_tokens": total_delta,
        "total_delta_usd": round(usd(total_delta, OUTPUT_USD_PER_TOKEN), 6),
        "over_length_pairs": [
            pair for pair in pairs if pair["answer_chars"] > 160
        ],
        "note": (
            "output is billed at 200x the cache-hit input price, so an answer that grows is a "
            "cost event as well as a format event; this table shows the billed difference and "
            "does not decide the contract question"
        ),
    }


def build() -> dict[str, Any]:
    v14 = batch_costs(V14_BATCH)
    v15 = [batch_costs(batch) for batch in V15_BATCHES]
    return {
        "schema": "openai_agents_v16_cache_metering_and_cost",
        "date": "20261006",
        "paid_requests": 0,
        "prices_usd_per_million_tokens": {
            "input_cache_miss": 0.15,
            "input_cache_hit": 0.003,
            "output": 0.60,
            "off_peak_multiplier": OFF_PEAK_MULTIPLIER,
            "peak_hours_utc": "Mon-Fri 01:00-04:00 and 06:00-10:00",
        },
        "price_ratios": {
            "cache_hit_vs_miss": round(0.15 / 0.003, 2),
            "output_vs_cache_hit": round(0.60 / 0.003, 2),
        },
        "v14": v14,
        "v15": v15,
        "v15_all_arms": arm_costs(
            sum(batch["all_arms"]["input_tokens"] for batch in v15),
            sum(batch["all_arms"]["output_tokens"] for batch in v15),
        ),
        "v14_output_delta_plugin_vs_baseline": output_delta(V14_BATCH),
        "over_length_samples": list(OVER_LENGTH),
        "reading": (
            "every decisive conclusion in v13 and v14 was reached on a batch whose whole cost is "
            "in these tables; the cost side is positive on v14 while the strict quality track "
            "failed on one of its three tasks, and the two statements belong together"
        ),
    }


def main() -> int:
    report = build()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    v14 = report["v14"]["all_arms"]
    print(
        f"v14 (9 samples/arm): input {v14['input_tokens']:,} output {v14['output_tokens']:,} | "
        f"all-miss ${v14['total_all_miss_usd']:.4f} | all-hit ${v14['total_hypothetical_all_hit_usd']:.4f}"
    )
    v15 = report["v15_all_arms"]
    print(
        f"v15 (3 acquisition tasks): input {v15['input_tokens']:,} output {v15['output_tokens']:,} | "
        f"all-miss ${v15['total_all_miss_usd']:.4f} | all-hit ${v15['total_hypothetical_all_hit_usd']:.4f}"
    )
    delta = report["v14_output_delta_plugin_vs_baseline"]
    print(
        f"v14 plugin-vs-baseline output delta {delta['total_delta_output_tokens']:+d} tokens = "
        f"${delta['total_delta_usd']:+.6f} | over-length pairs: "
        f"{[(p['task'], p['answer_chars'], p['delta_output_tokens']) for p in delta['over_length_pairs']]}"
    )
    for batch in report["v15"]:
        arms = batch["arms"]
        print(
            f"  {batch['batch'].split('-v15-')[1]:<45} requests="
            f"{sum(a['requests'] for a in arms.values())} tokens="
            f"{batch['all_arms']['input_tokens'] + batch['all_arms']['output_tokens']:,} "
            f"all-miss=${batch['all_arms']['total_all_miss_usd']:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
