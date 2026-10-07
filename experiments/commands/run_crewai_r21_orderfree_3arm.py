"""Rerun the r20 development protocol under a durable cumulative request cap.

The interrupted r20 directory is never reused. Every request slot is written and
fsynced before the provider call. Resume is allowed only at a clean row boundary:
the durable slot count must exactly match the completed rows' request ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from experiments.commands import run_crewai_r20_orderfree_3arm as previous
from experiments.runners import run_crewai_experiment as base


ROOT = Path(__file__).resolve().parents[2]
BATCH = "crewai-r21-orderfree-3arm-cumulative-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R21_ORDERFREE_3ARM_01.json"
SLOT_FILE = "request-slots.jsonl"
OriginalBudget = base.RequestBudget


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior_slots(output: Path, freeze: Path) -> int:
    """Refuse any ambiguous partial request or mismatched batch before resume."""
    manifest_path = output / "manifest.json"
    rows_path = output / "results.jsonl"
    slots_path = output / SLOT_FILE
    if not all(p.is_file() for p in (manifest_path, rows_path, slots_path)):
        raise ValueError("resume lacks manifest, rows, or durable request slots")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("freeze_sha256") != sha(freeze) or
            manifest.get("mode") != "api" or
            manifest.get("max_api_requests") != previous.MAX_API_REQUESTS):
        raise ValueError("resume manifest differs from the frozen API batch")
    raw_rows = rows_path.read_bytes()
    raw_slots = slots_path.read_bytes()
    if not raw_rows.endswith(b"\n") or not raw_slots.endswith(b"\n"):
        raise ValueError("resume has an incomplete trailing row or request slot")
    rows = [json.loads(x) for x in raw_rows.splitlines() if x]
    slots = [json.loads(x) for x in raw_slots.splitlines() if x]
    keys = [(r["task_id"], r["repeat"], r["method"]) for r in rows]
    if len(keys) != len(set(keys)) or len(rows) >= 36:
        raise ValueError("resume grid is duplicate or already complete")
    numbers = [s.get("slot") for s in slots]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("request slots are not contiguous")
    recorded = sum(int(r["api_request_attempts"]) for r in rows)
    if recorded != len(slots) or recorded > previous.MAX_API_REQUESTS:
        raise ValueError("partial or unrecorded request: refuse resume")
    return recorded


def budget_class(output: Path, used: int):
    slots_path = output / SLOT_FILE

    class DurableBudget(OriginalBudget):
        def __init__(self, limit: int, *, used: int = used) -> None:
            if limit != previous.MAX_API_REQUESTS:
                raise ValueError("request cap differs from freeze")
            super().__init__(limit, used=used)

        def consume(self) -> None:
            super().consume()
            with slots_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"slot": self.used}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    return DurableBudget


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--freeze", default=str(FREEZE))
    parser.add_argument("--out", default=str(ROOT / "runs/stage5-crewai"))
    parser.add_argument("--experiment-id", default=BATCH)
    parser.add_argument("--resume", action="store_true")
    args, _ = parser.parse_known_args(argv)
    if args.experiment_id != BATCH:
        raise SystemExit("r21 requires its frozen batch ID")
    if Path(args.freeze).resolve() != FREEZE.resolve():
        raise SystemExit("r21 requires its own freeze")
    if (args.mode == "api" and Path(args.out).resolve() !=
            (ROOT / "runs/stage5-crewai").resolve()):
        raise SystemExit("API output must use the frozen runs directory")
    previous.BATCH = BATCH
    previous.FREEZE = FREEZE
    previous.check_freeze(FREEZE)
    output = Path(args.out) / BATCH
    if args.resume and args.mode != "api":
        raise SystemExit("only the charged API batch may resume")
    if output.exists() and not args.resume:
        raise SystemExit("write-once batch already exists")
    used = prior_slots(output, FREEZE) if args.resume else 0
    base.RequestBudget = budget_class(output, used)
    forwarded = list(argv or [])
    if "--freeze" not in forwarded:
        forwarded.extend(("--freeze", str(FREEZE)))
    if "--experiment-id" not in forwarded:
        forwarded.extend(("--experiment-id", BATCH))
    previous.main(forwarded)


if __name__ == "__main__":
    main()
