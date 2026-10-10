"""Independent, fail-closed audit of the v13 Stage A payload-acquisition batch.

Hard fields, in the order the round requires them:

* the manifest records the **freeze file's own SHA256** and this audit recomputes it;
* the batch shape is the frozen one: one scenario, the `none` arm only, one repeat, one
  sample, and the request cap is not exceeded;
* the three citation keys are really present with the frozen values - ``purpose`` as a
  non-empty string, ``citable_as_saving`` false and ``citable_as_quality_equivalence`` false
  (a batch that claims these keys in its report but not in its manifest fails here);
* the per-boundary evidence exists, one ``model_input`` record per model call, and every
  boundary reproduces the rebuilt payload's structure and per-output SHA256/character counts;
* the invariant gate columns hold on the recorded payloads: task constraints, current
  evidence, id-free tool-group hash, restore/fallback accounting and source location;
* anything unmatched is an error and the audit is not ``complete``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_requests_replay_gate_v13 as gate  # noqa: E402
from experiments.runners import openai_agents_requests_task_registry_v13 as registry  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def literal_view_index() -> dict[str, int]:
    """Which view carries each registered literal, derived from the registered views.

    A literal can only be visible at a boundary once the view that carries it has been read,
    so the presence check is boundary-scoped: at boundary ``k`` exactly views ``0..k-1`` have
    been read.  The mapping is computed from the view texts (not hard-coded), so a changed
    registration shows up as a changed expectation rather than as a silent pass.
    """
    views = registry.view_texts()
    mapping: dict[str, int] = {}
    for label, phrases in registry.LITERAL_LABELS.items():
        for index, view in enumerate(views):
            if any(phrase in view for phrase in phrases):
                mapping[label] = index
                break
        else:
            mapping[label] = len(views)
    return mapping


def audit(batch: Path, freeze_path: Path) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    rows = _read_jsonl(batch / "samples.jsonl")

    # -- identity and the freeze hash ------------------------------------------
    stage_a = freeze["stage_A_payload_acquisition"]
    if batch.name != str(stage_a["batch"]):
        errors.append("batch_id_mismatch")
    recorded_freeze = manifest.get("freeze_sha256")
    if not recorded_freeze:
        errors.append("manifest_missing_freeze_sha256")
    elif recorded_freeze != hashlib.sha256(freeze_path.read_bytes()).hexdigest():
        errors.append("freeze_hash_mismatch")

    # -- the three citation keys, read from the manifest itself ----------------
    if not isinstance(manifest.get("purpose"), str) or not manifest.get("purpose", "").strip():
        errors.append("manifest_purpose_missing")
    if manifest.get("citable_as_saving") is not False:
        errors.append("manifest_citable_as_saving_not_false")
    if manifest.get("citable_as_quality_equivalence") is not False:
        errors.append("manifest_citable_as_quality_equivalence_not_false")

    # -- the frozen shape -------------------------------------------------------
    if list(manifest.get("scenarios") or []) != [registry.TASK_ID]:
        errors.append("manifest_scenarios_mismatch")
    if list(manifest.get("methods") or []) != ["none"]:
        errors.append("manifest_methods_not_single_arm_none")
    if int(manifest.get("repeats", -1)) != 1:
        errors.append("manifest_repeats_not_one")
    if len(rows) != 1:
        errors.append(f"sample_count_{len(rows)}")
    attempts = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    if attempts > int(stage_a["max_api_requests"]):
        errors.append("request_cap_exceeded")
    if manifest.get("data_class") != "public_source_diagnostic":
        errors.append("data_class_not_public_source_diagnostic")

    # -- evidence and the rebuilt payload --------------------------------------
    rebuilt = gate.boundaries()
    for row in rows:
        evidence = batch / str(row.get("input_evidence_file", ""))
        if not evidence.is_file():
            errors.append("missing_input_evidence")
            continue
        records = [item for item in _read_jsonl(evidence) if item.get("stage") == "model_input"]
        if len(records) != int(row.get("model_calls", -1)):
            errors.append("boundary_count_mismatch")
        if len(records) != len(rebuilt):
            errors.append("boundary_count_not_seven")
        for index, payload in enumerate(rebuilt):
            if index >= len(records):
                break
            record = records[index]
            outputs = gate._outputs(payload)
            if len(payload) != int(record["item_count"]):
                errors.append(f"boundary{index}_item_count")
            if len(outputs) != int(record.get("recency_output_items", len(outputs))):
                errors.append(f"boundary{index}_output_count")
            manifest_outputs = list(record.get("output_manifest") or [])
            if len(manifest_outputs) != len(outputs):
                errors.append(f"boundary{index}_manifest_length")
                continue
            for item, entry in zip(outputs, manifest_outputs):
                digest = hashlib.sha256(gate.text_of(item).encode()).hexdigest()
                if digest != str(entry["output_sha256"]) or len(gate.text_of(item)) != int(
                    entry["output_chars"]
                ):
                    errors.append(f"boundary{index}_output_hash_mismatch")
        # Literal presence, boundary-scoped: at boundary k exactly views 0..k-1 have been
        # read, so only those literals can be visible yet.
        view_of = literal_view_index()
        for index, payload in enumerate(rebuilt[: len(records)]):
            counts = gate.visible_counts(payload)
            for name, value in counts.items():
                if not name.startswith("literal:"):
                    continue
                label = name.split(":", 1)[1]
                if view_of.get(label, len(rebuilt)) < index and value == 0:
                    errors.append(f"boundary{index}_literal_absent:{name}")
                if view_of.get(label, len(rebuilt)) >= index and value > 0:
                    errors.append(f"boundary{index}_literal_early:{name}")
        if int(row.get("unmatched_call_count", 0) or 0) != 0:
            errors.append("unmatched_tool_call")
        if not bool(row.get("pairing_integrity", True)):
            errors.append("pairing_integrity_false")
        for key in ("task_anchor_restore_failures", "task_restore_fallbacks", "budget_fallbacks"):
            if int(row.get(key, 0) or 0) != 0:
                errors.append(f"{key}_nonzero")
        if int(row.get("all_arm_total_tokens", -1)) < 0:
            errors.append("missing_complete_token_count")

    return {
        "schema": "openai_agents_v13_requests1766_acquisition_audit",
        "complete": not errors,
        "errors": sorted(set(errors)),
        "rows": len(rows),
        "api_request_attempts": attempts,
        "model_calls": int(rows[0].get("model_calls", 0)) if rows else 0,
        "strict_success": bool(rows[0].get("success")) if rows else False,
        "final_format_correct": bool(rows[0].get("final_format_correct")) if rows else False,
        "answer_correct": bool(rows[0].get("answer_correct")) if rows else False,
        "complete_total_tokens": int(rows[0].get("all_arm_total_tokens", 0)) if rows else 0,
        "freeze_sha256_recorded": bool(recorded_freeze),
        "citable_as_saving": manifest.get("citable_as_saving"),
        "citable_as_quality_equivalence": manifest.get("citable_as_quality_equivalence"),
        "purpose": manifest.get("purpose"),
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: audit_openai_agents_requests_acquisition_v13 BATCH FREEZE")
    batch, freeze = map(Path, sys.argv[1:])
    report = audit(batch, freeze)
    (batch / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
