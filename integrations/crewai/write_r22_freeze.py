"""Write the r22 cumulative-budget development freeze exactly once."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json"
NEW = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R22_ORDERFREE_3ARM_01.json"
EXTRA = (
    "experiments/commands/run_crewai_r22_orderfree_3arm.py",
    "experiments/audits/audit_crewai_r22_orderfree_3arm.py",
    "integrations/crewai/PILOT_PROTOCOL_R22_CUMULATIVE_20261007.md",
    "integrations/crewai/write_r22_freeze.py",
    "tests/test_crewai_r22_cumulative_budget.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if NEW.exists():
        raise SystemExit(f"refusing to overwrite frozen file: {NEW}")
    old = json.loads(OLD.read_text(encoding="utf-8"))
    old_pins = dict(old["source_sha256"])
    drift = [name for name, digest in old_pins.items() if sha(ROOT / name) != digest]
    if drift:
        raise SystemExit(f"r20 pinned source drift: {drift}")
    new = dict(old)
    new.update({
        "batch": "crewai-r22-orderfree-3arm-cumulative-01",
        "protocol": "crewai-two-role-r22-orderfree-cumulative",
        "purpose_note": (
            "development rerun after r20 stopped at 7/36 and r21 CLI plan fell through to mock; "
            "same tasks and contract, durable request slots and CLI plan forwarding only; "
            "not held-out confirmation"
        ),
        "r20_partial_sha256": sha(ROOT / "runs/stage5-crewai/crewai-r20-orderfree-3arm-01/results.jsonl"),
        "r20_partial_requests": 56,
        "r21_execution": "mock-only CLI forwarding failure; zero paid requests",
        "source_sha256": {**old_pins, **{name: sha(ROOT / name) for name in EXTRA}},
        "written_utc": "2026-10-07",
        "revision": 1,
        "revision_history": [],
        "self_hash_of": "every field except freeze_sha256, key-sorted, indent=2, utf-8",
    })
    new.pop("draft_sha256", None)
    new.pop("freeze_sha256", None)
    canonical = json.dumps(new, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    new["freeze_sha256"] = hashlib.sha256(canonical).hexdigest()
    NEW.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {NEW}; sha256={sha(NEW)}")


if __name__ == "__main__":
    main()
