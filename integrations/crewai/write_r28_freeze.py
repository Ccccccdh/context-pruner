"""Write the r28 fresh-task freeze once after all zero-API gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R27_VARIABLE_DEV_01.json"
NEW = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R28_VARIABLE_CONFIRM_01.json"
EXTRA = (
    "tasks/stage5_autogen/natural_tasks_r28_fresh.json",
    "integrations/crewai/build_r28_fresh_tasks.py",
    "integrations/crewai/r28_task_contract_gate.py",
    "experiments/commands/run_crewai_r28_variable_confirm.py",
    "experiments/audits/audit_crewai_r28_variable_confirm.py",
    "integrations/crewai/PROTOCOL_R28_VARIABLE_CONFIRM_20261007.md",
    "integrations/crewai/write_r28_freeze.py",
    "tests/test_crewai_r28_variable_controls.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if NEW.exists():
        raise SystemExit("r28 freeze is write-once")
    old = json.loads(OLD.read_text(encoding="utf-8"))
    pins = dict(old["source_sha256"])
    drift = [name for name, expected in pins.items() if sha(ROOT / name) != expected]
    if drift:
        raise SystemExit(f"r27 base source drift: {drift}")
    freeze = {
        "batch": "crewai-r28-variable-source-confirm-01",
        "protocol": "crewai-two-role-r28-variable-confirm",
        "purpose": "prospective_variable_source_synthetic_confirmation",
        "tasks": ["invoice_settlement_finalize", "dns_cutover_wait"],
        "methods": ["none", "pruner_v1", "native_summary"],
        "repeats": 3,
        "samples": 18,
        "model": "deepseek-v4-flash",
        "max_output_tokens": 512,
        "max_summary_tokens": 1024,
        "max_summary_calls": 4,
        "max_api_requests": 270,
        "guard_scope": "all_arms_order_free_coverage",
        "guard_rejections_per_first_role": 6,
        "quality_gate": "each arm source coverage 3/3 per task, baseline strict >=2/3 per task, plugin strict and semantic >= baseline per task, zero restore failures and guard exhaustion",
        "cost_gate": "complete provider token deltas >=2/3 positive and positive sum per task; >=5/6 positive and positive sum overall",
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "source_sha256": {**pins, **{name: sha(ROOT / name) for name in EXTRA}},
        "written_utc": "2026-10-07",
        "self_hash_of": "every field except freeze_sha256, key-sorted, indent=2, utf-8",
    }
    canonical = json.dumps(freeze, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    freeze["freeze_sha256"] = hashlib.sha256(canonical).hexdigest()
    NEW.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {NEW}; sha256={sha(NEW)}")


if __name__ == "__main__":
    main()
