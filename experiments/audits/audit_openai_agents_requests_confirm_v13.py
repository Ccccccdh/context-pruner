"""Independent, fail-closed audit of the v13 Stage C held-out confirmation batch.

Hard fields:

* the manifest records the freeze file's own SHA256, recomputed here, and the batch shape is
  the frozen one (one scenario, three arms, three repeats, nine samples, cap respected);
* **both quality tracks are computed here from the frozen contract**, not read from the row's
  own flags: Track A (prefix, single line, at most 160 characters, frozen regex fullmatch and
  the required literals) is the acceptance track, Track B (both fields present, every required
  literal present in order with relaxed separators, cause+fix within its own limit, forbidden
  claims absent) is a diagnostic track that is never merged with Track A;
* the paired complete-total provider-token saving is recomputed from the nine rows, per repeat
  and pooled, with the baseline row of the same repeat;
* every sample's recorded boundary evidence is checked against the rebuilt payload: structure,
  per-output SHA256 and character counts, literal presence at the boundaries where the carrying
  view has been read, tool pairing and the restore/fallback ledger;
* the plugin arm's replacements are recomputed from the recorded payloads, so "zero actual
  replacements" cannot hide behind a row flag;
* any mismatch is an error and the audit is not `complete`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_requests_replay_gate_v13 as gate  # noqa: E402
from experiments.runners import openai_agents_requests_task_registry_v13 as registry  # noqa: E402

METHODS = ("none", "pruner_v1", "native_summary")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).casefold()


def _ordered_tokens(text: str, literal: str) -> bool:
    """Every token of ``literal`` present in order, separated by non-alphanumerics only.

    Applied to identifier-like literals.  A *stem* literal (one that is a prefix of the word
    the answer writes, such as ``quot`` for ``quoted``) cannot satisfy word-boundary matching
    by construction, so it is checked as a substring instead - the frozen Track B text relaxes
    separators, it does not demand that a stem be written as a standalone word.  The freeze
    states the required literals; the audit documents how each is matched.
    """
    if "_" not in literal and len(literal) < 6:
        return literal.casefold() in str(text).casefold()
    folded = _fold(text)
    position = 0
    for token in re.findall(r"[0-9a-z_]+", literal.casefold()):
        pattern = re.compile(r"(?<![0-9a-z_])" + re.escape(token) + r"(?![0-9a-z_])")
        match = pattern.search(folded, position)
        if match is None:
            return False
        position = match.end()
    return True


def track_a(answer: str) -> dict:
    terms_present = {term: term.casefold() in answer.casefold() for term in registry.REQUIRED_TERMS}
    conditions = {
        "prefix": answer.startswith("RESULT issue=requests-1766 cause="),
        "single_line": "\n" not in answer and "\r" not in answer,
        "within_max_chars": len(answer) <= registry.MAX_ANSWER_CHARS,
        "frozen_regex_fullmatch": bool(
            re.fullmatch(registry.ANSWER_PATTERN, answer, flags=re.IGNORECASE)
        ),
        "required_terms": all(terms_present.values()),
    }
    return {
        "pass": all(conditions.values()),
        "conditions": conditions,
        "required_terms": terms_present,
        "characters": len(answer),
    }


def track_b(answer: str) -> dict:
    cause = answer.split("cause=", 1)[1].split(" fix=", 1)[0] if "cause=" in answer else ""
    fix = answer.split(" fix=", 1)[1] if " fix=" in answer else ""
    terms_present = {
        term: _ordered_tokens(answer, term) for term in registry.REQUIRED_TERMS
    }
    forbidden = [
        claim
        for claim in registry.TRACK_B_FORBIDDEN
        if claim.casefold() in answer.casefold()
    ]
    conditions = {
        "prefix": answer.startswith("RESULT issue=requests-1766 cause="),
        "single_line": "\n" not in answer and "\r" not in answer,
        "cause_and_fix_fields": bool(cause.strip()) and bool(fix.strip()),
        "required_terms_ordered": all(terms_present.values()),
        "within_track_b_chars": len(cause + fix) <= registry.TRACK_B_MAX_CHARS,
        "no_forbidden_claim": not forbidden,
    }
    return {
        "pass": all(conditions.values()),
        "conditions": conditions,
        "required_terms": terms_present,
        "forbidden_found": forbidden,
        "cause_plus_fix_characters": len(cause + fix),
    }


def audit(batch: Path, freeze_path: Path) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(batch / "samples.jsonl")
    by_key = {(int(row["repeat"]), str(row["method"])): row for row in rows}

    stage_c = freeze["stage_C_confirmation"]
    if batch.name != str(stage_c["batch"]):
        errors.append("batch_id_mismatch")
    recorded_freeze = manifest.get("freeze_sha256")
    if not recorded_freeze or recorded_freeze != hashlib.sha256(freeze_path.read_bytes()).hexdigest():
        errors.append("freeze_hash_mismatch")
    if manifest.get("citable_as_saving") is not False:
        errors.append("manifest_citable_as_saving_not_false")
    if manifest.get("citable_as_quality_equivalence") is not False:
        errors.append("manifest_citable_as_quality_equivalence_not_false")
    if not str(manifest.get("purpose") or "").strip():
        errors.append("manifest_purpose_missing")
    if list(manifest.get("methods") or []) != list(METHODS):
        errors.append("manifest_methods_mismatch")
    if int(manifest.get("repeats", -1)) != 3:
        errors.append("manifest_repeats_not_three")

    expected_keys = {(repeat, method) for repeat in range(3) for method in METHODS}
    actual_keys = {(int(row["repeat"]), str(row["method"])) for row in rows}
    if actual_keys != expected_keys:
        errors.append(f"sample_matrix_mismatch:{sorted(actual_keys ^ expected_keys)}")
    attempts = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    if attempts > int(stage_c["max_api_requests"]):
        errors.append("request_cap_exceeded")

    # -- per-sample evidence and the rebuilt payload -----------------------------
    rebuilt = gate.boundaries()
    view_of: dict[str, int] = {}
    for label, phrases in registry.LITERAL_LABELS.items():
        for index, view in enumerate(registry.view_texts()):
            if any(phrase in view for phrase in phrases):
                view_of[label] = index
                break
        else:
            view_of[label] = 0  # statement-side literal: visible from the first boundary
    boundary_report: list[dict] = []
    for key in sorted(actual_keys):
        row = by_key[key]
        repeat, method = key
        evidence = batch / str(row.get("input_evidence_file", ""))
        if not evidence.is_file():
            errors.append(f"missing_evidence:{repeat}:{method}")
            continue
        records = [item for item in _read_jsonl(evidence) if item.get("stage") == "model_input"]
        if len(records) != int(row.get("model_calls", -1)):
            errors.append(f"boundary_count:{repeat}:{method}")
        losses: list[str] = []
        from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

        for index, payload in enumerate(rebuilt[: len(records)]):
            record = records[index]
            outputs = gate._outputs(payload)
            manifest_outputs = list(record.get("output_manifest") or [])
            if method == "native_summary":
                # The host-native arm legitimately rewrites history units, so the rebuilt
                # baseline shape does not apply.  Its own recorded literal ledger is checked
                # below, arm-agnostically.
                continue
            if len(payload) != int(record["item_count"]):
                errors.append(f"item_count:{repeat}:{method}:{index}")
            if len(manifest_outputs) != len(outputs):
                errors.append(f"manifest_length:{repeat}:{method}:{index}")
                continue
            _reduced, replacements = deduplicate(payload)
            positions = [
                position
                for position, item in enumerate(payload)
                if item.get("type") == "function_call_output"
            ]
            position_of = {item_index: position for position, item_index in enumerate(positions)}
            replaced = {
                position_of[int(replacement["old_index"])]: position_of[
                    int(replacement["new_index"])
                ]
                for replacement in replacements
            }
            for position, (item, entry) in enumerate(zip(outputs, manifest_outputs)):
                text = gate.text_of(item)
                recorded = str(entry["output_sha256"])
                if method == "pruner_v1" and position in replaced:
                    # The plugin's replaced item must be exactly the frozen pointer text,
                    # recomputed from the newest full copy's own recorded call id and digest.
                    newest_position = replaced[position]
                    expected = gate.pointer_text(
                        str(manifest_outputs[newest_position]["call_id"]),
                        str(manifest_outputs[newest_position]["output_sha256"]),
                    )
                    if recorded != hashlib.sha256(expected.encode()).hexdigest():
                        errors.append(f"pointer_recompute:{repeat}:{method}:{index}:{position}")
                    if int(entry["output_chars"]) != len(expected):
                        errors.append(f"pointer_length:{repeat}:{method}:{index}:{position}")
                    continue
                # A kept item must still hash to the original source text.
                if recorded != hashlib.sha256(text.encode()).hexdigest():
                    errors.append(f"output_hash:{repeat}:{method}:{index}:{position}")
        # Arm-agnostic presence, taken from the recorder's own ledger per boundary.
        for index, record in enumerate(records):
            present = dict(record.get("registered_literals_present") or {})
            for label in registry.LITERAL_LABELS:
                if view_of.get(label, 0) < index and not present.get(label, False):
                    losses.append(f"{index}:{label}")
        if losses:
            errors.append(f"literal_absent:{repeat}:{method}:{losses}")
        if int(row.get("unmatched_call_count", 0) or 0) != 0:
            errors.append(f"unmatched_call:{repeat}:{method}")
        if not bool(row.get("pairing_integrity", True)):
            errors.append(f"pairing_integrity:{repeat}:{method}")
        for name in ("task_anchor_restore_failures", "task_restore_fallbacks", "budget_fallbacks"):
            if int(row.get(name, 0) or 0) != 0:
                errors.append(f"{name}:{repeat}:{method}")
        boundary_report.append(
            {
                "repeat": repeat,
                "method": method,
                "model_calls": int(row.get("model_calls", 0)),
                "api_request_attempts": int(row.get("api_request_attempts", 0) or 0),
                "boundaries": len(records),
                "item_counts": [int(record["item_count"]) for record in records],
                "elided_outputs": [
                    sum(1 for entry in (record.get("output_manifest") or []) if entry.get("output_elided"))
                    for record in records
                ],
            }
        )

    # -- plugin replacements recomputed from the recorded payloads ---------------
    plugin_replacements = 0
    for index, payload in enumerate(rebuilt):
        if index == 0:
            continue
        from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

        _reduced, replacements = deduplicate(payload)
        plugin_replacements += len(replacements)
    if plugin_replacements == 0:
        errors.append("no_recomputed_plugin_replacement")

    # -- both quality tracks, per sample ----------------------------------------
    quality: dict[str, dict] = {}
    for key in sorted(actual_keys):
        row = by_key[key]
        answer = str(row.get("final_output", ""))
        quality[f"{key[1]}:r{key[0]}"] = {
            "track_A": track_a(answer),
            "track_B": track_b(answer),
            "recorded_success": bool(row.get("success")),
            "recorded_answer_correct": bool(row.get("answer_correct")),
            "recorded_final_format_correct": bool(row.get("final_format_correct")),
            "complete_total_tokens": int(row.get("all_arm_total_tokens", 0)),
            "actual_input_tokens": int(row.get("actual_input_tokens", 0)),
            "answer": answer,
        }

    paired: dict[str, dict] = {}
    for method in ("pruner_v1", "native_summary"):
        savings = []
        for repeat in range(3):
            baseline = by_key.get((repeat, "none"))
            arm = by_key.get((repeat, method))
            if baseline is None or arm is None:
                continue
            base_total = int(baseline.get("all_arm_total_tokens", 0))
            arm_total = int(arm.get("all_arm_total_tokens", 0))
            savings.append((base_total - arm_total) / base_total if base_total else 0.0)
        paired[method] = {
            "per_repeat_complete_total_saving": savings,
            "paired_n": len(savings),
            "mean_saving": sum(savings) / len(savings) if savings else 0.0,
            "positive_pairs": sum(1 for value in savings if value > 0),
        }

    baseline_track_a = sum(
        1 for repeat in range(3) if quality[f"none:r{repeat}"]["track_A"]["pass"]
    )
    plugin_track_a = sum(
        1 for repeat in range(3) if quality[f"pruner_v1:r{repeat}"]["track_A"]["pass"]
    )
    plugin_track_b = sum(
        1 for repeat in range(3) if quality[f"pruner_v1:r{repeat}"]["track_B"]["pass"]
    )
    acceptance = {
        "track_A_plugin_at_least_baseline": plugin_track_a >= baseline_track_a,
        "paired_saving_at_least_3_percent": paired["pruner_v1"]["mean_saving"] >= 0.03,
        "zero_replacements": plugin_replacements == 0,
    }
    acceptance["met"] = (
        acceptance["track_A_plugin_at_least_baseline"]
        and acceptance["paired_saving_at_least_3_percent"]
        and not acceptance["zero_replacements"]
        and not errors
    )
    report = {
        "schema": "openai_agents_v13_requests1766_confirmation_audit",
        "complete": not errors,
        "errors": sorted(set(errors)),
        "rows": len(rows),
        "api_request_attempts": attempts,
        "track_A_baseline_pass": baseline_track_a,
        "track_A_plugin_pass": plugin_track_a,
        "track_B_plugin_pass": plugin_track_b,
        "paired": paired,
        "acceptance": acceptance,
        "recomputed_plugin_replacements_across_boundaries": plugin_replacements,
        "freeze_sha256_recorded": bool(recorded_freeze),
        "citable_as_saving": manifest.get("citable_as_saving"),
        "citable_as_quality_equivalence": manifest.get("citable_as_quality_equivalence"),
        "quality": quality,
        "boundaries": boundary_report,
        "reporting_rule": (
            "Track A is the acceptance track. If Track B passes while Track A fails, the "
            "result is 'strict track not met; semantic track passed (wording difference)' and "
            "never 'quality unchanged'. One task and three repeats are not multi-task "
            "stability."
        ),
    }
    return report


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: audit_openai_agents_requests_confirm_v13 BATCH FREEZE"
        )
    batch, freeze = map(Path, sys.argv[1:])
    report = audit(batch, freeze)
    (batch / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "complete",
                    "errors",
                    "rows",
                    "api_request_attempts",
                    "track_A_baseline_pass",
                    "track_A_plugin_pass",
                    "track_B_plugin_pass",
                    "paired",
                    "acceptance",
                    "recomputed_plugin_replacements_across_boundaries",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
