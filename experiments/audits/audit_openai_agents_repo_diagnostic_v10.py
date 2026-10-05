"""Independent, zero-model audit for the v10 tool-call-recency batch.

All evidence checks are v9's (grid, quality, request ledger, tool-group boundaries, literal
retention per boundary against the baseline arm, span partitions, guard ledgers, paired
means and the acceptance verdict) - imported, not restated, so the two versions cannot drift
apart in what they verify.  This module adds what is specific to v10:

* the mechanism identity and the recency **unit** in the manifest are checked;
* the replay gate's fixture must have reproduced the recorded payload structure, and the
  recorded-structure fingerprint in the freeze must still match the paid v9 batch;
* the batch may only exist when the replay projection was positive, and that projection is
  re-read from the artifact rather than trusted from the row.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.audits import audit_openai_agents_repo_diagnostic_v9 as base_audit
from experiments.runners import openai_agents_long_baseline_boundary_v10 as policy
from experiments.runners import openai_agents_real_payload_replay_v10 as replay

ROOT = Path(__file__).resolve().parents[2]
MECHANISM = "tool_call_recency_v10"
PROJECTION = ROOT / "integrations/openai_agents/V10_REPLAY_PROJECTION.json"


def _freeze_for_v9_audit(freeze: dict) -> dict:
    """Present the v10 freeze in the shape the v9 checks expect.

    Only the recency-unit-specific keys differ; the v9 audit consumes
    ``recent_turns_kept_verbatim`` to compare against the manifest's
    ``mechanism.recent_turns_kept_verbatim``, which v10's manifest also carries (the v9
    field is inherited unchanged), so the value is passed through.
    """
    adjusted = dict(freeze)
    adjusted.setdefault(
        "recent_turns_kept_verbatim", int(freeze.get("recent_turns_kept_verbatim", 1))
    )
    return adjusted


def audit(batch: Path, freeze_path: Path, batch_name: str | None = None) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    result = base_audit.audit(batch, freeze_path, batch_name)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    # The v9 checks look for v9's mechanism name and the v10 rows carry extra columns, so
    # those two expectations are replaced by this version's own (rather than carrying a
    # spurious issue forward).
    issues = [
        issue
        for issue in result["issues"]
        if issue != "manifest does not declare the v9 mechanism"
        and "evidence schema mismatch" not in issue
    ]

    # -- v10 identity ------------------------------------------------------
    if manifest.get("mechanism", {}).get("name") != MECHANISM:
        issues.append("manifest does not declare the v10 tool-call-recency mechanism")
    if not str(manifest.get("mechanism", {}).get("recency_unit", "")).startswith("tool call"):
        issues.append("manifest does not declare the tool-call recency unit")
    if int(manifest.get("mechanism", {}).get("recent_tool_calls_kept_verbatim", -1)) != int(
        freeze["recent_tool_calls_kept_verbatim"]
    ):
        issues.append("manifest tool-call recency constant mismatch")

    # -- the replay fixture and the recorded structure ---------------------
    fixture = freeze.get("replay_fixture", {})
    if not fixture.get("ok"):
        issues.append("the freeze does not record a verified replay fixture")
    recorded = replay.recorded_structure_fingerprint()
    if fixture.get("recorded_structure_fingerprint") != recorded:
        issues.append("the recorded payload-structure fingerprint changed since the freeze")
    if fixture.get("tool_items_contiguous") is not True:
        issues.append("the recorded payload structure is not recorded as contiguous")
    if fixture.get("recorded_turn_count_by_boundary") != [0, 1, 1, 1, 1, 1, 1]:
        issues.append("the recorded turn counts are not the ones the replay asserts")

    # -- the paid decision -------------------------------------------------
    projection = freeze.get("replay_projection", {})
    artifact = (
        json.loads(PROJECTION.read_text(encoding="utf-8")) if PROJECTION.is_file() else {}
    )
    if projection.get("verdict") != "positive":
        issues.append("the freeze does not record a positive replay projection")
    if artifact.get("verdict") != "positive":
        issues.append("the replay projection artifact is not positive")
    if artifact and artifact.get("fixture", {}).get("ok") is not True:
        issues.append("the replay projection artifact's fixture was not verified")
    if artifact.get("byte_saving_rate", 0) <= 0:
        issues.append("the replay projection recorded no positive projected saving")

    # -- v10 evidence schema ----------------------------------------------
    rows_all = base_audit._read_jsonl(batch / "samples.jsonl")
    for row in rows_all:
        relative = row.get("input_evidence_file")
        if not relative:
            continue
        path = batch / str(relative)
        if not path.is_file():
            continue
        for record in base_audit._read_jsonl(path):
            if record.get("schema") != "openai_tool_call_recency_v10":
                issues.append(
                    f"{row['scenario']}-{row['repeat']}-{row['method']}: v10 evidence "
                    "schema mismatch"
                )
                break

    # -- v10 row columns ---------------------------------------------------
    rows = rows_all
    for row in rows:
        if str(row.get("method")) != "pruner_v1":
            continue
        if row.get("tool_call_recency_unit") != "tool call":
            issues.append(
                f"{row['scenario']}-{row['repeat']}: row does not record the recency unit"
            )
        if int(row.get("tool_call_recency_recent_calls_kept", -1)) != int(
            freeze["recent_tool_calls_kept_verbatim"]
        ):
            issues.append(
                f"{row['scenario']}-{row['repeat']}: row recency constant mismatch"
            )
        if int(row.get("tool_call_recency_tool_calls", 0)) <= 0:
            issues.append(
                f"{row['scenario']}-{row['repeat']}: row records no tool calls"
            )
    result = {
        **result,
        "mechanism": MECHANISM,
        "recency_unit": "tool call",
        "replay_fixture_ok": bool(fixture.get("ok")),
        "replay_projection_verdict": projection.get("verdict"),
        "replay_projected_byte_saving": projection.get("byte_saving_rate"),
        "recorded_structure_fingerprint": recorded,
        "issues": issues,
        "complete": not issues,
        "verdict": result["verdict"] if not issues else "not-yet-valid",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--freeze", required=True, type=Path)
    parser.add_argument("--batch-name", default=None)
    args = parser.parse_args()
    result = audit(args.batch, args.freeze, args.batch_name)
    (args.batch / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
