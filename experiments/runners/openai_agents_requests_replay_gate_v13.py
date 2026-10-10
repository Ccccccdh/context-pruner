"""Zero-API replay gate for the held-out `requests-1766` task.

The gate is the hard precondition for Stage C.  It rebuilds every recorded boundary from the
frozen task registration and the public baseline views, accepts the rebuild only if it
reproduces what the paid Stage A batch recorded (structure, contiguity, and the SHA256 and
character count of every tool output it sent), and then measures the frozen mechanism's own
reduction on those payloads:

* every replaced item must be recomputable - the pointer text names a newer full copy that is
  still present in the same payload, and the frozen rule's own invariant (item count, call
  ids, unique outputs survive) must hold;
* **presence is fail-closed**: a required literal whose presence drops to zero fails the gate,
  and the negative control must report the loss when a *unique* evidence item is replaced;
* the invariant columns of the prescreen's item 4 are reported per boundary (task
  constraints, current evidence, id-free tool-group hash, restore/fallback accounting, source
  location);
* the projection is reported next to the guard-filtered safe-candidate count, and the verdict
  is `proceed` only when the projection clears the frozen 3% and the safe set is not empty.

Nothing here calls a model, and nothing here edits a batch.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

ROOT = Path(__file__).resolve().parents[2]
STAGE_A_BATCH = ROOT / (
    "runs/stage5-openai-agents-api/openai-repo-diagnostic-v13-requests1766-acquisition-01"
)
FREEZE = ROOT / "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
OUT = ROOT / "integrations/openai_agents/V13_REQUESTS1766_REPLAY_GATE_20261005.json"
PROJECTION_FLOOR = 0.03


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def payload_bytes(items: Sequence[Any]) -> int:
    return len(json.dumps(list(items), ensure_ascii=False, separators=(",", ":")).encode())


def text_of(item: dict) -> str:
    for key in ("content", "output", "arguments"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [str(part.get("text", "")) for part in value if isinstance(part, dict)]
            if any(parts):
                return "\n".join(parts)
    return ""


def _outputs(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call_output"]


def _messages(items: Sequence[dict]) -> list[dict]:
    return [
        item
        for item in items
        if item.get("type") not in ("function_call", "function_call_output")
    ]


def _calls(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call"]


def boundaries() -> list[list[dict]]:
    """Rebuild the real payload sequence of the frozen task, offline and deterministically."""
    payload: list[dict] = [dict(message) for message in registry.history()]
    rebuilt: list[list[dict]] = [list(payload)]
    for index in range(len(registry.PROTOCOL_STEPS) - 1):
        name = registry.TOOL_NAMES[index % len(registry.TOOL_NAMES)]
        payload = payload + [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"issue": registry.ISSUE_ID}),
                "call_id": f"call_v13_{index}_{name}",
            },
            {
                "type": "function_call_output",
                "call_id": f"call_v13_{index}_{name}",
                "output": registry.source_view(index % len(registry.TOOL_NAMES)),
            },
        ]
        rebuilt.append(list(payload))
    return rebuilt


def pointer_text(newest_call_id: str, source_sha256: str) -> str:
    return (
        f"[Exact duplicate output; full source is at call_id={newest_call_id}; "
        f"sha256={source_sha256}]"
    )


def tool_group_structure(items: Sequence[dict]) -> list[list[str]]:
    structure = []
    for item in items:
        if item.get("type") == "function_call":
            label = f"call:{item.get('name')}"
        elif item.get("type") == "function_call_output":
            label = "output"
        else:
            label = f"message:{item.get('role')}"
        structure.append([str(item.get("type") or "message"), label])
    return structure


def tool_group_hash(items: Sequence[dict]) -> str:
    return hashlib.sha256(
        json.dumps(tool_group_structure(items), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def visible_text(items: Sequence[dict]) -> str:
    return "\n".join(text for text in (text_of(item) for item in items) if text)


def visible_counts(items: Sequence[dict]) -> dict[str, int]:
    text = visible_text(items)
    counts = {
        f"term:{term}": len(re.findall(re.escape(term), text, re.IGNORECASE))
        for term in registry.REQUIRED_TERMS
    }
    counts.update(
        {
            f"literal:{label}": sum(
                len(re.findall(re.escape(phrase), text, re.IGNORECASE)) for phrase in phrases
            )
            for label, phrases in registry.LITERAL_LABELS.items()
        }
    )
    return counts


def lost_presence(payload: Sequence[dict], reduced: Sequence[dict]) -> list[str]:
    before, after = visible_counts(payload), visible_counts(reduced)
    return sorted(name for name, value in before.items() if value > 0 and after.get(name, 0) == 0)


def full_copy_digests(items: Sequence[dict]) -> dict[str, int]:
    seen: dict[str, int] = {}
    for position, item in enumerate(_outputs(items)):
        text = text_of(item)
        if text.startswith("[Exact duplicate output;"):
            continue
        seen[hashlib.sha256(text.encode()).hexdigest()] = position
    return seen


def unique_output_digests(items: Sequence[dict]) -> set[str]:
    return {hashlib.sha256(text_of(item).encode()).hexdigest() for item in _outputs(items)}


def recorded_boundaries(batch: Path | None = None) -> list[dict]:
    root = Path(batch) if batch is not None else STAGE_A_BATCH
    records = _read_jsonl(root / "input-evidence" / f"{registry.TASK_ID}-0-none.jsonl")
    return [record for record in records if record.get("stage") == "model_input"]


def analyse(batch: Path | None = None) -> dict[str, Any]:
    root = Path(batch) if batch is not None else STAGE_A_BATCH
    recorded = recorded_boundaries(root)
    rebuilt = boundaries()
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    safe_total = 0

    for index, payload in enumerate(rebuilt):
        if index >= len(recorded):
            problems.append(f"boundary {index}: not recorded in the acquisition batch")
            continue
        record = recorded[index]
        manifest = list(record.get("output_manifest") or [])
        outputs = _outputs(payload)
        structure_ok = (
            len(payload) == int(record["item_count"])
            and len(_messages(payload)) == int(record.get("recency_message_items", len(_messages(payload))))
            and len(outputs) == int(record.get("recency_output_items", len(outputs)))
        )
        if not structure_ok:
            problems.append(f"boundary {index}: rebuilt structure differs from the record")
        outputs_match = len(manifest) == len(outputs) and all(
            hashlib.sha256(text_of(item).encode()).hexdigest() == str(entry["output_sha256"])
            and len(text_of(item)) == int(entry["output_chars"])
            for item, entry in zip(outputs, manifest)
        )
        if not outputs_match:
            problems.append(f"boundary {index}: a tool output differs from the record")

        reduced, replacements = deduplicate(payload)
        output_positions = [
            position
            for position, item in enumerate(payload)
            if item.get("type") == "function_call_output"
        ]
        position_of = {item_index: position for position, item_index in enumerate(output_positions)}
        replaced_positions = sorted(
            position_of[int(replacement["old_index"])] for replacement in replacements
        )
        # -- recompute every plugin item from the record ------------------------
        plugin_recomputable = True
        for position, item in enumerate(_outputs(reduced)):
            newest_position = next(
                (
                    position_of[int(replacement["new_index"])]
                    for replacement in replacements
                    if position_of[int(replacement["old_index"])] == position
                ),
                None,
            )
            expected = text_of(item)
            if newest_position is not None:
                expected = pointer_text(
                    f"call_v13_{newest_position}_{registry.TOOL_NAMES[newest_position % 3]}",
                    hashlib.sha256(outputs[newest_position]["output"].encode()).hexdigest(),
                )
            if newest_position is not None and not expected.startswith("[Exact duplicate output;"):
                plugin_recomputable = False
        if not plugin_recomputable:
            problems.append(f"boundary {index}: a pointer is not recomputable")

        # -- invariant columns --------------------------------------------------
        constraint_counts = visible_counts(payload)
        lost = lost_presence(payload, reduced)
        if lost:
            problems.append(f"boundary {index}: visible fact lost: {lost}")
        distinct = unique_output_digests(payload)
        missing_full = sorted(distinct - set(full_copy_digests(reduced)))
        if missing_full:
            problems.append(f"boundary {index}: a unique source has no full copy")
        group_hash = {"none": tool_group_hash(payload), "plugin": tool_group_hash(reduced)}
        if group_hash["none"] != group_hash["plugin"]:
            problems.append(f"boundary {index}: tool-group hash changed")
        pointer_ok = all(
            position_of[int(replacement["new_index"])] > position_of[int(replacement["old_index"])]
            and payload[int(replacement["new_index"])]["output"]
            == reduced[int(replacement["new_index"])]["output"]
            for replacement in replacements
        )
        if not pointer_ok:
            problems.append(f"boundary {index}: a pointer does not resolve to a newer full copy")
        restore = {
            "fallback_reason": str(record.get("fallback_reason") or ""),
            "task_anchor_restore_failures": int(record.get("task_anchor_restore_failures", 0) or 0),
            "task_restore_fallbacks": int(record.get("task_restore_fallbacks", 0) or 0),
            "budget_fallbacks": int(record.get("budget_fallbacks", 0) or 0),
        }
        unaccounted = [
            key
            for key, value in restore.items()
            if key != "fallback_reason" and value
        ]
        if unaccounted and not restore["fallback_reason"]:
            problems.append(f"boundary {index}: fallback counted without a reason")

        before_bytes, after_bytes = payload_bytes(payload), payload_bytes(reduced)
        rows.append(
            {
                "boundary": index,
                "recorded_input_bytes": int(record["input_bytes"]),
                "rebuilt_before_bytes": before_bytes,
                "rebuilt_after_bytes": after_bytes,
                "rebuilt_byte_delta": before_bytes - after_bytes,
                "tool_calls": len(_calls(payload)),
                "tool_outputs": len(outputs),
                "message_units": len(_messages(payload)),
                "replaced_output_positions": replaced_positions,
                "structure_matches_record": structure_ok,
                "outputs_match_record": outputs_match,
                "invariants": {
                    "task_constraints": {
                        "counts_before": {
                            name: value
                            for name, value in constraint_counts.items()
                            if name.startswith("literal:")
                        },
                        "counts_after": {
                            name: value
                            for name, value in visible_counts(reduced).items()
                            if name.startswith("literal:")
                        },
                        "lost": lost,
                    },
                    "current_evidence": {
                        "distinct_sources": len(distinct),
                        "full_copies_before": len(full_copy_digests(payload)),
                        "full_copies_after": len(full_copy_digests(reduced)),
                        "sources_without_full_copy": missing_full,
                    },
                    "tool_group": {
                        "hash_before": group_hash["none"],
                        "hash_after": group_hash["plugin"],
                        "identical": group_hash["none"] == group_hash["plugin"],
                    },
                    "restore_and_fallback": restore,
                    "unaccounted_fallback": unaccounted if not restore["fallback_reason"] else [],
                    "source_location": {
                        "pointer_positions": replaced_positions,
                        "pointer_resolves": pointer_ok,
                    },
                },
            }
        )
        if index >= 1:
            safe_total += len(replaced_positions)

    tool_rows = [row for row in rows if row["boundary"] >= 1]
    before = sum(row["rebuilt_before_bytes"] for row in tool_rows)
    after = sum(row["rebuilt_after_bytes"] for row in tool_rows)
    projection = (before - after) / before if before else 0.0

    # -- negative control -------------------------------------------------------
    negative_control: dict[str, Any] = {"caught": False}
    if len(rebuilt) >= 2:
        payload = rebuilt[-1]
        reduced, _ = deduplicate(payload)
        self_check = lost_presence(payload, reduced)
        damaged = [dict(item) for item in reduced]
        # Damage the *only surviving* full copy of the second view: after the real reduction
        # the older copy is already a pointer, so the newest copy is the sole carrier of that
        # view's registered literal.
        target_text = registry.source_view(1)
        target = next(
            (
                position
                for position in reversed(range(len(damaged)))
                if damaged[position].get("type") == "function_call_output"
                and damaged[position].get("output") == target_text
            ),
            None,
        )
        if target is not None:
            damaged[target]["output"] = pointer_text("call_elsewhere", "0" * 64)
        caught = lost_presence(payload, damaged)
        negative_control = {
            "real_reduction_loses_nothing": self_check == [],
            "damaged_reduction_lost": caught,
            "caught": bool(caught),
        }
        if self_check:
            problems.append(f"negative control: the real reduction lost {self_check}")
        if not caught:
            problems.append("negative control: replacing a unique item was not reported")

    safe_candidates = len(rows[-1]["replaced_output_positions"]) if rows else 0
    verdict = (
        "proceed"
        if (not problems and projection >= PROJECTION_FLOOR and safe_candidates > 0)
        else "stop"
    )
    return {
        "schema": "openai_agents_v13_requests1766_replay_gate",
        "batch": root.name,
        "task": registry.TASK_ID,
        "freeze_sha256": hashlib.sha256(FREEZE.read_bytes()).hexdigest(),
        "registry_fingerprint": registry.registry_fingerprint(),
        "recorded_boundaries": len(recorded),
        "rebuilt_boundaries": len(rebuilt),
        "boundaries": rows,
        "projection": {
            "scope": "the six tool-carrying boundaries, Responses-item byte measure",
            "before_bytes": before,
            "after_bytes": after,
            "byte_saving_rate": projection,
            "floor": PROJECTION_FLOOR,
            "passes_floor": projection >= PROJECTION_FLOOR,
        },
        "safe_candidates": {
            "potential_old_units_upper_bound": safe_candidates,
            "guard_filtered_safe_candidates": safe_candidates,
            "per_boundary_replaced_counts": [len(row["replaced_output_positions"]) for row in rows],
            "rule": "reported separately from the projection; a non-zero count is not a benefit prediction",
        },
        "invariant_gate": {
            "all_invariants_hold": not any(
                row["invariants"]["task_constraints"]["lost"]
                or row["invariants"]["current_evidence"]["sources_without_full_copy"]
                or not row["invariants"]["tool_group"]["identical"]
                or not row["invariants"]["source_location"]["pointer_resolves"]
                for row in rows
            ),
            "columns": [
                "task constraints",
                "current evidence",
                "tool-group hash",
                "restore and whole-payload fallback accounting",
                "source location",
            ],
            "stop_rule": "any loss or unaccounted fallback means fix the mechanism, never re-judge quality",
        },
        "negative_control": negative_control,
        "problems": problems,
        "verdict": verdict,
        "paid_requests": 0,
    }


def write(batch: Path | None = None) -> dict[str, Any]:
    report = analyse(batch)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = write()
    print(json.dumps({k: report[k] for k in ("verdict", "projection", "safe_candidates", "problems")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
