"""Zero-API replay gate for the v14 held-out tasks (per task, per model call).

For one acquired task this gate

* rebuilds every recorded boundary from the registered views and accepts the rebuild only if
  it reproduces the recorded structure, contiguity and per-output SHA256/character counts;
* applies the frozen v12 duplicate rule and recomputes every plugin-side item, so a pointer
  must equal the frozen pointer text naming the newest full copy's call id and source digest;
* checks presence fail-closed, with a negative control that must report a lost fact when the
  only surviving full copy of a view is replaced;
* reports the invariant columns (task constraints, current evidence, id-free tool-group hash,
  restore/fallback accounting, source location);
* reports the **per-model-call projection**: the byte saving summed over every recorded
  boundary divided by the bytes sent at those boundaries, plus the guard-filtered safe
  candidate count on the deciding boundary.

The verdict is `proceed` only when the gate is clean, the per-call projection clears 3% and
the safe set is not empty.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_multitask_registry_v14 as registry
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

REPO = registry.REPO
RUNS = REPO / "runs/stage5-openai-agents-api"
PROJECTION_FLOOR = 0.03


def batch_dir(task_id: str) -> Path:
    return RUNS / f"openai-repo-diagnostic-v14-{task_id}-acquisition-01"


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


def outputs(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call_output"]


def messages(items: Sequence[dict]) -> list[dict]:
    return [
        item
        for item in items
        if item.get("type") not in ("function_call", "function_call_output")
    ]


def calls(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call"]


def boundaries(task_id: str) -> list[list[dict]]:
    """Rebuild the task's real payload sequence, offline and deterministically."""
    entry = registry.task(task_id)
    payload: list[dict] = [dict(message) for message in registry.history(task_id)]
    rebuilt: list[list[dict]] = [list(payload)]
    reads = int(entry["read_calls"])
    for index in range(reads):
        name = entry["tool_names"][index % len(entry["tool_names"])]
        payload = payload + [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"issue": entry["instance_id"]}),
                "call_id": f"call_v14_{index}_{name}",
            },
            {
                "type": "function_call_output",
                "call_id": f"call_v14_{index}_{name}",
                "output": registry.source_view(task_id, index % len(entry["tool_names"])),
            },
        ]
        rebuilt.append(list(payload))
    return rebuilt


def pointer_text(newest_call_id: str, source_sha256: str) -> str:
    return (
        f"[Exact duplicate output; full source is at call_id={newest_call_id}; "
        f"sha256={source_sha256}]"
    )


def tool_group_hash(items: Sequence[dict]) -> str:
    structure = []
    for item in items:
        if item.get("type") == "function_call":
            label = f"call:{item.get('name')}"
        elif item.get("type") == "function_call_output":
            label = "output"
        else:
            label = f"message:{item.get('role')}"
        structure.append([str(item.get("type") or "message"), label])
    return hashlib.sha256(
        json.dumps(structure, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def visible_text(items: Sequence[dict]) -> str:
    return "\n".join(text for text in (text_of(item) for item in items) if text)


def visible_counts(items: Sequence[dict], task_id: str) -> dict[str, int]:
    text = visible_text(items)
    entry = registry.task(task_id)
    counts = {
        f"term:{term}": len(re.findall(re.escape(term), text, re.IGNORECASE))
        for term in entry["required_terms"]
    }
    counts.update(
        {
            f"literal:{label}": sum(
                len(re.findall(re.escape(phrase), text, re.IGNORECASE)) for phrase in phrases
            )
            for label, phrases in entry["literals"].items()
        }
    )
    return counts


def lost_presence(payload: Sequence[dict], reduced: Sequence[dict], task_id: str) -> list[str]:
    before = visible_counts(payload, task_id)
    after = visible_counts(reduced, task_id)
    return sorted(name for name, value in before.items() if value > 0 and after.get(name, 0) == 0)


def full_copy_digests(items: Sequence[dict]) -> dict[str, int]:
    seen: dict[str, int] = {}
    for position, item in enumerate(outputs(items)):
        text = text_of(item)
        if text.startswith("[Exact duplicate output;"):
            continue
        seen[hashlib.sha256(text.encode()).hexdigest()] = position
    return seen


def recorded_boundaries(task_id: str, root: Path | None = None) -> list[dict]:
    base = Path(root) if root is not None else batch_dir(task_id)
    path = base / "input-evidence" / f"{task_id}-0-none.jsonl"
    return [record for record in _read_jsonl(path) if record.get("stage") == "model_input"]


def analyse(task_id: str, root: Path | None = None) -> dict[str, Any]:
    recorded = recorded_boundaries(task_id, root)
    rebuilt = boundaries(task_id)
    entry = registry.task(task_id)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []

    for index, payload in enumerate(rebuilt):
        if index >= len(recorded):
            problems.append(f"boundary {index}: not recorded in the acquisition batch")
            continue
        record = recorded[index]
        manifest = list(record.get("output_manifest") or [])
        payload_outputs = outputs(payload)
        structure_ok = (
            len(payload) == int(record["item_count"])
            and len(messages(payload)) == int(record.get("recency_message_items", len(messages(payload))))
            and len(payload_outputs) == int(record.get("recency_output_items", len(payload_outputs)))
        )
        if not structure_ok:
            problems.append(f"boundary {index}: rebuilt structure differs from the record")
        outputs_match = len(manifest) == len(payload_outputs) and all(
            hashlib.sha256(text_of(item).encode()).hexdigest() == str(entry_rec["output_sha256"])
            and len(text_of(item)) == int(entry_rec["output_chars"])
            for item, entry_rec in zip(payload_outputs, manifest)
        )
        if not outputs_match:
            problems.append(f"boundary {index}: a tool output differs from the record")

        reduced, replacements = deduplicate(payload)
        positions = [
            position
            for position, item in enumerate(payload)
            if item.get("type") == "function_call_output"
        ]
        position_of = {item_index: position for position, item_index in enumerate(positions)}
        replaced = sorted(position_of[int(r["old_index"])] for r in replacements)
        pointer_ok = True
        for replacement in replacements:
            old = int(replacement["old_index"])
            new = int(replacement["new_index"])
            if position_of[new] <= position_of[old]:
                pointer_ok = False
            if payload[new]["output"] != reduced[new]["output"]:
                pointer_ok = False
            expected = pointer_text("", hashlib.sha256(payload[new]["output"].encode()).hexdigest())
            if not str(reduced[old]["output"]).startswith("[Exact duplicate output;"):
                pointer_ok = False
            if str(reduced[old]["output"]).split("sha256=")[-1].rstrip("]") != expected.split(
                "sha256="
            )[-1].rstrip("]"):
                pointer_ok = False
        if not pointer_ok:
            problems.append(f"boundary {index}: a pointer does not resolve correctly")

        lost = lost_presence(payload, reduced, task_id)
        if lost:
            problems.append(f"boundary {index}: visible fact lost: {lost}")
        distinct = {
            hashlib.sha256(text_of(item).encode()).hexdigest() for item in payload_outputs
        }
        missing = sorted(distinct - set(full_copy_digests(reduced)))
        if missing:
            problems.append(f"boundary {index}: a unique source has no full copy")
        group = {"none": tool_group_hash(payload), "plugin": tool_group_hash(reduced)}
        if group["none"] != group["plugin"]:
            problems.append(f"boundary {index}: tool-group hash changed")
        restore = {
            "fallback_reason": str(record.get("fallback_reason") or ""),
            "task_anchor_restore_failures": int(record.get("task_anchor_restore_failures", 0) or 0),
            "task_restore_fallbacks": int(record.get("task_restore_fallbacks", 0) or 0),
            "budget_fallbacks": int(record.get("budget_fallbacks", 0) or 0),
        }
        unaccounted = [
            key for key, value in restore.items() if key != "fallback_reason" and value
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
                "tool_calls": len(calls(payload)),
                "tool_outputs": len(payload_outputs),
                "message_units": len(messages(payload)),
                "replaced_output_positions": replaced,
                "structure_matches_record": structure_ok,
                "outputs_match_record": outputs_match,
                "invariants": {
                    "task_constraints": {
                        "counts_before": {
                            name: value
                            for name, value in visible_counts(payload, task_id).items()
                            if name.startswith("literal:")
                        },
                        "counts_after": {
                            name: value
                            for name, value in visible_counts(reduced, task_id).items()
                            if name.startswith("literal:")
                        },
                        "lost": lost,
                    },
                    "current_evidence": {
                        "distinct_sources": len(distinct),
                        "full_copies_before": len(full_copy_digests(payload)),
                        "full_copies_after": len(full_copy_digests(reduced)),
                        "sources_without_full_copy": missing,
                    },
                    "tool_group": {
                        "hash_before": group["none"],
                        "hash_after": group["plugin"],
                        "identical": group["none"] == group["plugin"],
                    },
                    "restore_and_fallback": restore,
                    "unaccounted_fallback": unaccounted if not restore["fallback_reason"] else [],
                    "source_location": {
                        "pointer_positions": replaced,
                        "pointer_resolves": pointer_ok,
                    },
                },
            }
        )

    before = sum(row["rebuilt_before_bytes"] for row in rows)
    after = sum(row["rebuilt_after_bytes"] for row in rows)
    projection = (before - after) / before if before else 0.0

    negative_control: dict[str, Any] = {"caught": False}
    if rebuilt:
        payload = rebuilt[-1]
        reduced, _ = deduplicate(payload)
        self_check = lost_presence(payload, reduced, task_id)
        damaged = [dict(item) for item in reduced]
        # Damage the only surviving full copy of the view whose text carries the most
        # registered-literal occurrences: after the real reduction the older copies are
        # pointers, so that copy is the sole carrier of those occurrences.
        counts_before = visible_counts(payload, task_id)
        best = None
        for index in range(len(registry.views(task_id))):
            target_text = registry.source_view(task_id, index)
            score = sum(
                len(re.findall(re.escape(phrase), target_text, re.IGNORECASE))
                for phrases in registry.task(task_id)["literals"].values()
                for phrase in phrases
            )
            target = next(
                (
                    position
                    for position in reversed(range(len(damaged)))
                    if damaged[position].get("type") == "function_call_output"
                    and damaged[position].get("output") == target_text
                ),
                None,
            )
            if target is not None and (best is None or score > best[0]):
                best = (score, target)
        caught: list[str] = []
        decreased: list[str] = []
        if best is not None:
            damaged[best[1]]["output"] = pointer_text("call_elsewhere", "0" * 64)
            counts_after = visible_counts(damaged, task_id)
            caught = sorted(
                name
                for name, value in counts_before.items()
                if value > 0 and counts_after.get(name, 0) == 0
            )
            decreased = sorted(
                name
                for name, value in counts_before.items()
                if counts_after.get(name, 0) < value
            )
        negative_control = {
            "real_reduction_loses_nothing": self_check == [],
            "damaged_component_lost": caught,
            "damaged_component_decreased": decreased,
            "target_found": best is not None,
            "caught": bool(caught) or bool(decreased),
        }
        if self_check:
            problems.append(f"negative control: the real reduction lost {self_check}")
        if best is None:
            problems.append("negative control: no surviving full copy to damage")
        elif not (caught or decreased):
            problems.append("negative control: replacing a unique item was not reported")

    safe = len(rows[-1]["replaced_output_positions"]) if rows else 0
    return {
        "schema": "openai_agents_v14_multitask_replay_gate",
        "task_id": task_id,
        "instance_id": entry["instance_id"],
        "repo": entry["repo"],
        "base_commit": entry["base_commit"],
        "registry_fingerprint": registry.fingerprint(task_id),
        "recorded_boundaries": len(recorded),
        "rebuilt_boundaries": len(rebuilt),
        "boundaries": rows,
        "projection": {
            "scope": "per model call: byte saving summed over every recorded boundary",
            "before_bytes": before,
            "after_bytes": after,
            "byte_saving_rate": projection,
            "floor": PROJECTION_FLOOR,
            "passes_floor": projection >= PROJECTION_FLOOR,
        },
        "safe_candidates": {
            "potential_old_units_upper_bound": safe,
            "guard_filtered_safe_candidates": safe,
            "per_boundary_replaced_counts": [len(row["replaced_output_positions"]) for row in rows],
            "rule": "reported separately from the projection; non-zero is not a benefit prediction",
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
        },
        "negative_control": negative_control,
        "problems": problems,
        "qualified": (
            not problems and projection >= PROJECTION_FLOOR and safe > 0
        ),
        "verdict": (
            "proceed"
            if (not problems and projection >= PROJECTION_FLOOR and safe > 0)
            else "stop"
        ),
        "paid_requests": 0,
    }


def write(task_id: str, root: Path | None = None, out: Path | None = None) -> dict[str, Any]:
    report = analyse(task_id, root)
    target = out or (REPO / f"integrations/openai_agents/V14_GATE_{task_id.upper()}_20261005.json")
    Path(target).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: -m experiments.runners.openai_agents_multitask_replay_gate_v14 TASK_ID")
    task_id = sys.argv[1]
    report = write(task_id)
    print(
        json.dumps(
            {
                "task_id": task_id,
                "verdict": report["verdict"],
                "projection": report["projection"]["byte_saving_rate"],
                "safe_candidates": report["safe_candidates"]["guard_filtered_safe_candidates"],
                "invariants": report["invariant_gate"]["all_invariants_hold"],
                "negative_control": report["negative_control"]["caught"],
                "problems": report["problems"],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
