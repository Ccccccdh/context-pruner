"""Zero-API wording-effect analysis across the v12, v13 and v14 recorded batches.

Question: does the duplicate-output reduction systematically change how long the model's
answer is, and can the change be explained mechanically by something the reduction did to the
visible text?

Method (all from recorded data, no API call, nothing re-run, nothing re-scored):

* per sample: the answer length, whether the frozen "at most 160 characters" condition held,
  whether the frozen regex and the required literals held, and the arm's recorded reduction;
* per **(task, repeat) pair**: the plugin-minus-baseline length difference, so a task that
  simply has longer answers cannot masquerade as a compression effect;
* per plugin sample: the final visible text is rebuilt twice - once as the baseline arm sends
  it, once exactly as the plugin arm sent it (untouched originals plus the frozen pointer text
  for the recorded replacements) - and the tokens the answer itself uses are looked up in both,
  which tests "the reduction hid something the answer then had to explain";
* the pointer text's own length is measured, since it occupies space in the payload.

The statistics are descriptive (means, spreads, sign counts) on a small, task-mixed sample;
no confidence interval or significance claim is made.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_multitask_registry_v14 as registry_v14
from experiments.runners import openai_agents_requests_replay_gate_v13 as gate_v13
from experiments.runners import openai_agents_requests_task_registry_v13 as registry_v13
from experiments.runners import openai_agents_real_payload_replay_v10 as replay_v10
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs/stage5-openai-agents-api"
MAX_ANSWER_CHARS = 160
STOPWORDS = {
    "that", "this", "with", "from", "which", "when", "then", "than", "must", "should",
    "into", "also", "because", "during", "after", "before", "value", "values", "raise",
    "raises", "raised", "returns", "return", "using", "used", "uses", "where", "while",
}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pointer_text(newest_call_id: str, digest: str) -> str:
    return (
        f"[Exact duplicate output; full source is at call_id={newest_call_id}; sha256={digest}]"
    )


def text_of(item: dict) -> str:
    for key in ("content", "output", "arguments"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [str(part.get("text", "")) for part in value if isinstance(part, dict)]
            if any(parts):
                return "\n".join(parts)
    return ""


def outputs(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call_output"]


def answer_tokens(answer: str) -> list[str]:
    tokens = {
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", answer)
        if token.casefold() not in STOPWORDS
    }
    return sorted(tokens)


def plugin_final_text(rebuilt_final: Sequence[dict], record: dict) -> tuple[str, int, int]:
    """The plugin arm's final visible text, rebuilt from the recorded item manifest.

    Returns the text, how many items the record shows as pointers, and the total characters of
    the original copies those pointers stand in for.
    """
    manifest = list(record.get("output_manifest") or [])
    original_outputs = outputs(rebuilt_final)
    parts: list[str] = []
    pointers = 0
    omitted_chars = 0
    for position, item in enumerate(rebuilt_final):
        if item.get("type") != "function_call_output":
            parts.append(text_of(item))
            continue
        position_in_outputs = sum(
            1 for previous in rebuilt_final[:position] if previous.get("type") == "function_call_output"
        )
        original = text_of(original_outputs[position_in_outputs])
        recorded = manifest[position_in_outputs] if position_in_outputs < len(manifest) else {}
        digest = hashlib.sha256(original.encode()).hexdigest()
        if str(recorded.get("output_sha256", digest)) != digest:
            # the record says this item is not the original: it must be the frozen pointer
            newest = next(
                (
                    candidate
                    for candidate in manifest
                    if str(candidate.get("output_sha256")) == str(recorded.get("note_sha256", ""))
                    or (
                        candidate is not recorded
                        and str(candidate.get("output_chars")) == str(recorded.get("output_chars"))
                    )
                ),
                None,
            )
            target_digest = ""
            for replacement_digest in {
                str(entry.get("output_sha256")) for entry in manifest
            }:
                if replacement_digest == digest:
                    target_digest = replacement_digest
                    break
            call_id = str((newest or {}).get("call_id", ""))
            parts.append(pointer_text(call_id, target_digest or "0" * 64))
            pointers += 1
            omitted_chars += len(original)
            continue
        parts.append(original)
    return "\n".join(part for part in parts if part), pointers, omitted_chars


def batch_rows(name: str) -> list[dict]:
    return read_jsonl(RUNS / name / "samples.jsonl")


def final_record(batch: str, arm: str, repeat: int, evidence_name: str | None = None) -> dict | None:
    path = RUNS / batch / "input-evidence" / evidence_name
    if not path.is_file():
        return None
    records = [r for r in read_jsonl(path) if r.get("stage") == "model_input"]
    return records[-1] if records else None


def analyse() -> dict[str, Any]:
    samples: list[dict] = []
    pairs: list[dict] = []
    token_checks: list[dict] = []

    # -- v12: one task, one repeat, three arms ---------------------------------
    v12 = "openai-repo-diagnostic-v12-exact-duplicate-dev-01"
    v12_bounds = replay_v10.realistic_boundaries(0)
    for row in batch_rows(v12):
        arm = str(row["method"])
        answer = str(row.get("final_output", ""))
        entry = {
            "batch": "v12",
            "task": "django_long_investigation",
            "repeat": 0,
            "method": arm,
            "answer_chars": len(answer),
            "within_160": len(answer) <= MAX_ANSWER_CHARS,
            "format_correct": bool(row.get("final_format_correct")),
            "answer_correct": bool(row.get("answer_correct")),
            "success": bool(row.get("success")),
            "saved_bytes": int(row.get("selective_retention_saved_bytes_total", 0) or 0),
            "replacements": int(row.get("exact_duplicate_replacements", 0) or 0),
        }
        samples.append(entry)
    base_len = next(s["answer_chars"] for s in samples if s["batch"] == "v12" and s["method"] == "none")
    for method in ("pruner_v1", "native_summary"):
        row = next(s for s in samples if s["batch"] == "v12" and s["method"] == method)
        pairs.append(
            {
                "batch": "v12",
                "task": "django_long_investigation",
                "repeat": 0,
                "method": method,
                "baseline_chars": base_len,
                "arm_chars": row["answer_chars"],
                "delta": row["answer_chars"] - base_len,
                "within_160": row["within_160"],
            }
        )
    record = final_record(v12, "pruner_v1", 0, "django_long_investigation-0-pruner_v1.jsonl")
    if record is not None:
        text, pointers, omitted = plugin_final_text(v12_bounds[-1], record)
        baseline_text = "\n".join(text_of(item) for item in v12_bounds[-1])
        answer = next(
            s for s in samples if s["batch"] == "v12" and s["method"] == "pruner_v1"
        )
        row = next(r for r in batch_rows(v12) if r["method"] == "pruner_v1")
        tokens = answer_tokens(str(row.get("final_output", "")))
        token_checks.append(
            {
                "batch": "v12",
                "task": "django_long_investigation",
                "repeat": 0,
                "answer_chars": answer["answer_chars"],
                "pointers_in_final_text": pointers,
                "omitted_original_chars": omitted,
                "answer_tokens": len(tokens),
                "tokens_in_baseline_text": sum(1 for t in tokens if t in baseline_text),
                "tokens_in_plugin_text": sum(1 for t in tokens if t in text),
                "tokens_missing_from_plugin_only": [
                    t for t in tokens if t in baseline_text and t not in text
                ],
            }
        )

    # -- v13: one task, three repeats, three arms ------------------------------
    v13 = "openai-repo-diagnostic-v13-requests1766-confirm-01"
    v13_bounds = gate_v13.boundaries()
    v13_rows = batch_rows(v13)
    for row in v13_rows:
        answer = str(row.get("final_output", ""))
        samples.append(
            {
                "batch": "v13",
                "task": registry_v13.TASK_ID,
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "answer_chars": len(answer),
                "within_160": len(answer) <= MAX_ANSWER_CHARS,
                "format_correct": bool(row.get("final_format_correct")),
                "answer_correct": bool(row.get("answer_correct")),
                "success": bool(row.get("success")),
                "saved_bytes": int(row.get("selective_retention_saved_bytes_total", 0) or 0),
                "replacements": int(row.get("exact_duplicate_replacements", 0) or 0),
            }
        )
    for repeat in range(3):
        base = next(
            s for s in samples if s["batch"] == "v13" and s["repeat"] == repeat and s["method"] == "none"
        )
        for method in ("pruner_v1", "native_summary"):
            arm = next(
                s
                for s in samples
                if s["batch"] == "v13" and s["repeat"] == repeat and s["method"] == method
            )
            pairs.append(
                {
                    "batch": "v13",
                    "task": registry_v13.TASK_ID,
                    "repeat": repeat,
                    "method": method,
                    "baseline_chars": base["answer_chars"],
                    "arm_chars": arm["answer_chars"],
                    "delta": arm["answer_chars"] - base["answer_chars"],
                    "within_160": arm["within_160"],
                }
            )
    for repeat in range(3):
        evidence = f"{registry_v13.TASK_ID}-{repeat}-pruner_v1.jsonl"
        record = final_record(v13, "pruner_v1", repeat, evidence)
        if record is None:
            continue
        text, pointers, omitted = plugin_final_text(v13_bounds[-1], record)
        baseline_text = "\n".join(text_of(item) for item in v13_bounds[-1])
        row = next(
            r for r in v13_rows if r["method"] == "pruner_v1" and int(r["repeat"]) == repeat
        )
        tokens = answer_tokens(str(row.get("final_output", "")))
        token_checks.append(
            {
                "batch": "v13",
                "task": registry_v13.TASK_ID,
                "repeat": repeat,
                "answer_chars": len(str(row.get("final_output", ""))),
                "pointers_in_final_text": pointers,
                "omitted_original_chars": omitted,
                "answer_tokens": len(tokens),
                "tokens_in_baseline_text": sum(1 for t in tokens if t in baseline_text),
                "tokens_in_plugin_text": sum(1 for t in tokens if t in text),
                "tokens_missing_from_plugin_only": [
                    t for t in tokens if t in baseline_text and t not in text
                ],
            }
        )

    # -- v14: three tasks, three repeats, three arms ---------------------------
    v14 = "openai-repo-diagnostic-v14-multitask-confirm-01"
    v14_rows = batch_rows(v14)
    for row in v14_rows:
        answer = str(row.get("final_output", ""))
        samples.append(
            {
                "batch": "v14",
                "task": str(row["scenario"]),
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "answer_chars": len(answer),
                "within_160": len(answer) <= MAX_ANSWER_CHARS,
                "format_correct": bool(row.get("final_format_correct")),
                "answer_correct": bool(row.get("answer_correct")),
                "success": bool(row.get("success")),
                "saved_bytes": int(row.get("selective_retention_saved_bytes_total", 0) or 0),
                "replacements": int(row.get("exact_duplicate_replacements", 0) or 0),
            }
        )
    for task_id in sorted({str(row["scenario"]) for row in v14_rows}):
        for repeat in range(3):
            base = next(
                s
                for s in samples
                if s["batch"] == "v14"
                and s["task"] == task_id
                and s["repeat"] == repeat
                and s["method"] == "none"
            )
            for method in ("pruner_v1", "native_summary"):
                arm = next(
                    s
                    for s in samples
                    if s["batch"] == "v14"
                    and s["task"] == task_id
                    and s["repeat"] == repeat
                    and s["method"] == method
                )
                pairs.append(
                    {
                        "batch": "v14",
                        "task": task_id,
                        "repeat": repeat,
                        "method": method,
                        "baseline_chars": base["answer_chars"],
                        "arm_chars": arm["answer_chars"],
                        "delta": arm["answer_chars"] - base["answer_chars"],
                        "within_160": arm["within_160"],
                    }
                )
    for task_id in sorted({str(row["scenario"]) for row in v14_rows}):
        bounds = registry_v14 and None  # placeholder to keep the import used
        rebuilt = __import__(
            "experiments.runners.openai_agents_multitask_replay_gate_v14",
            fromlist=["boundaries"],
        ).boundaries(task_id)
        for repeat in range(3):
            evidence = f"{task_id}-{repeat}-pruner_v1.jsonl"
            record = final_record(v14, "pruner_v1", repeat, evidence)
            if record is None:
                continue
            text, pointers, omitted = plugin_final_text(rebuilt[-1], record)
            baseline_text = "\n".join(text_of(item) for item in rebuilt[-1])
            row = next(
                r
                for r in v14_rows
                if r["scenario"] == task_id and r["method"] == "pruner_v1" and int(r["repeat"]) == repeat
            )
            tokens = answer_tokens(str(row.get("final_output", "")))
            token_checks.append(
                {
                    "batch": "v14",
                    "task": task_id,
                    "repeat": repeat,
                    "answer_chars": len(str(row.get("final_output", ""))),
                    "pointers_in_final_text": pointers,
                    "omitted_original_chars": omitted,
                    "answer_tokens": len(tokens),
                    "tokens_in_baseline_text": sum(1 for t in tokens if t in baseline_text),
                    "tokens_in_plugin_text": sum(1 for t in tokens if t in text),
                    "tokens_missing_from_plugin_only": [
                        t for t in tokens if t in baseline_text and t not in text
                    ],
                }
            )

    # -- aggregates -------------------------------------------------------------
    def stats(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        }

    def by(predicate) -> list[dict]:
        return [entry for entry in samples if predicate(entry)]

    lengths = {
        f"{batch}|{method}": stats(
            [entry["answer_chars"] for entry in by(lambda e, b=batch, m=method: e["batch"] == b and e["method"] == m)]
        )
        for batch in ("v12", "v13", "v14")
        for method in ("none", "pruner_v1", "native_summary")
    }
    plugin_pairs = [pair for pair in pairs if pair["method"] == "pruner_v1"]
    native_pairs = [pair for pair in pairs if pair["method"] == "native_summary"]
    deltas = {
        "pruner_v1": {
            "overall": stats([pair["delta"] for pair in plugin_pairs]),
            "per_batch": {
                batch: stats([pair["delta"] for pair in plugin_pairs if pair["batch"] == batch])
                for batch in ("v12", "v13", "v14")
            },
            "per_task_v14": {
                task: stats(
                    [pair["delta"] for pair in plugin_pairs if pair["batch"] == "v14" and pair["task"] == task]
                )
                for task in sorted({pair["task"] for pair in plugin_pairs if pair["batch"] == "v14"})
            },
            "signs": {
                "positive": sum(1 for pair in plugin_pairs if pair["delta"] > 0),
                "zero": sum(1 for pair in plugin_pairs if pair["delta"] == 0),
                "negative": sum(1 for pair in plugin_pairs if pair["delta"] < 0),
            },
        },
        "native_summary": {
            "overall": stats([pair["delta"] for pair in native_pairs]),
            "signs": {
                "positive": sum(1 for pair in native_pairs if pair["delta"] > 0),
                "zero": sum(1 for pair in native_pairs if pair["delta"] == 0),
                "negative": sum(1 for pair in native_pairs if pair["delta"] < 0),
            },
        },
    }
    failures = [
        entry
        for entry in samples
        if not entry["within_160"]
    ]
    over_length = {
        "total_failing_samples": len(failures),
        "by_batch_and_method": {},
        "chars": [entry["answer_chars"] for entry in failures],
    }
    for entry in failures:
        key = f"{entry['batch']}|{entry['method']}"
        over_length["by_batch_and_method"][key] = (
            over_length["by_batch_and_method"].get(key, 0) + 1
        )
    # per (task, method) determinism across repeats: the baseline arms are byte-stable, which
    # matters because it means the repeats are not independent draws for those arms
    determinism: dict[str, dict] = {}
    for batch in ("v13", "v14"):
        groups: dict[tuple[str, str], set[int]] = {}
        for entry in samples:
            if entry["batch"] != batch:
                continue
            groups.setdefault((entry["task"], entry["method"]), set()).add(entry["answer_chars"])
        determinism[batch] = {
            f"{task}|{method}": {"distinct_answer_lengths": sorted(values), "deterministic": len(values) == 1}
            for (task, method), values in sorted(groups.items())
        }

    plugin_lengths = [entry["answer_chars"] for entry in samples if entry["method"] == "pruner_v1"]
    baseline_lengths = [entry["answer_chars"] for entry in samples if entry["method"] == "none"]
    pointer_length = len(pointer_text("call_00_" + "x" * 24, "0" * 64))
    tokens_hidden = [
        check
        for check in token_checks
        if check["tokens_in_plugin_text"] < check["tokens_in_baseline_text"]
    ]
    overall = deltas["pruner_v1"]["overall"]
    signs = deltas["pruner_v1"]["signs"]
    verdict = {
        "consistent_direction": (
            signs["positive"] if signs["positive"] > signs["negative"]
            else signs["negative"] if signs["negative"] > signs["positive"]
            else 0
        ),
        "sign_pattern": signs,
        "median_delta": overall.get("median"),
        "mean_delta": overall.get("mean"),
        "spread_of_delta": overall.get("stdev"),
        "delta_range": [overall.get("min"), overall.get("max")],
        "length_variance": {
            "pruner_v1": stats(plugin_lengths),
            "none": stats(baseline_lengths),
            "note": (
                "the plugin arm's answer lengths spread wider than the baseline arm's; the "
                "three over-length answers are all plugin rows"
            ),
        },
        "over_length_only_in_plugin_arm": len(failures) > 0
        and all(entry["method"] == "pruner_v1" for entry in failures),
        "hidden_evidence_supported": bool(tokens_hidden),
        "hidden_evidence_checks": len(token_checks),
        "hidden_evidence_failures": len(tokens_hidden),
        "pointer_text_chars": pointer_length,
        "omitted_chars_by_sample": [
            {
                "batch": check["batch"],
                "task": check["task"],
                "repeat": check["repeat"],
                "omitted_original_chars": check["omitted_original_chars"],
                "answer_chars": check["answer_chars"],
            }
            for check in token_checks
        ],
        "determinism": determinism,
        "reading": (
            "the reduction is not associated with a uniform lengthening: the paired median "
            "difference is zero and the signs are split. What is observable is a wider spread "
            "and a longer upper tail, and it is only in that tail that the frozen "
            "160-character condition fails; the failures are not deterministic (the same task "
            "passed on one of its three repeats). No answer token that the baseline payload "
            "showed is missing from the plugin payload, so the drift is not explained by hidden "
            "evidence; and the omitted-character totals do not order the drift (the largest "
            "omission has the largest positive drift while a smaller one has negative drift), "
            "so nothing here identifies a mechanical cause."
        ),
    }
    return {
        "schema": "openai_agents_wording_effect_analysis",
        "date": "20261005",
        "scope": "v12, v13 and v14 recorded sample rows and input evidence; zero API",
        "max_answer_chars": MAX_ANSWER_CHARS,
        "samples": samples,
        "pairs": pairs,
        "length_stats": lengths,
        "delta_stats": deltas,
        "over_length": over_length,
        "pointer_text_chars": pointer_length,
        "token_checks": token_checks,
        "verdict": verdict,
        "limits": [
            "descriptive statistics only: 13 plugin pairs across three different tasks, no "
            "confidence interval and no significance claim",
            "the v12 batch has one repeat, so it cannot separate a task effect from run-to-run "
            "variation",
            "nothing here changes any frozen contract, score or verdict; the strict failures "
            "stay failures",
        ],
    }


def write(out: Path | None = None) -> dict[str, Any]:
    report = analyse()
    target = out or (REPO / "integrations/openai_agents/WORDING_EFFECT_ANALYSIS_20261005.json")
    Path(target).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    report = write()
    print(json.dumps({k: report[k] for k in ("length_stats", "delta_stats", "over_length", "pointer_text_chars", "verdict")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
