"""Write a write-once v15 acquisition freeze after source and audit smoke gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.runners import openai_agents_v15_registry as registry


REPO = registry.REPO
GATE = REPO / "integrations/openai_agents/V15_SOURCE_CONTRACT_GATE_20261006.json"
OUT = REPO / "integrations/openai_agents/V15_ACQUISITION_FREEZE_02_20261006.json"
SOURCES = [
    "integrations/openai_agents/V15_ACQUISITION_FREEZE_20261006.json",
    "integrations/openai_agents/V15_PRESELECT_20261006.json",
    "integrations/openai_agents/V15_SOURCE_REGISTRATION_20261006.json",
    "integrations/openai_agents/V15_SOURCE_CONTRACT_GATE_20261006.json",
    "experiments/runners/openai_agents_v15_registry.py",
    "experiments/runners/run_openai_agents_v15_acquisition.py",
    "experiments/audits/audit_openai_agents_v15_acquisition.py",
    "experiments/runners/run_openai_agents_v15_acquisition_retry.py",
    "experiments/audits/audit_openai_agents_v15_acquisition_retry.py",
    "experiments/runners/openai_agents_bounded_final_v15.py",
    "experiments/runners/openai_agents_strict_wiring_v15.py",
    "experiments/audits/audit_openai_agents_strict_v15.py",
    "experiments/runners/openai_agents_multitask_chain_v14.py",
    "experiments/runners/run_openai_agents_multitask_acquisition_v14.py",
    "experiments/runners/run_openai_agents_repo_diagnostic_v8.py",
    "experiments/runners/openai_agents_multitask_replay_gate_v14.py",
    "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
    "experiments/runners/run_openai_agents_api_experiment.py",
]


def main() -> None:
    if OUT.exists():
        raise SystemExit("freeze already exists; never rewrite it")
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if gate.get("source_contract_gate_pass") is not True or any(registry.all_problems().values()):
        raise SystemExit("source/contract gate not clean")
    source_hashes = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in SOURCES
    }
    freeze = {
        "schema": "openai_agents_v15_acquisition_freeze",
        "status": "FROZEN",
        "date_local": "2026-10-06",
        "purpose": "fresh-ID real SDK payload acquisition for exact-source replay after sandbox connection failure; controlled repeated-source diagnostic only",
        "batch_id_template": "openai-repo-diagnostic-v15-{task_id}-acquisition-02",
        "previous_failed_batch_id_template": "openai-repo-diagnostic-v15-{task_id}-acquisition-01",
        "task_ids": list(registry.task_ids()),
        "per_task": {t: {"instance_id": registry.task(t)["instance_id"], "base_commit": registry.task(t)["base_commit"], "fingerprint": registry.fingerprint(t)} for t in registry.task_ids()},
        "methods": ["none"],
        "repeats": 1,
        "max_api_requests_per_task": 36,
        "max_turns": 10,
        "max_format_repair_calls_per_sample": 1,
        "max_transport_retries_per_model_call": 2,
        "max_summary_calls_per_sample": 0,
        "worst_case_model_attempts_per_task": 33,
        "model": "deepseek-v4-flash",
        "endpoint": "https://api.deepseek.com",
        "max_output_tokens_per_request": 1024,
        "strict_primary": "RESULT one line, cause= and fix=, <=160 chars, frozen per-task full regex and required terms",
        "all_requests_and_tokens_counted": True,
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "source_sha256": source_hashes,
        "admission_after_acquisition": "independent audit complete, real-payload seven-boundary replay all invariants pass, safe candidates >=1 and byte projection >=3%; baseline strict pass required before any three-arm pilot",
        "old_batches_unchanged": True,
    }
    OUT.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"freeze": str(OUT), "tasks": len(freeze["task_ids"]), "sources": len(source_hashes), "max_requests_per_task": 36}))


if __name__ == "__main__":
    main()
