"""Executable zero-API positive and negative controls for the v15 auditor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.audits import audit_openai_agents_v15_acquisition as audit
from experiments.runners import openai_agents_multitask_replay_gate_v14 as replay
from experiments.runners import openai_agents_v15_registry as registry


def test_synthetic_full_acquisition_audit_and_negative_controls(tmp_path: Path):
    task = registry.task_ids()[0]
    replay.registry = registry
    old_freeze, old_runs = audit.FREEZE, audit.RUNS
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"task_ids": [task], "max_api_requests_per_task": 36, "source_sha256": {}}), encoding="utf-8")
    audit.FREEZE, audit.RUNS = freeze, tmp_path
    root = tmp_path / f"openai-repo-diagnostic-v15-{task}-acquisition-01"
    (root / "input-evidence").mkdir(parents=True)
    (root / "format-evidence").mkdir()
    boundaries = replay.boundaries(task)
    records = []
    for items in boundaries:
        records.append({
            "stage": "model_input",
            "item_count": len(items),
            "recency_message_items": len(replay.messages(items)),
            "recency_output_items": len(replay.outputs(items)),
            "output_manifest": [{"output_sha256": hashlib.sha256(x["output"].encode()).hexdigest(), "output_chars": len(x["output"])} for x in replay.outputs(items)],
            "input_bytes": replay.payload_bytes(items),
            "fallback_reason": "",
            "task_anchor_restore_failures": 0,
            "task_restore_fallbacks": 0,
            "budget_fallbacks": 0,
        })
    evidence_path = root / "input-evidence" / f"{task}-0-none.jsonl"
    evidence_path.write_text("".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
    (root / "format-evidence" / f"{task}-0-none.jsonl").write_text("", encoding="utf-8")
    row = {
        "scenario": task, "repeat": 0, "method": "none", "success": True,
        "bounded_final_schema": "all_arm_one_retry_v15",
        "bounded_final_usage_in_recorded_model_totals": True,
        "bounded_final_repair_calls": 0,
        "bounded_final_repair_ledger": [],
        "bounded_final_repair_evidence_file": f"format-evidence/{task}-0-none.jsonl",
        "actual_input_tokens_by_call": [1] * 7,
        "actual_output_tokens_by_call": [1] * 7,
        "actual_input_tokens": 7, "actual_output_tokens": 7,
        "model_calls": 7, "summary_calls": 0, "api_request_attempts": 7,
        "summary_input_tokens": 0, "summary_output_tokens": 0,
        "all_arm_total_tokens": 14,
    }
    (root / "samples.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    manifest = {"freeze_sha256": hashlib.sha256(freeze.read_bytes()).hexdigest(), "citable_as_saving": False, "citable_as_quality_equivalence": False}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    try:
        positive = audit.audit(task)
        assert positive["complete"] and positive["replay_qualified_for_pilot_gate"], positive
        row["actual_input_tokens"] = 6
        (root / "samples.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        assert any("provider_token_sum" in x for x in audit.audit(task)["errors"])
        row["actual_input_tokens"] = 7
        (root / "samples.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        records[-1]["output_manifest"][-1]["output_sha256"] = "0" * 64
        evidence_path.write_text("".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
        assert any("output differs" in x for x in audit.audit(task)["errors"])
    finally:
        audit.FREEZE, audit.RUNS = old_freeze, old_runs
