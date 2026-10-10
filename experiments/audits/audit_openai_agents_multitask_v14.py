"""Independent, fail-closed audit of the v14 multi-task chain (acquisition and pilot).

Both modes recompute what they judge instead of reading a self-reported flag:

* the freeze file's own SHA256, the batch shape, the task matrix and the request cap;
* **Track A and Track B per sample, computed from the frozen contract** (prefix, single line,
  length, frozen regex fullmatch, required literals; Track B relaxed wording, both fields,
  its own length bound and the forbidden claims) - never merged;
* per-task and pooled paired complete-total provider-token savings, with the spread between
  tasks, because a pooled number alone is not multi-task stability;
* the recorded boundary evidence against the rebuilt payload: structure, per-output SHA256 and
  character counts, arm-appropriate handling of the plugin's pointers (recomputed from the
  newest full copy's recorded call id and digest) and of the host-native arm (its own recorded
  literal ledger), literal presence scoped to the boundaries where the carrying view has been
  read, tool pairing and the restore/fallback ledger;
* the persisted mechanism counters on the plugin rows, which the earlier round could not read.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from experiments.runners import openai_agents_multitask_registry_v14 as registry  # noqa: E402
from experiments.runners import openai_agents_multitask_replay_gate_v14 as gate  # noqa: E402

RUNS = REPO / "runs/stage5-openai-agents-api"
FREEZE = REPO / "integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json"
METHODS = ("none", "pruner_v1", "native_summary")
PERSISTED_ON_PLUGIN_ROWS = (
    "exact_duplicate_replacements",
    "exact_duplicate_saved_bytes",
    "trigger_gate_triggered_calls",
    "narrow_guard_protected_unit_count",
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).casefold()


def ordered_tokens(text: str, literal: str) -> bool:
    if "_" not in literal and len(literal) < 6:
        return literal.casefold() in str(text).casefold()
    folded = fold(text)
    position = 0
    for token in re.findall(r"[0-9a-z_]+", literal.casefold()):
        pattern = re.compile(r"(?<![0-9a-z_])" + re.escape(token) + r"(?![0-9a-z_])")
        match = pattern.search(folded, position)
        if match is None:
            return False
        position = match.end()
    return True


def track_a(answer: str, task_id: str) -> dict:
    entry = registry.task(task_id)
    terms = {term: term.casefold() in answer.casefold() for term in entry["required_terms"]}
    conditions = {
        "prefix": answer.startswith(f"RESULT issue={entry['short_id']} cause="),
        "single_line": "\n" not in answer and "\r" not in answer,
        "within_max_chars": len(answer) <= registry.payload()["max_answer_chars"],
        "frozen_regex_fullmatch": bool(
            re.fullmatch(entry["answer_pattern"], answer, flags=re.IGNORECASE)
        ),
        "required_terms": all(terms.values()),
    }
    return {"pass": all(conditions.values()), "conditions": conditions, "required_terms": terms}


def track_b(answer: str, task_id: str) -> dict:
    entry = registry.task(task_id)
    cause = answer.split("cause=", 1)[1].split(" fix=", 1)[0] if "cause=" in answer else ""
    fix = answer.split(" fix=", 1)[1] if " fix=" in answer else ""
    terms = {term: ordered_tokens(answer, term) for term in entry["required_terms"]}
    forbidden = [
        claim
        for claim in registry.payload()["track_b_forbidden"]
        if claim.casefold() in answer.casefold()
    ]
    conditions = {
        "prefix": answer.startswith(f"RESULT issue={entry['short_id']} cause="),
        "single_line": "\n" not in answer and "\r" not in answer,
        "cause_and_fix_fields": bool(cause.strip()) and bool(fix.strip()),
        "required_terms_ordered": all(terms.values()),
        "within_track_b_chars": len(cause + fix)
        <= int(registry.payload()["track_b_max_chars"]),
        "no_forbidden_claim": not forbidden,
    }
    return {
        "pass": all(conditions.values()),
        "conditions": conditions,
        "required_terms": terms,
        "forbidden_found": forbidden,
    }


def literal_view_index(task_id: str) -> dict[str, int]:
    views = registry.view_texts(task_id)
    entry = registry.task(task_id)
    mapping: dict[str, int] = {}
    for label, phrases in entry["literals"].items():
        for index, text in enumerate(views):
            if any(phrase in text for phrase in phrases):
                mapping[label] = index
                break
        else:
            mapping[label] = 0
    return mapping


def audit(batch: Path, freeze_path: Path, mode: str) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    rows = read_jsonl(batch / "samples.jsonl")
    by_key = {(str(row["scenario"]), int(row["repeat"]), str(row["method"])): row for row in rows}
    tasks = list(manifest.get("held_out_task_ids") or [])

    # -- identity, freeze hash, one-pass manifest --------------------------------
    expected_batch = (
        freeze["stage_acquisition"].get("batch")
        if mode == "acquisition"
        else freeze["stage_pilot"]["batch"]
    )
    if expected_batch and batch.name != expected_batch:
        errors.append("batch_id_mismatch")
    recorded = manifest.get("freeze_sha256")
    if not recorded or recorded != hashlib.sha256(freeze_path.read_bytes()).hexdigest():
        errors.append("freeze_hash_mismatch")
    if manifest.get("citable_as_saving") is not False:
        errors.append("manifest_citable_as_saving_not_false")
    if manifest.get("citable_as_quality_equivalence") is not False:
        errors.append("manifest_citable_as_quality_equivalence_not_false")
    if not str(manifest.get("purpose") or "").strip():
        errors.append("manifest_purpose_missing")
    if not manifest.get("manifest_written_in_one_pass"):
        errors.append("manifest_not_written_in_one_pass")
    if "manifest_amendments" in manifest:
        errors.append("manifest_had_a_post_run_amendment")

    # -- shape and caps ----------------------------------------------------------
    attempts = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    if mode == "acquisition":
        if list(manifest.get("methods") or []) != ["none"]:
            errors.append("acquisition_methods_not_single_arm_none")
        if int(manifest.get("repeats", -1)) != 1:
            errors.append("acquisition_repeats_not_one")
        if len(rows) != 1:
            errors.append(f"acquisition_sample_count_{len(rows)}")
        if attempts > 12:
            errors.append("acquisition_request_cap_exceeded")
    else:
        expected_keys = {
            (task_id, repeat, method)
            for task_id in tasks
            for repeat in range(3)
            for method in METHODS
        }
        if set(by_key) != expected_keys:
            errors.append(f"pilot_matrix_mismatch:{sorted(set(by_key) ^ expected_keys)}")
        if list(manifest.get("methods") or []) != list(METHODS):
            errors.append("pilot_methods_mismatch")
        if int(manifest.get("repeats", -1)) != 3:
            errors.append("pilot_repeats_not_three")
        cap = int(freeze["stage_pilot"]["max_api_requests"])
        if attempts > cap:
            errors.append("pilot_request_cap_exceeded")
        budget = manifest.get("request_budget") or {}
        if int(budget.get("max_summary_calls_per_sample", -1)) != int(
            freeze["stage_pilot"]["max_summary_calls_per_sample"]
        ):
            errors.append("summary_cap_not_the_frozen_value")
        summary_calls = sum(int(row.get("summary_calls", 0) or 0) for row in rows)
        if summary_calls > int(freeze["stage_pilot"]["max_summary_calls_per_sample"]) * len(rows):
            errors.append("summary_cap_exceeded")

    # -- per-sample evidence and rebuilt payloads --------------------------------
    boundary_report: list[dict] = []
    for key in sorted(set(by_key)):
        task_id, repeat, method = key
        row = by_key[key]
        evidence = batch / str(row.get("input_evidence_file", ""))
        if not evidence.is_file():
            errors.append(f"missing_evidence:{task_id}:{repeat}:{method}")
            continue
        records = [item for item in read_jsonl(evidence) if item.get("stage") == "model_input"]
        if len(records) != int(row.get("model_calls", -1)):
            errors.append(f"boundary_count:{task_id}:{repeat}:{method}")
        rebuilt = gate.boundaries(task_id)
        view_of = literal_view_index(task_id)
        losses: list[str] = []
        from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

        for index, payload in enumerate(rebuilt[: len(records)]):
            record = records[index]
            manifest_outputs = list(record.get("output_manifest") or [])
            payload_outputs = gate.outputs(payload)
            if method == "none":
                if len(payload) != int(record["item_count"]):
                    errors.append(f"item_count:{task_id}:{repeat}:{method}:{index}")
                if len(manifest_outputs) != len(payload_outputs):
                    errors.append(f"manifest_length:{task_id}:{repeat}:{method}:{index}")
                    continue
                for position, (item, entry) in enumerate(zip(payload_outputs, manifest_outputs)):
                    if hashlib.sha256(gate.text_of(item).encode()).hexdigest() != str(
                        entry["output_sha256"]
                    ):
                        errors.append(
                            f"output_hash:{task_id}:{repeat}:{method}:{index}:{position}"
                        )
            elif method == "pruner_v1":
                if len(payload) != int(record["item_count"]):
                    errors.append(f"item_count:{task_id}:{repeat}:{method}:{index}")
                _reduced, replacements = deduplicate(payload)
                positions = [
                    position
                    for position, item in enumerate(payload)
                    if item.get("type") == "function_call_output"
                ]
                position_of = {
                    item_index: position for position, item_index in enumerate(positions)
                }
                replaced = {
                    position_of[int(r["old_index"])]: position_of[int(r["new_index"])]
                    for r in replacements
                }
                # An item may be the untouched original (the shared soft-budget gate only lets
                # the reduction act above the threshold, so early boundaries legitimately keep
                # duplicates) or exactly the frozen pointer text. Anything else is an error.
                for position, (item, entry) in enumerate(zip(payload_outputs, manifest_outputs)):
                    allowed = {hashlib.sha256(gate.text_of(item).encode()).hexdigest()}
                    if position in replaced:
                        newest = replaced[position]
                        expected = gate.pointer_text(
                            str(manifest_outputs[newest]["call_id"]),
                            str(manifest_outputs[newest]["output_sha256"]),
                        )
                        allowed.add(hashlib.sha256(expected.encode()).hexdigest())
                    if str(entry["output_sha256"]) not in allowed:
                        errors.append(
                            f"unrecomputable_change:{task_id}:{repeat}:{method}:{index}:{position}"
                        )
        # arm-agnostic presence, from the recorder's own per-boundary ledger
        for index, record in enumerate(records):
            present = dict(record.get("registered_literals_present") or {})
            for label in registry.task(task_id)["literals"]:
                if view_of.get(label, 0) < index and not present.get(label, False):
                    losses.append(f"{index}:{label}")
        if losses:
            errors.append(f"literal_absent:{task_id}:{repeat}:{method}:{losses}")
        if int(row.get("unmatched_call_count", 0) or 0) != 0:
            errors.append(f"unmatched_call:{task_id}:{repeat}:{method}")
        if not bool(row.get("pairing_integrity", True)):
            errors.append(f"pairing_integrity:{task_id}:{repeat}:{method}")
        for name in ("task_anchor_restore_failures", "task_restore_fallbacks", "budget_fallbacks"):
            if int(row.get(name, 0) or 0) != 0:
                errors.append(f"{name}:{task_id}:{repeat}:{method}")
        if method == "pruner_v1":
            missing = [name for name in PERSISTED_ON_PLUGIN_ROWS if name not in row]
            if missing:
                errors.append(f"persisted_counters_missing:{task_id}:{repeat}:{missing}")
            elif int(row.get("exact_duplicate_replacements", 0) or 0) <= 0:
                errors.append(f"zero_replacements:{task_id}:{repeat}")
        boundary_report.append(
            {
                "task_id": task_id,
                "repeat": repeat,
                "method": method,
                "model_calls": int(row.get("model_calls", 0)),
                "requests": int(row.get("api_request_attempts", 0) or 0),
                "boundaries": len(records),
                "item_counts": [int(record["item_count"]) for record in records],
            }
        )

    # -- quality tracks and paired savings ---------------------------------------
    quality: dict[str, dict] = {}
    for key in sorted(set(by_key)):
        task_id, repeat, method = key
        answer = str(by_key[key].get("final_output", ""))
        quality[f"{task_id}|{method}|r{repeat}"] = {
            "track_A": track_a(answer, task_id),
            "track_B": track_b(answer, task_id),
            "recorded_success": bool(by_key[key].get("success")),
            "complete_total_tokens": int(by_key[key].get("all_arm_total_tokens", 0)),
            "answer_chars": len(answer),
        }
    repeats_present = sorted({key[1] for key in by_key})
    per_task: dict[str, dict] = {}
    pooled: dict[str, list[float]] = {method: [] for method in ("pruner_v1", "native_summary")}
    for task_id in tasks:
        entry: dict = {
            "track_A_baseline_pass": sum(
                1
                for repeat in repeats_present
                if quality[f"{task_id}|none|r{repeat}"]["track_A"]["pass"]
            ),
            "track_B_baseline_pass": sum(
                1
                for repeat in repeats_present
                if quality[f"{task_id}|none|r{repeat}"]["track_B"]["pass"]
            ),
        }
        for method in ("pruner_v1", "native_summary"):
            present_repeats = [
                repeat
                for repeat in repeats_present
                if (task_id, repeat, method) in by_key
                and f"{task_id}|{method}|r{repeat}" in quality
            ]
            savings = []
            for repeat in present_repeats:
                baseline = by_key.get((task_id, repeat, "none"))
                arm = by_key.get((task_id, repeat, method))
                if not baseline or not arm:
                    continue
                base_total = int(baseline.get("all_arm_total_tokens", 0))
                arm_total = int(arm.get("all_arm_total_tokens", 0))
                savings.append((base_total - arm_total) / base_total if base_total else 0.0)
            entry[f"{method}_track_A_pass"] = sum(
                1
                for repeat in present_repeats
                if quality[f"{task_id}|{method}|r{repeat}"]["track_A"]["pass"]
            )
            entry[f"{method}_track_B_pass"] = sum(
                1
                for repeat in present_repeats
                if quality[f"{task_id}|{method}|r{repeat}"]["track_B"]["pass"]
            )
            entry[f"{method}_per_repeat_saving"] = savings
            entry[f"{method}_mean_saving"] = sum(savings) / len(savings) if savings else 0.0
            entry[f"{method}_positive_pairs"] = sum(1 for value in savings if value > 0)
            pooled[method].extend(savings)
        per_task[task_id] = entry
    pooled_stats = {
        method: {
            "paired_n": len(values),
            "mean_saving": sum(values) / len(values) if values else 0.0,
            "min_saving": min(values) if values else 0.0,
            "max_saving": max(values) if values else 0.0,
            "positive_pairs": sum(1 for value in values if value > 0),
        }
        for method, values in pooled.items()
    }
    per_task_means = [per_task[task_id]["pruner_v1_mean_saving"] for task_id in tasks]
    dispersion = {
        "per_task_means": per_task_means,
        "spread": (max(per_task_means) - min(per_task_means)) if per_task_means else 0.0,
        "all_tasks_positive": all(value > 0 for value in per_task_means),
        "all_tasks_above_floor": all(
            value >= float(freeze["admission_rules"]["projection_floor"])
            for value in per_task_means
        ),
        "note": (
            "the spread between task means is the stability evidence; the pooled mean alone is "
            "not multi-task stability"
        ),
    }
    acceptance = {
        "track_A_all_tasks": all(
            per_task[task_id]["pruner_v1_track_A_pass"]
            >= per_task[task_id]["track_A_baseline_pass"]
            for task_id in tasks
        ),
        "pooled_saving_at_least_3_percent": pooled_stats["pruner_v1"]["mean_saving"]
        >= float(freeze["admission_rules"]["projection_floor"]),
        "all_tasks_positive": dispersion["all_tasks_positive"],
    }
    acceptance["met"] = all(acceptance.values()) and not errors
    return {
        "schema": "openai_agents_v14_multitask_audit",
        "mode": mode,
        "complete": not errors,
        "errors": sorted(set(errors)),
        "rows": len(rows),
        "tasks": tasks,
        "api_request_attempts": attempts,
        "summary_calls": sum(int(row.get("summary_calls", 0) or 0) for row in rows),
        "per_task": per_task,
        "pooled": pooled_stats,
        "dispersion": dispersion,
        "acceptance": acceptance,
        "quality": quality,
        "boundaries": boundary_report,
        "reporting_rule": (
            "Track A is the acceptance track. If Track B passes while Track A fails, the result "
            "is 'strict track not met; semantic track passed (wording difference)' and never "
            "'quality unchanged'."
        ),
    }


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in {"acquisition", "pilot"}:
        raise SystemExit("usage: audit_openai_agents_multitask_v14 {acquisition|pilot} BATCH FREEZE")
    mode, batch, freeze = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    report = audit(batch, freeze, mode)
    (batch / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "mode",
                    "complete",
                    "errors",
                    "rows",
                    "api_request_attempts",
                    "summary_calls",
                    "pooled",
                    "dispersion",
                    "acceptance",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
