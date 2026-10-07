"""Write the r24 nine-sample development freeze once, after zero-API source gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json"
NEW = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R24_COVERAGE_PILOT_01.json"
EXTRA = (
    "experiments/runners/crewai_coverage_guard_v24.py",
    "experiments/commands/run_crewai_r24_coverage_pilot.py",
    "experiments/audits/audit_crewai_r24_coverage_pilot.py",
    "integrations/crewai/PILOT_PROTOCOL_R24_COVERAGE_20261007.md",
    "integrations/crewai/write_r24_freeze.py",
    "tests/test_crewai_coverage_guard_v24.py",
    "tests/test_crewai_r24_audit_controls.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if NEW.exists():
        raise SystemExit("r24 freeze is write-once")
    old = json.loads(OLD.read_text(encoding="utf-8"))
    pins = dict(old["source_sha256"])
    drift = [name for name, expected in pins.items() if sha(ROOT / name) != expected]
    if drift:
        raise SystemExit(f"r20 base source drift: {drift}")
    freeze = {
        "batch": "crewai-r24-coverage-guard-dev-01",
        "protocol": "crewai-two-role-r24-coverage-dev",
        "purpose": "development_pilot_not_confirmation",
        "tasks": ["ledger_lock_attestation"],
        "methods": ["none", "pruner_v1", "native_summary"],
        "repeats": 3,
        "samples": 9,
        "model": "deepseek-v4-flash",
        "max_output_tokens": 512,
        "max_summary_tokens": 1024,
        "max_summary_calls": 4,
        "max_api_requests": 120,
        "guard_scope": "all_arms_order_free_coverage",
        "guard_rejections_per_first_role": 6,
        "quality_gate": (
            "first-role source coverage 3/3 on every arm, plugin strict and semantic "
            "success no lower than baseline, zero guard exhaustion"
        ),
        "cost_gate": (
            "paired complete provider tokens positive in at least 2/3 units and positive "
            "in sum, all remedy and summary calls included"
        ),
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
