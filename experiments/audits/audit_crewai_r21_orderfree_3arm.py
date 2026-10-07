"""Independent r21 cumulative-slot check plus the frozen r20 quality/cost audit."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.audits.audit_crewai_r20_orderfree_3arm import audit as base_audit

ROOT = Path(__file__).resolve().parents[2]
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R21_ORDERFREE_3ARM_01.json"
BATCH = ROOT / "runs/stage5-crewai/crewai-r21-orderfree-3arm-cumulative-01"


def audit(directory: Path = BATCH, freeze_path: Path = FREEZE) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    prior = base_audit(directory, freeze_path, out_path=None)
    errors = list(prior["errors"])
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(
        encoding="utf-8").splitlines() if line]
    slot_path = directory / "request-slots.jsonl"
    slots = ([json.loads(line) for line in slot_path.read_text(encoding="utf-8").splitlines()
              if line] if slot_path.is_file() else [])
    numbers = [item.get("slot") for item in slots]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append("durable request slots are missing, duplicated, or noncontiguous")
    recorded = sum(int(row.get("api_request_attempts", 0)) for row in rows)
    if recorded != len(slots):
        errors.append(f"durable slots {len(slots)} != recorded requests {recorded}")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if len(slots) > int(freeze["max_api_requests"]):
        errors.append("durable request slots exceed frozen cap")
    return {
        **prior,
        "complete": not errors,
        "can_be_quoted_as_saving": bool(prior.get("can_be_quoted_as_saving")) and not errors,
        "errors": errors,
        "durable_request_slots": len(slots),
        "recorded_request_attempts": recorded,
    }


if __name__ == "__main__":
    result = audit()
    (BATCH / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "errors": result["errors"]},
                     ensure_ascii=False))
    raise SystemExit(0 if result["complete"] else 1)
