"""v11 entrypoint: the elision-ratio ablation, one ladder step per process.

``M`` is taken from ``DSH_V11_RECENT_CALLS_KEPT`` (the frozen ladder only), because the
ablation must not require editing any file between arms.  Everything else - the case
builder, the task input, the registry, the reduction, the guards, the thresholds and the
budgets - is v10's/v9's/v8's, imported rather than copied.

Batch id: ``openai-repo-diagnostic-v11-elision-ratio-boundary``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v11 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v10 as v10

HOST = v10.HOST
DISCLOSURE = v10.DISCLOSURE
BYTES_PER_ESTIMATED_TOKEN = v10.BYTES_PER_ESTIMATED_TOKEN
build_case = v10.build_case
protocol_message = v10.protocol_message
registered_statement = v10.registered_statement
task_statement = v10.task_statement
_CaseWithTaskStatement = v10._CaseWithTaskStatement
REGISTERED_CASES = v10.REGISTERED_CASES
LONG_TASK_ID = policy.LONG_TASK_ID
SHORT_TASK_ID = policy.SHORT_TASK_ID
TASKS = policy.TASKS

MANIFEST_EXTRA = {
    **v10.MANIFEST_EXTRA,
    "kind": "openai_agents_runner_real_api_paired_elision_ratio_boundary",
    "mechanism": {
        "arm": "pruner_v1",
        "name": "elision_ratio_boundary_v11",
        "schema": policy.MECHANISM_SCHEMA,
        "registry": long_registry.REGISTRY_SCHEMA,
        "base_registry": policy.base_registry.REGISTRY_SCHEMA,
        "recent_tool_calls_kept_verbatim": policy.current_elision_m(),
        "selected_M": policy.current_elision_m(),
        "elision_ladder": list(policy.ELISION_LADDER),
        **policy.policy_dict(),
        "purpose": (
            "find the largest elision that keeps plugin quality at the baseline level: M is "
            "the single variable, chosen from the frozen ladder by the replay projection "
            "(most conservative positive M)"
        ),
    },
}

PERSISTED_RETENTION_FIELDS = tuple(
    dict.fromkeys(
        (
            *v10.PERSISTED_RETENTION_FIELDS,
            "elision_ratio_recent_calls_kept_m",
            "elision_ratio_ladder",
            "elision_ratio_tool_calls",
            "elision_ratio_elidable_calls",
            "elision_ratio_protected_calls",
            "elision_ratio_elidable_fraction",
            "elision_ratio_elidable_indices",
            "elision_ratio_protected_indices",
        )
    )
)


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the elision-ratio filter in the plugin arm only, at the selected ``M``."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = policy.ElisionRatioFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        recent_calls_kept=policy.current_elision_m(),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    from experiments.runners.trigger_gate import BudgetTriggeredFilter

    if isinstance(context_filter, BudgetTriggeredFilter):
        context_filter.inner = retention
        return v10.v9.v8._GateAware(context_filter, retention)
    return retention


def _retention_counters(recorder: Any, context_filter: Any) -> dict[str, Any]:
    """v10's counters plus the elision-ratio columns, taken from the evidence records."""
    counters = v10._V9_RETENTION_COUNTERS(recorder, context_filter)
    model = [record for record in recorder.records if record.get("stage") == "model_input"]
    final = model[-1] if model else {}
    counters.update(
        {
            "elision_ratio_recent_calls_kept_m": int(
                final.get("elision_ratio_recent_calls_kept_m", policy.current_elision_m())
            ),
            "elision_ratio_ladder": list(policy.ELISION_LADDER),
            "elision_ratio_tool_calls": int(final.get("elision_ratio_tool_calls", 0)),
            "elision_ratio_elidable_calls": int(
                final.get("elision_ratio_elidable_calls", 0)
            ),
            "elision_ratio_protected_calls": int(
                final.get("elision_ratio_protected_calls", 0)
            ),
            "elision_ratio_elidable_fraction": float(
                final.get("elision_ratio_elidable_fraction", 0.0)
            ),
            "elision_ratio_elidable_indices": list(
                final.get("elision_ratio_elidable_indices", [])
            ),
            "elision_ratio_protected_indices": list(
                final.get("elision_ratio_protected_indices", [])
            ),
        }
    )
    return counters


#: Environment variable pointing at the freeze file this batch runs under.  The runner
#: records that file's own SHA256 in the manifest, so an after-the-fact edit of a frozen
#: file is detectable from the batch alone.
FREEZE_ENV = "DSH_V11_FREEZE"


def _freeze_sha256() -> str:
    import hashlib
    import os

    path = os.getenv(FREEZE_ENV, "")
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


#: The frozen v8 manifest amendment, captured at import time so this module's replacement
#: can call the original after it has been installed into the v8 module.
_V8_AMEND_MANIFEST = v10.v9.v8._amend_manifest


def _amend_manifest(root: Path | None, hard_bytes: int) -> None:
    """v8's manifest amendment plus the freeze file's own digest."""
    _V8_AMEND_MANIFEST(root, hard_bytes)
    if root is None:
        return
    path = root / "manifest.json"
    if not path.is_file():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    digest = _freeze_sha256()
    manifest["freeze_sha256"] = digest
    manifest["freeze_path"] = str(Path(str(__import__("os").getenv(FREEZE_ENV, ""))).as_posix())
    manifest["freeze_sha256_note"] = (
        "SHA256 of the freeze file recorded by this batch; empty means the batch was run "
        "without DSH_V11_FREEZE and must not be treated as a frozen batch"
    )
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    import sys

    from experiments.runners.openai_agents_evidence_v11 import ElisionRatioRecorder

    # ``v8.main`` calls ``v8._amend_manifest`` at the end of the run, so this version's
    # amendment is installed there (it is v8's manifest this batch is written under).
    _amend_manifest_for_v8 = _amend_manifest
    args = list(sys.argv[1:] if argv is None else argv)
    original = {
        "build_retentive_filter": v10.build_retentive_filter,
        "retention_counters": v10._retention_counters,
        "manifest_extra": v10.MANIFEST_EXTRA,
        "persisted_fields": v10.PERSISTED_RETENTION_FIELDS,
        "recorder_factory": v10.v9.RECORDER_FACTORY,
        "amend_manifest": v10.v9.v8._amend_manifest,
    }
    v10.build_retentive_filter = build_retentive_filter
    v10._retention_counters = _retention_counters
    v10.MANIFEST_EXTRA = MANIFEST_EXTRA
    v10.PERSISTED_RETENTION_FIELDS = list(PERSISTED_RETENTION_FIELDS)
    v10.v9.RECORDER_FACTORY = ElisionRatioRecorder
    v10.v9.v8._amend_manifest = _amend_manifest_for_v8
    try:
        return v10.main(args)
    finally:
        v10.build_retentive_filter = original["build_retentive_filter"]
        v10._retention_counters = original["retention_counters"]
        v10.MANIFEST_EXTRA = original["manifest_extra"]
        v10.PERSISTED_RETENTION_FIELDS = original["persisted_fields"]
        v10.v9.RECORDER_FACTORY = original["recorder_factory"]
        v10.v9.v8._amend_manifest = original["amend_manifest"]


def ladder_projection() -> dict[str, Any]:
    from experiments.runners.openai_agents_elision_ladder_projection_v11 import main as project

    project()
    from experiments.runners.openai_agents_elision_ladder_projection_v11 import OUT

    return json.loads(OUT.read_text(encoding="utf-8"))


def boundary_for_batch(batch: Path) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in (batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    baseline = [row for row in rows if row.get("method") == "none"]
    if not baseline:
        raise SystemExit("no baseline rows in this batch")
    return policy.long_task_boundary(
        max(int(row.get("model_calls", 0)) for row in baseline),
        max(int(row.get("actual_input_tokens", 0)) for row in baseline),
    )


if __name__ == "__main__":
    raise SystemExit(main())
