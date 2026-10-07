"""Write the r26 confirmation freeze once after zero-API gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R25_COVERAGE_PILOT_01.json"
NEW = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R26_COVERAGE_CONFIRM_01.json"
EXTRA = (
    "tasks/stage5_autogen/natural_tasks_r26_confirmation.json",
    "integrations/crewai/build_r26_confirmation_tasks.py",
    "experiments/commands/run_crewai_r26_coverage_confirm.py",
    "experiments/audits/audit_crewai_r26_coverage_confirm.py",
    "integrations/crewai/PROTOCOL_R26_COVERAGE_CONFIRM_20261007.md",
    "integrations/crewai/write_r26_freeze.py",
    "tests/test_crewai_r26_confirmation_controls.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if NEW.exists():
        raise SystemExit("r26 freeze is write-once")
    old = json.loads(OLD.read_text(encoding="utf-8"))
    pins = dict(old["source_sha256"])
    drift = [name for name, expected in pins.items() if sha(ROOT / name) != expected]
    if drift:
        raise SystemExit(f"r25 base source drift: {drift}")
    freeze = {
        "batch": "crewai-r26-coverage-guard-confirm-01",
        "protocol": "crewai-two-role-r26-coverage-confirm",
        "purpose": "prospective_synthetic_multitask_confirmation",
        "tasks": ["certificate_rotation_release", "inventory_quarantine_hold",
                  "backup_restore_closeout"],
        "methods": ["none", "pruner_v1", "native_summary"],
        "repeats": 3,
        "samples": 27,
        "model": "deepseek-v4-flash",
        "max_output_tokens": 512,
        "max_summary_tokens": 1024,
        "max_summary_calls": 4,
        "max_api_requests": 360,
        "guard_scope": "all_arms_order_free_coverage",
        "guard_rejections_per_first_role": 6,
        "quality_gate": "all-arm source coverage 9/9, baseline strict >=2/3 per task, plugin strict and semantic >= baseline per task, zero guard exhaustion",
        "cost_gate": "complete provider token deltas >=2/3 positive and positive sum per task; >=7/9 positive and positive sum overall",
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
