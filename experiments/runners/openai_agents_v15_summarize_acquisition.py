"""Read-only aggregation of the three frozen v15 acquisition audits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.runners import openai_agents_v15_registry as registry


REPO = registry.REPO
RUNS = REPO / "runs/stage5-openai-agents-api"
OUT = REPO / "integrations/openai_agents/V15_ACQUISITION_02_ADMISSION_20261006.json"
FREEZE = REPO / "integrations/openai_agents/V15_ACQUISITION_FREEZE_02_20261006.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    rows = []
    for task in registry.task_ids():
        root = RUNS / f"openai-repo-diagnostic-v15-{task}-acquisition-02"
        sample_path = root / "samples.jsonl"
        audit_path = root / "v15_acquisition_audit.json"
        manifest_path = root / "manifest.json"
        samples = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines() if line]
        if len(samples) != 1:
            raise RuntimeError(f"not one acquisition sample: {task}")
        sample = samples[0]
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["freeze_sha256"] != _sha(FREEZE) or not audit["complete"] or audit["errors"]:
            raise RuntimeError(f"audit or frozen source chain not clean: {task}")
        rows.append({
            "task": task,
            "instance_id": registry.task(task)["instance_id"],
            "batch_id": root.name,
            "requests": int(sample["api_request_attempts"]),
            "complete_provider_tokens": int(sample["all_arm_total_tokens"]),
            "strict_baseline_success": bool(sample["success"]),
            "answer_correct": bool(sample["answer_correct"]),
            "final_format_correct": bool(sample["final_format_correct"]),
            "final_output_chars": int(sample["final_output_chars"]),
            "format_repair_calls": int(sample["bounded_final_repair_calls"]),
            "repair_ledger": sample["bounded_final_repair_ledger"],
            "audit_complete": bool(audit["complete"]),
            "real_payload_replay_qualified": bool(audit["replay_qualified_for_pilot_gate"]),
            "replay_byte_projection_not_provider_tokens": audit["replay_projection_bytes_not_provider_tokens"],
            "safe_candidates": audit["replay_safe_candidates"],
            "artifact_sha256": {name: _sha(path) for name, path in {"samples.jsonl": sample_path, "manifest.json": manifest_path, "v15_acquisition_audit.json": audit_path}.items()},
        })
    admitted = [row["task"] for row in rows if row["strict_baseline_success"] and row["real_payload_replay_qualified"]]
    result = {
        "schema": "openai_agents_v15_acquisition_02_admission",
        "freeze_sha256": _sha(FREEZE),
        "controlled_diagnostic_workload": True,
        "natural_agent_behavior_claim": False,
        "paid_request_attempts": sum(row["requests"] for row in rows),
        "complete_provider_tokens_all_acquisitions": sum(row["complete_provider_tokens"] for row in rows),
        "rows": rows,
        "admitted_tasks": admitted,
        "required_admitted_tasks": 3,
        "pilot_gate_met": len(admitted) >= 3,
        "decision": "STOP_BEFORE_THREE_ARM_PILOT" if len(admitted) < 3 else "READY_FOR_SEPARATE_PILOT_FREEZE",
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "spent_task_not_retuned": "django_textchoices_string_value",
        "fourth_preselected_candidate": "django__django-13512; using it now would be a new exploratory selection requiring fresh registration and holdout status, not completion of the original three-task gate",
        "failed_sandbox_batch_kept": "openai-repo-diagnostic-v15-django_field_error_messages_copy-acquisition-01",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paid_request_attempts": result["paid_request_attempts"], "admitted": len(admitted), "pilot_gate_met": result["pilot_gate_met"], "decision": result["decision"]}))


if __name__ == "__main__":
    main()
