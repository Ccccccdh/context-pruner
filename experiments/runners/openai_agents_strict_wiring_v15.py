"""Install the same bounded final-answer model wrapper in all three arms."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any

from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners.openai_agents_bounded_final_v15 import BoundedFinalModel


@contextmanager
def strict_wiring(output_root: Path):
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    active: dict[str, Any] = {}

    def recording_model(*args, **kwargs):
        wrapper = BoundedFinalModel(original_model(*args, **kwargs))
        active["wrapper"] = wrapper
        return wrapper

    async def run_case(case, *, method: str, **kwargs):
        active.clear()
        row = await original_case(case, method=method, **kwargs)
        wrapper = active.get("wrapper")
        ledger = list(wrapper.repair_ledger) if wrapper is not None else []
        evidence = list(wrapper.repair_evidence) if wrapper is not None else []
        evidence_rel = f"format-evidence/{case.scenario}-{case.repeat}-{method}.jsonl"
        evidence_path = Path(output_root) / evidence_rel
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in evidence),
            encoding="utf-8",
        )
        row["bounded_final_schema"] = "all_arm_one_retry_v15"
        row["bounded_final_repair_calls"] = len(ledger)
        row["bounded_final_repair_ledger"] = ledger
        row["bounded_final_repair_evidence_file"] = evidence_rel
        row["bounded_final_usage_in_recorded_model_totals"] = bool(wrapper is not None)
        return row

    base.RecordingRetryModel = recording_model
    base.run_case = run_case
    try:
        yield
    finally:
        base.RecordingRetryModel = original_model
        base.run_case = original_case
