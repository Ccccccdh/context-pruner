"""Bounded development pilot for exact duplicate tool-output retention."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from experiments.runners import run_openai_agents_repo_diagnostic_v9 as v9
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import ExactDuplicateFilter
from experiments.runners.trigger_gate import BudgetTriggeredFilter

FREEZE_ENV = "DSH_V12_FREEZE"


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    wrapper = original(method, **kwargs)
    retention = ExactDuplicateFilter(
        task=str(case.scenario), task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    if isinstance(wrapper, BudgetTriggeredFilter):
        wrapper.inner = retention
        return v9.v8._GateAware(wrapper, retention)
    return retention


def main(argv=None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    original_filter = v9.build_retentive_filter
    original_manifest = v9.MANIFEST_EXTRA
    original_amend = v9.v8._amend_manifest
    v9.build_retentive_filter = build_retentive_filter
    v9.MANIFEST_EXTRA = {
        **original_manifest,
        "kind": "openai_agents_exact_duplicate_development_v12",
        "mechanism": {"arm": "pruner_v1", "name": "exact_duplicate_output_v12",
                      "schema": "exact_duplicate_v12",
                      "rule": "only an older output with a byte-identical newer output is replaced; newest full output and all call/output items survive"},
    }

    def amend(root: Path | None, hard_bytes: int) -> None:
        original_amend(root, hard_bytes)
        if root is None:
            return
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return
        freeze_path = Path(os.environ.get(FREEZE_ENV, ""))
        if not freeze_path.is_file():
            raise RuntimeError("DSH_V12_FREEZE must identify the immutable freeze file")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["freeze_path"] = str(freeze_path)
        data["freeze_sha256"] = hashlib.sha256(freeze_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    v9.v8._amend_manifest = amend
    try:
        return v9.main(args)
    finally:
        v9.build_retentive_filter = original_filter
        v9.MANIFEST_EXTRA = original_manifest
        v9.v8._amend_manifest = original_amend


if __name__ == "__main__":
    raise SystemExit(main())
