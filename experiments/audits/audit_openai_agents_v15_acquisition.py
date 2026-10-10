"""Independent v15 acquisition audit; no saving or quality claim."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from experiments.audits.audit_openai_agents_strict_v15 import audit_rows, verify_sources
from experiments.runners import openai_agents_multitask_replay_gate_v14 as replay
from experiments.runners import openai_agents_v15_registry as registry


FREEZE = registry.REPO / "integrations/openai_agents/V15_ACQUISITION_FREEZE_20261006.json"
RUNS = registry.REPO / "runs/stage5-openai-agents-api"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(task_id: str) -> dict:
    root = RUNS / f"openai-repo-diagnostic-v15-{task_id}-acquisition-01"
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    problems = verify_sources(registry.REPO, freeze["source_sha256"])
    if task_id not in freeze["task_ids"]:
        problems.append("task_not_frozen")
    problems.extend(registry.verify(task_id))
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError) as error:
        return {"complete": False, "errors": problems + [f"missing_or_invalid_artifact:{type(error).__name__}"], "citable_as_saving": False, "citable_as_quality_equivalence": False}
    if len(rows) != 1 or rows[0].get("scenario") != task_id or rows[0].get("method") != "none":
        problems.append("not_one_baseline_sample")
    if manifest.get("freeze_sha256") != _sha(FREEZE):
        problems.append("freeze_manifest_hash")
    if manifest.get("citable_as_saving") is not False or manifest.get("citable_as_quality_equivalence") is not False:
        problems.append("citation_flag")
    if any(int(row.get("api_request_attempts", 10**9)) > freeze["max_api_requests_per_task"] for row in rows):
        problems.append("request_cap")
    problems.extend(audit_rows(rows, evidence_root=root, required_methods={"none"}))
    replay.registry = registry
    try:
        replay_report = replay.analyse(task_id, root)
    except Exception as error:
        replay_report = {"qualified": False, "problems": [f"replay_crash:{type(error).__name__}:{error}"]}
    if replay_report.get("problems"):
        problems.extend(replay_report["problems"])
    return {
        "schema": "openai_agents_v15_acquisition_audit",
        "task": task_id,
        "batch": root.name,
        "complete": not problems,
        "errors": problems,
        "paid_request_attempts": sum(int(row.get("api_request_attempts", 0)) for row in rows),
        "replay_qualified_for_pilot_gate": bool(replay_report.get("qualified")) and not problems,
        "replay_projection_bytes_not_provider_tokens": replay_report.get("projection", {}).get("byte_saving_rate"),
        "replay_safe_candidates": replay_report.get("safe_candidates", {}).get("guard_filtered_safe_candidates"),
        "strict_baseline_success_recorded_diagnostic_only": bool(rows[0].get("success")) if rows else False,
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
    }


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: -m experiments.audits.audit_openai_agents_v15_acquisition TASK_ID")
    report = audit(args[0])
    path = RUNS / report["batch"] / "v15_acquisition_audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task": args[0], "complete": report["complete"], "errors": report["errors"], "requests": report.get("paid_request_attempts"), "replay_qualified": report.get("replay_qualified_for_pilot_gate")}, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
