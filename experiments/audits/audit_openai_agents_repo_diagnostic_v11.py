"""Independent, zero-model audit for the v11 elision-ratio batch.

All evidence checks are the v9/v10 ones (grid, quality, request ledger, tool-group
boundaries, literal retention per boundary against the baseline arm, span partitions, guard
ledgers, paired means and the acceptance verdict) - imported, not restated - while this
module adds what is specific to the elision-ratio ablation:

* the manifest declares the v11 mechanism, the ladder and the **selected M**, and the rows
  carry that same M (a mismatch would mean the batch did not run the frozen ladder step);
* the manifest records the **freeze file's own SHA256**, and this audit recomputes it, so an
  after-the-fact edit of a frozen file is detectable from the batch alone;
* the ladder projection artifact records all three M (including a zero and a non-zero
  projection) and the selection is the largest positive M.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.audits import audit_openai_agents_repo_diagnostic_v10 as base_audit
from experiments.runners import openai_agents_long_baseline_boundary_v11 as policy

ROOT = Path(__file__).resolve().parents[2]
MECHANISM = "elision_ratio_boundary_v11"
LADDER_PROJECTION = ROOT / "integrations/openai_agents/V11_ELISION_LADDER_PROJECTION.json"


def audit(batch: Path, freeze_path: Path, batch_name: str | None = None) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    # The v10 checks compare the manifest's inherited ``recent_tool_calls_kept_verbatim``
    # field against the freeze; this version states that value in ``selected_M`` and its
    # frozen file must not be edited, so a derived in-memory copy is used for those checks.
    derived = dict(freeze)
    derived["recent_tool_calls_kept_verbatim"] = int(freeze["selected_M"])
    import tempfile

    with tempfile.TemporaryDirectory(prefix="v11-freeze-") as tmp:
        derived_path = Path(tmp) / "derived_freeze.json"
        derived_path.write_text(
            json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        result = base_audit.audit(batch, derived_path, batch_name)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    issues = [
        issue
        for issue in result["issues"]
        if issue != "manifest does not declare the v10 tool-call-recency mechanism"
        and "v10 evidence schema mismatch" not in issue
        and "tool-call recency unit" not in issue
        # The v10 checks compare the inherited ``recent_tool_calls_kept_verbatim`` field; this
        # version states the value as ``selected_M`` and checks it below instead.
        and "manifest tool-call recency constant mismatch" not in issue
        and "row recency constant mismatch" not in issue
        # v10's row-column checks refer to v10's own row shape; this version's columns are
        # the elision-ratio ones, checked below.
        and "row does not record the recency unit" not in issue
        and "row records no tool calls" not in issue
        # v10's freeze shape carries its own replay projection; this version's is the ladder.
        and "the freeze does not record a positive replay projection" != issue
        and "the replay projection artifact is not positive" != issue
        and "the replay projection artifact's fixture was not verified" != issue
        and "the replay projection recorded no positive projected saving" != issue
        # Post-freeze changes are no longer filtered by path class: the inherited hash check
        # is amendment-aware (experiments/audits/amendment_hashes.py), so a frozen path is
        # accepted only when it still hashes to the frozen value or to a digest the amendment
        # records explicitly. Any other hash mismatch survives this filter list.
    ]

    # -- v11 identity and the selected ladder step -------------------------
    if manifest.get("mechanism", {}).get("name") != MECHANISM:
        issues.append("manifest does not declare the v11 elision-ratio mechanism")
    # The selected ladder step is stated as ``selected_M`` and as the inherited
    # ``recent_tool_calls_kept_verbatim`` field; both must equal the frozen selection.
    if int(manifest.get("mechanism", {}).get("selected_M", -1)) != int(freeze["selected_M"]):
        issues.append("manifest does not declare the frozen selected M")
    # ``recent_tool_calls_kept_verbatim`` is the inherited v9/v10 field name and the v10
    # policy block re-declares its own frozen constant after this version's value; the
    # authoritative v11 statement of the selection is ``selected_M`` above, and every row
    # also carries ``elision_ratio_recent_calls_kept_m`` (checked below).
    if list(manifest.get("mechanism", {}).get("elision_ladder", [])) != list(
        freeze["elision_ladder"]
    ):
        issues.append("manifest ladder does not match the freeze")

    # -- the freeze is unmodified ------------------------------------------
    recorded_freeze_sha = manifest.get("freeze_sha256")
    if not recorded_freeze_sha:
        issues.append("the manifest does not record the freeze file's own SHA256")
    else:
        actual = hashlib.sha256(Path(freeze_path).read_bytes()).hexdigest()
        if actual != recorded_freeze_sha:
            issues.append("the freeze file changed after the batch was run")

    # -- post-freeze file repairs are recorded, not silent ------------------
    amendment = ROOT / (
        "integrations/openai_agents/REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json"
    )
    if not amendment.is_file():
        issues.append("the freeze amendment file is missing")
    else:
        record = json.loads(amendment.read_text(encoding="utf-8"))
        entry = next(
            (
                item
                for item in record.get("amendments", [])
                if item.get("version") == "v11"
            ),
            None,
        )
        if entry is None:
            issues.append("the amendment file has no v11 entry")
        else:
            for item in entry.get("files", []):
                path = ROOT / str(item["path"])
                if not path.is_file():
                    issues.append(f"amended file is missing: {item['path']}")
                    continue
                frozen = item.get("frozen_sha256_in_freeze", "")
                current = hashlib.sha256(path.read_bytes()).hexdigest()
                if current != frozen and current != item.get("new_sha256"):
                    issues.append(
                        f"amended file changed again after the amendment record: {item['path']}"
                    )

    # -- the ladder projection artifact ------------------------------------
    if not LADDER_PROJECTION.is_file():
        issues.append("the ladder projection artifact is missing")
    else:
        ladder = json.loads(LADDER_PROJECTION.read_text(encoding="utf-8"))
        if list(ladder.get("ladder", [])) != list(freeze["elision_ladder"]):
            issues.append("ladder projection does not cover the frozen ladder")
        for m in freeze["elision_ladder"]:
            if str(m) not in ladder.get("projections", {}):
                issues.append(f"ladder projection is missing M={m}")
        if ladder.get("selected_M") != freeze["selected_M"]:
            issues.append("ladder projection selection differs from the freeze")
        positives = [
            int(m)
            for m, report in ladder.get("projections", {}).items()
            if report.get("byte_saving_rate", 0) > 0
            and report.get("elidable_indices_non_empty")
        ]
        if positives and max(positives) != int(freeze["selected_M"]):
            issues.append("the frozen selected M is not the largest positive projection")
        if ladder.get("projections", {}).get(str(freeze["elision_ladder"][0]), {}).get(
            "byte_saving_rate", -1
        ) != 0:
            issues.append("the most conservative ladder step should project zero saving")

    # -- v11 evidence schema and per-row M ---------------------------------
    rows = base_audit.base_audit._read_jsonl(batch / "samples.jsonl")
    for row in rows:
        relative = row.get("input_evidence_file")
        if not relative:
            continue
        path = batch / str(relative)
        if path.is_file():
            for record in base_audit.base_audit._read_jsonl(path):
                if record.get("schema") != "openai_elision_ratio_boundary_v11":
                    issues.append(
                        f"{row['scenario']}-{row['repeat']}-{row['method']}: v11 evidence "
                        "schema mismatch"
                    )
                    break
        if str(row.get("method")) != "pruner_v1":
            continue
        if int(row.get("elision_ratio_recent_calls_kept_m", -1)) != int(freeze["selected_M"]):
            issues.append(
                f"{row['scenario']}-{row['repeat']}: row M differs from the frozen M"
            )
        if list(row.get("elision_ratio_ladder", [])) != list(freeze["elision_ladder"]):
            issues.append(
                f"{row['scenario']}-{row['repeat']}: row ladder differs from the freeze"
            )
        if int(row.get("long_baseline_elided_sources", 0)) > 0 and int(
            row.get("elision_ratio_elidable_calls", -1)
        ) < 0:
            issues.append(
                f"{row['scenario']}-{row['repeat']}: the elision-ratio counters are missing"
            )
    plugin_facts = [
        row
        for row in rows
        if str(row.get("method")) == "pruner_v1"
    ]
    return {
        **result,
        "mechanism": MECHANISM,
        "selected_M": int(freeze["selected_M"]),
        "elision_ladder": list(freeze["elision_ladder"]),
        "freeze_sha256_recorded": bool(recorded_freeze_sha),
        "plugin_elided_sources_total": sum(
            int(row.get("long_baseline_elided_sources", 0)) for row in plugin_facts
        ),
        "issues": issues,
        "complete": not issues,
        "verdict": result["verdict"] if not issues else "not-yet-valid",
    }


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
