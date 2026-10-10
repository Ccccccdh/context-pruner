"""New-ID, bounded, non-citable v15 public payload acquisition.

One baseline arm and one repeat per selected task. The strict format wrapper
is installed identically to the future three-arm pilot, and all its requests
are charged. No saving or quality-equivalence claim may cite this batch.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from experiments.runners import openai_agents_multitask_chain_v14 as chain
from experiments.runners import openai_agents_v15_registry as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_multitask_acquisition_v14 as old_acq
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_strict_wiring_v15 import strict_wiring


FREEZE = registry.REPO / "integrations/openai_agents/V15_ACQUISITION_FREEZE_02_20261006.json"
MAX_REQUESTS = 36
METHODS = ("none",)
DISCLOSURE = (
    "Controlled six-read repeated-source diagnostic workload. Sends a public Verified issue "
    "statement and three read-only ranges of exact public Django baseline source at the pinned "
    "commit; no reference patch, evaluation test patch, expected answer or local secret."
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_freeze() -> dict:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if freeze.get("status") != "FROZEN" or freeze.get("max_api_requests_per_task") != MAX_REQUESTS:
        raise RuntimeError("v15 acquisition freeze missing or cap differs")
    for relative, expected in freeze["source_sha256"].items():
        path = registry.REPO / relative
        if not path.is_file() or _hash(path) != expected:
            raise RuntimeError(f"frozen source drift: {relative}")
    if any(registry.all_problems().values()):
        raise RuntimeError(f"v15 source registration invalid: {registry.all_problems()}")
    return freeze


def forced_args(args: list[str], task_id: str) -> list[str]:
    cleaned = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item in ("--task", "--methods", "--repeats", "--scenarios", "--experiment-id", "--max-api-requests", "--max-turns", "--max-output-tokens", "--provider-soft", "--provider-target", "--provider-hard", "--max-summary-calls", "--max-api-retries"):
            skip = True
            continue
        cleaned.append(item)
    if "--confirm-send-synthetic-data" in cleaned:
        raise SystemExit("use --confirm-send-public-source for exact public source")
    cleaned = ["--confirm-send-synthetic-data" if x == "--confirm-send-public-source" else x for x in cleaned]
    return cleaned + [
        "--methods", "none", "--repeats", "1", "--scenarios", task_id,
        "--experiment-id", f"openai-repo-diagnostic-v15-{task_id}-acquisition-02",
        "--max-api-requests", str(MAX_REQUESTS), "--max-turns", "10",
        "--max-output-tokens", "1024", "--provider-soft", "2000",
        "--provider-target", "1500", "--provider-hard", "6000",
        "--max-summary-calls", "0", "--max-api-retries", "2",
    ]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--task" not in args or args.index("--task") + 1 >= len(args):
        raise SystemExit("--task <registered task_id> is required")
    task_id = args[args.index("--task") + 1]
    if task_id not in registry.tasks():
        raise SystemExit(f"unregistered task: {task_id}")
    freeze = _verify_freeze()
    if task_id not in freeze["task_ids"]:
        raise RuntimeError("task absent from acquisition freeze")
    cleaned = forced_args(args, task_id)
    parsed = base.build_parser().parse_args(cleaned)
    output_root = Path(parsed.out) / parsed.experiment_id
    budget, _ = base.resolve_budget(parsed)
    original = (base.SCENARIOS, base.build_case, base.SYNTHETIC_DISCLOSURE, chain.registry, old_acq.registry)
    base.SCENARIOS = (task_id,)
    chain.registry = registry
    old_acq.registry = registry
    base.build_case = lambda scenario, repeat: old_acq.build_case(task_id, repeat)
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    try:
        with chain.chain_wiring():
            with v8.evidence_wiring(output_root, int(budget.hard_limit_tokens) * 4, recorder_factory=chain.MultitaskRecorder):
                with strict_wiring(output_root):
                    status = base.main(cleaned)
    finally:
        base.SCENARIOS, base.build_case, base.SYNTHETIC_DISCLOSURE, chain.registry, old_acq.registry = original
    manifest_path = output_root / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({
            "kind": "openai_agents_v15_public_payload_acquisition",
            "purpose": "fresh-ID controlled six-read repeated-source real payload acquisition after the -01 sandbox connection failure; no old-batch resume",
            "previous_failed_batch": f"openai-repo-diagnostic-v15-{task_id}-acquisition-01",
            "citable_as_saving": False,
            "citable_as_quality_equivalence": False,
            "natural_agent_behavior_claim": False,
            "freeze_path": str(FREEZE.relative_to(registry.REPO)).replace("\\", "/"),
            "freeze_sha256": _hash(FREEZE),
            "v15_registry": registry.manifest_block(),
            "task_registration_problems": registry.all_problems(),
            "request_budget": {"max_api_requests": MAX_REQUESTS, "max_turns": 10, "max_repair_calls_per_sample": 1, "max_transport_retries_per_model_call": 2, "worst_case_attempts": 33},
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
