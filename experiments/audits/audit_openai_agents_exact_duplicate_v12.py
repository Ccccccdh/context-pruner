"""Independent, fail-closed audit of the v12 three-arm development pilot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit(batch: Path, freeze_path: Path) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    errors = []
    if batch.name != freeze["batch"]:
        errors.append("batch_id_mismatch")
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("freeze_sha256") != hashlib.sha256(freeze_path.read_bytes()).hexdigest():
        errors.append("freeze_hash_mismatch")
    root = Path(__file__).resolve().parents[2]
    for name, digest in freeze["source_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            errors.append(f"source_drift:{name}")
    rows = read_jsonl(batch / "samples.jsonl")
    expected = {("django_long_investigation", 0, method) for method in freeze["matrix"]["methods"]}
    actual = [(row.get("scenario"), row.get("repeat"), row.get("method")) for row in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        errors.append("missing_or_duplicate_sample")
    attempts = sum(int(row.get("api_request_attempts", 0)) for row in rows)
    if attempts > freeze["limits"]["max_api_requests"]:
        errors.append("request_cap_exceeded")
    if any(int(row.get("all_arm_total_tokens", -1)) < 0 for row in rows):
        errors.append("missing_complete_token_count")
    for row in rows:
        evidence = batch / str(row.get("input_evidence_file", ""))
        if not evidence.is_file():
            errors.append(f"missing_evidence:{row.get('method')}")
            continue
        records = read_jsonl(evidence)
        if len([r for r in records if r.get("stage") == "model_input"]) != int(row.get("model_calls", -1)):
            errors.append(f"boundary_count:{row.get('method')}")
    keyed = {row.get("method"): row for row in rows}
    baseline = keyed.get("none", {})
    plugin = keyed.get("pruner_v1", {})
    baseline_total = int(baseline.get("all_arm_total_tokens", 0))
    plugin_total = int(plugin.get("all_arm_total_tokens", 0))
    paired_saving = (baseline_total - plugin_total) / baseline_total if baseline_total else None
    quality_non_inferior = bool(plugin.get("success")) >= bool(baseline.get("success"))
    actual_reduction = int(plugin.get("selective_retention_saved_bytes_total", 0)) > 0
    if not actual_reduction:
        errors.append("no_actual_plugin_reduction")
    complete = not errors
    return {"schema": "openai_agents_v12_independent_audit", "complete": complete,
            "errors": errors, "rows": len(rows), "api_request_attempts": attempts,
            "baseline_success": bool(baseline.get("success")),
            "plugin_success": bool(plugin.get("success")),
            "quality_non_inferior": quality_non_inferior,
            "baseline_complete_total_tokens": baseline_total,
            "plugin_complete_total_tokens": plugin_total,
            "actual_plugin_reduction": actual_reduction,
            "paired_saving": paired_saving,
            "development_candidate": complete and quality_non_inferior
            and paired_saving is not None and paired_saving >= 0.03}


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: audit_openai_agents_exact_duplicate_v12 BATCH FREEZE")
    batch, freeze = map(Path, sys.argv[1:])
    report = audit(batch, freeze)
    (batch / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
