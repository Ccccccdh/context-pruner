"""Independent r24 coverage pilot audit; never imports a runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH = "crewai-r24-coverage-guard-dev-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R24_COVERAGE_PILOT_01.json"
DEFAULT = ROOT / "runs/stage5-crewai" / BATCH
ARMS = ("none", "pruner_v1", "native_summary")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_ledger_errors(row: dict, *, mode: str = "api") -> list[str]:
    errors = []
    key = f"{row.get('task_id')}/{row.get('repeat')}/{row.get('method')}"
    agent = row.get("full_attempt_capture") or []
    summary = row.get("native_summary_capture") or []
    n_agent = int(row.get("agent_request_attempts", -1))
    n_summary = int(row.get("auxiliary_request_attempts", -1))
    n_total = int(row.get("api_request_attempts", -1))
    if (len(agent), len(summary)) != (n_agent, n_summary):
        errors.append(f"{key}: per-call capture incomplete")
    if n_agent + n_summary != n_total:
        errors.append(f"{key}: request components do not sum")
    captured_tokens = sum(int(x.get("input_tokens", 0)) + int(x.get("output_tokens", 0))
                          for x in [*agent, *summary])
    if mode == "api" and captured_tokens != int(row.get("all_arm_total_tokens", -1)):
        errors.append(f"{key}: complete token ledger differs from capture")
    if row.get("capture_complete") is not True:
        errors.append(f"{key}: runner marks capture incomplete")
    if row.get("guard_enabled") is not True or row.get("guard_installed") is not True:
        errors.append(f"{key}: all-arm guard missing")
    if row.get("guard_scope") != "all_arms_order_free_coverage":
        errors.append(f"{key}: guard scope differs")
    return errors


def quality_and_cost(rows: list[dict], task: dict) -> dict:
    by = {(int(r["repeat"]), r["method"]): r for r in rows}
    required = {tool["name"] for tool in task["tools"]}
    coverage = {arm: 0 for arm in ARMS}
    strict = {arm: 0 for arm in ARMS}
    semantic = {arm: 0 for arm in ARMS}
    deltas = []
    for repeat in range(3):
        for arm in ARMS:
            row = by[(repeat, arm)]
            coverage[arm] += required.issubset(set(row.get("first_role_trace") or []))
            strict[arm] += row.get("strict_success") is True
            semantic[arm] += row.get("semantic_success") is True
        baseline = int(by[(repeat, "none")]["all_arm_total_tokens"])
        plugin = int(by[(repeat, "pruner_v1")]["all_arm_total_tokens"])
        deltas.append(baseline - plugin)
    quality_pass = (all(value == 3 for value in coverage.values())
                    and strict["pruner_v1"] >= strict["none"]
                    and semantic["pruner_v1"] >= semantic["none"]
                    and all(not r.get("guard_exhausted") for r in rows))
    cost_pass = sum(delta > 0 for delta in deltas) >= 2 and sum(deltas) > 0
    return {"coverage": coverage, "strict": strict, "semantic": semantic,
            "paired_delta_tokens": deltas, "quality_pass": quality_pass,
            "cost_pass": cost_pass}


def audit(directory: Path = DEFAULT, freeze_path: Path = FREEZE) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(
        encoding="utf-8").splitlines() if line]
    slots_path = directory / "request-slots.jsonl"
    slots = ([json.loads(line) for line in slots_path.read_text(encoding="utf-8").splitlines()
              if line] if slots_path.is_file() else [])
    errors = []
    if manifest.get("freeze_sha256") != sha(freeze_path):
        errors.append("manifest freeze bytes differ")
    if manifest.get("batch") != freeze["batch"] or manifest.get("guard_installed_arms") != list(ARMS):
        errors.append("batch or all-arm guard manifest differs")
    for name, expected in freeze["source_sha256"].items():
        if sha(ROOT / name) != expected:
            errors.append(f"frozen source drift: {name}")
    keys = [(r.get("task_id"), r.get("repeat"), r.get("method")) for r in rows]
    expected_keys = {(freeze["tasks"][0], repeat, arm) for repeat in range(3) for arm in ARMS}
    if len(rows) != 9 or len(set(keys)) != 9 or set(keys) != expected_keys:
        errors.append("nine-cell balanced grid incomplete")
    for row in rows:
        errors.extend(row_ledger_errors(row, mode=manifest.get("mode", "")))
    if [s.get("slot") for s in slots] != list(range(1, len(slots) + 1)):
        errors.append("durable slots noncontiguous")
    if len(slots) != sum(int(row.get("api_request_attempts", 0)) for row in rows):
        errors.append("durable slots differ from recorded attempts")
    if len(slots) > int(freeze["max_api_requests"]):
        errors.append("request cap exceeded")
    task_file = ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json"
    task = next(t for t in json.loads(task_file.read_text(encoding="utf-8"))
                if t["task_id"] == freeze["tasks"][0])
    outcomes = quality_and_cost(rows, task) if not errors else None
    return {"batch": freeze["batch"], "complete": not errors,
            "errors": errors, "rows": len(rows), "durable_request_slots": len(slots),
            "outcomes": outcomes,
            "acceptance_met": bool(not errors and manifest.get("mode") == "api"
                                   and outcomes["quality_pass"] and outcomes["cost_pass"]),
            "can_be_quoted_as_saving": False,
            "reason": "development task used to design the guard; confirmation requires new tasks"}


if __name__ == "__main__":
    result = audit()
    (DEFAULT / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "errors": result["errors"],
                      "acceptance_met": result["acceptance_met"]}, ensure_ascii=False))
    raise SystemExit(0 if result["complete"] else 1)
