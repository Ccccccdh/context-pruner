"""CrewAI r20 order-free acquisition: the pinned r19 full-capture path, r20 tasks.

The r19 and r17 commands this builds on are pinned by their freezes, so this file does not
edit them: it rebinds the four module globals the pinned code reads (task file, task ids,
request cap, freeze path) and delegates to ``r19.run``.  The per-attempt capture, the cache
metering, the freeze enforcement and the manifest shape are the pinned code's.

This batch is NOT a saving claim and NOT a quality-equivalence claim; it exists so the r20
order-free task family has its own recorded payload.
"""

from __future__ import annotations

from pathlib import Path
import json

from experiments.commands import run_crewai_r19_acquisition as r19
REUSED_FROM_R19 = {
    "chain": [
        "experiments/commands/run_crewai_r19_acquisition.py",
        "experiments/commands/run_crewai_r17_confirmation_acquisition.py"
    ],
    "source_sha256_at_build": {
        "experiments/commands/run_crewai_r19_acquisition.py": "f754ae1348445f6fc307ee3d11df38840fb3bb9a78a734e3989823c634eb3ad6",
        "experiments/commands/run_crewai_r17_confirmation_acquisition.py": "fed8633a6f29b5b58fd88c9c747937caec3de5dd7e89ce856b0091a2f21b7427"
    },
    "rebound_globals": [
        "TASK_FILE",
        "TASK_IDS",
        "MAX_API_REQUESTS",
        "FREEZE"
    ],
    "values": {
        "TASK_FILE": "tasks/stage5_autogen/natural_tasks_r20_orderfree.json",
        "TASK_IDS": [
            "ledger_lock_attestation",
            "telemetry_export_attestation",
            "replica_resync_attestation",
            "policy_signoff_attestation"
        ],
        "MAX_API_REQUESTS": 30,
        "FREEZE": "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_ACQUISITION_01.json",
        "BATCH": "crewai-r20-orderfree-fullcapture-acquisition-api-01"
    },
    "protocol_note": "the delegated code writes its own protocol string; the batch is identified by the batch id, the recorded r20 task ids and the r20 freeze"
}

ROOT = r19.ROOT
TASK_FILE = ROOT / "tasks/stage5_autogen" / 'natural_tasks_r20_orderfree.json'
TASK_IDS = ('ledger_lock_attestation', 'telemetry_export_attestation', 'replica_resync_attestation', 'policy_signoff_attestation')
FREEZE = ROOT / "integrations/crewai" / 'PRE_RUN_FREEZE_R20_ORDERFREE_ACQUISITION_01.json'
MAX_API_REQUESTS = 30
DEFAULT_EXPERIMENT_ID = 'crewai-r20-orderfree-fullcapture-acquisition-api-01'

# The pinned implementations.
CapturedOpenAICompatCrewAILLM = r19.CapturedOpenAICompatCrewAILLM
sha = r19.sha
PINNED = r19.PINNED


def check_freeze(freeze_path):
    """The r20 freeze enforcer: the same discipline as the pinned one, on the r20 pins.

    The pinned r19 enforcer also verifies the r19 source set, which this batch does not
    share, so the r20 batch verifies the r20 set here instead.  The discipline is identical
    and fail-closed: an unregistered digest refuses the run.
    """
    freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    if freeze.get("purpose") != "payload_acquisition_only":
        raise SystemExit("acquisition purpose mismatch")
    if int(freeze.get("max_api_requests", 0)) != MAX_API_REQUESTS:
        raise SystemExit("request hard cap mismatch against the freeze")
    if tuple(freeze.get("tasks") or ()) != TASK_IDS:
        raise SystemExit("freeze task set mismatch")
    if freeze.get("methods") != ["none"] or int(freeze.get("repeats", 0)) != 1:
        raise SystemExit("freeze arm or repeat mismatch")
    if freeze.get("citable_as_saving") is not False or freeze.get(
        "citable_as_quality_equivalence"
    ) is not False:
        raise SystemExit("acquisition must not be citable")
    for relative, expected in (freeze.get("source_sha256") or {}).items():
        if not expected:
            continue
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"unregistered source drift: {relative}")
    return freeze


from experiments.runners import crewai_semantic_equivalence_v8 as judge

load_tasks = judge.load_tasks


def main(argv=None) -> None:
    r19.TASK_FILE = TASK_FILE
    r19.TASK_IDS = TASK_IDS
    r19.MAX_API_REQUESTS = MAX_API_REQUESTS
    r19.FREEZE = FREEZE
    r19.check_freeze = check_freeze
    r19.run(argv)


run = main

if __name__ == "__main__":
    main()
