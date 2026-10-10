"""Zero-API test of lossless-by-content tool-output deduplication.

Only an older output with a byte-identical newer output may be replaced.  The
newest copy, every call item, and every output item remain in the payload.
This is a replay projection, not a provider-token or quality measurement.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners import openai_agents_long_baseline_boundary_v9 as v9
from context_pruner.adapters.openai_agents import _jsonable_item


def _bytes(items: list[dict[str, Any]]) -> int:
    return len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode())


def deduplicate(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace only old exact duplicates, pointing to their newest full copy."""
    output_indices: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
            output_indices.setdefault(item["output"], []).append(index)
    result = deepcopy(items)
    replacements = []
    for output, indices in output_indices.items():
        if len(indices) < 2:
            continue
        newest = indices[-1]
        newest_call = str(items[newest].get("call_id", ""))
        if not newest_call:
            continue
        digest = hashlib.sha256(output.encode()).hexdigest()
        for old in indices[:-1]:
            old_call = str(items[old].get("call_id", ""))
            if not old_call or old_call == newest_call:
                continue
            result[old]["output"] = (
                f"[Exact duplicate output; full source is at call_id={newest_call}; "
                f"sha256={digest}]"
            )
            replacements.append({"old_index": old, "new_index": newest,
                                 "old_call_id": old_call, "new_call_id": newest_call,
                                 "sha256": digest})
    return result, replacements


class ExactDuplicateFilter(v9.NarrowGuardBoundaryFilter):
    """SDK filter using only exact duplicate outputs; inherited observers remain active."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.exact_duplicate_replacements = 0
        self.exact_duplicate_saved_bytes = 0
        self.exact_duplicate_rejected = 0

    def filter_items(self, items: Any) -> list[Any] | None:
        self.calls += 1
        self.last_saved_bytes = 0
        self.last_fallback_reason = ""
        source = [_jsonable_item(item) for item in items]
        if not all(isinstance(item, dict) for item in source):
            self.exact_duplicate_rejected += 1
            self.last_fallback_reason = "unsupported_item_shape"
            return None
        reduced, replacements = deduplicate(source)
        if not replacements:
            return None
        calls_before = [x.get("call_id") for x in source if x.get("type") == "function_call"]
        calls_after = [x.get("call_id") for x in reduced if x.get("type") == "function_call"]
        output_before = [x.get("call_id") for x in source if x.get("type") == "function_call_output"]
        output_after = [x.get("call_id") for x in reduced if x.get("type") == "function_call_output"]
        unique_before = {x["output"] for x in source if x.get("type") == "function_call_output"
                         and isinstance(x.get("output"), str)}
        full_after = {x["output"] for x in reduced if x.get("type") == "function_call_output"
                      and isinstance(x.get("output"), str)}
        valid = (
            len(source) == len(reduced) and calls_before == calls_after
            and output_before == output_after and unique_before <= full_after
            and all(call in output_after for call in calls_after)
            and all(source[r["old_index"]]["output"] == reduced[r["new_index"]]["output"]
                    for r in replacements)
        )
        saved = _bytes(source) - _bytes(reduced)
        if not valid or saved <= 0:
            self.exact_duplicate_rejected += 1
            self.last_fallback_reason = "exact_duplicate_invariant_or_budget_failed"
            return None
        self.exact_duplicate_replacements += len(replacements)
        self.exact_duplicate_saved_bytes += saved
        self.compacted_calls += 1
        self.last_saved_bytes = saved
        self.saved_bytes_total += saved
        return reduced

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update({"selective_retention_schema": "exact_duplicate_v12",
                        "exact_duplicate_replacements": self.exact_duplicate_replacements,
                        "exact_duplicate_saved_bytes": self.exact_duplicate_saved_bytes,
                        "exact_duplicate_rejected": self.exact_duplicate_rejected})
        return metrics


def replay_gate(repeat: int = 0) -> dict[str, Any]:
    boundaries = replay.realistic_boundaries(repeat)
    recorded = replay.fixture_report(repeat=repeat)
    paid = replay.load_recorded_batch()[(repeat, "none")]
    recorded_inputs = [r for r in paid.evidence_records if r.get("stage") == "model_input"]
    events = []
    for index, original in enumerate(boundaries[1:], 1):
        reconstructed_outputs = [item for item in original if item.get("type") == "function_call_output"]
        paid_manifest = recorded_inputs[index].get("output_manifest", [])
        actual_output_hashes_match = len(reconstructed_outputs) == len(paid_manifest) and all(
            hashlib.sha256(item["output"].encode()).hexdigest() == record.get("output_sha256")
            and len(item["output"]) == record.get("output_chars")
            for item, record in zip(reconstructed_outputs, paid_manifest)
        )
        reduced, replacements = deduplicate(original)
        calls_before = [item.get("call_id") for item in original if item.get("type") == "function_call"]
        calls_after = [item.get("call_id") for item in reduced if item.get("type") == "function_call"]
        outputs_before = [item.get("call_id") for item in original if item.get("type") == "function_call_output"]
        outputs_after = [item.get("call_id") for item in reduced if item.get("type") == "function_call_output"]
        unique_before = {item["output"] for item in original if item.get("type") == "function_call_output"}
        full_after = {item["output"] for item in reduced if item.get("type") == "function_call_output"}
        invariant = (
            actual_output_hashes_match
            and
            len(original) == len(reduced)
            and calls_before == calls_after
            and outputs_before == outputs_after
            and all(call in outputs_after for call in calls_after)
            and unique_before <= full_after
            and all(original[r["old_index"]]["output"] == reduced[r["new_index"]]["output"]
                    for r in replacements)
        )
        events.append({"boundary": index, "before_bytes": _bytes(original),
                       "after_bytes": _bytes(reduced), "replacements": replacements,
                       "actual_output_hashes_match": actual_output_hashes_match,
                       "invariant_ok": invariant})
    before = sum(event["before_bytes"] for event in events)
    after = sum(event["after_bytes"] for event in events)
    return {"schema": "openai_agents_exact_duplicate_replay_v12",
            "repeat": repeat,
            "recorded_fixture_ok": recorded["ok"],
            "recorded_structure_fingerprint": replay.recorded_structure_fingerprint(repeat=repeat),
            "events": events, "all_invariants_ok": all(e["invariant_ok"] for e in events),
            "baseline_payload_bytes": before, "projected_payload_bytes": after,
            "projected_byte_saving_rate": (before-after)/before if before else 0.0,
            "provider_token_or_quality_claim": False}
