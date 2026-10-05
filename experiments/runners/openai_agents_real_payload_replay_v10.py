"""Real-payload replay: build offline fixtures from the recorded v9 batch.

Why this module exists
----------------------
v9's paid batch falsified its own offline projection (+34.15 % projected, −0.000004 %
measured).  The cause was not the mechanism but the **fixture**: the offline stub inserted
an assistant message between turns, so its payloads had ``turn_count = 6`` while the real
SDK payloads keep every tool item contiguous and have ``turn_count ≡ 1``.  A zero-API gate
that invents its own payload shape can therefore be wrong about the *structure*, not just
about the number of turns.

This module removes the invention.  It rebuilds each model boundary from the **public,
deterministic** inputs the batch actually sent - the frozen task history followed by the
tool call/output pairs the registered readers return - and validates the rebuilt payload
against the recorded evidence of the paid batch:

* the message-item count must equal the recorded ``recency_message_items``;
* the tool-output count must equal the recorded ``recency_output_items``;
* the total item count must equal the recorded ``item_count``;
* the tool items must form **exactly one contiguous run** (the recorded
  ``recency_turn_count`` is 1 at every boundary that carries a tool item);
* each boundary must add exactly one call/output pair.

The payload bytes differ from the recorded ``input_bytes`` because the paid run went
through the chat-completions transport while the fixture is built as the Responses item
list; that difference is stated in the fixture and is *not* used by the replay - the replay
measures structure and payload content, which are what the mechanism acts on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

ROOT = Path(__file__).resolve().parents[2]
V9_BATCH = (
    ROOT
    / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v9-long-baseline-boundary"
)
#: The frozen task id whose public inputs the fixture replays.
TASK_ID = long_registry.TASK_ID
#: Tool call names, in the order the frozen investigation protocol requests them.
READ_ORDER = ("read_django_count_entry", "read_django_aggregation", "read_django_count_call")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@dataclass
class RecordedBoundary:
    """One recorded model boundary of the paid batch (structure only)."""

    index: int
    stage: str
    item_count: int
    input_bytes: int
    input_sha256: str
    message_items: int
    tool_items: int
    tool_outputs: int
    turn_count: int
    elidable_indices: list[int]
    registered_literal_counts: dict[str, int]


@dataclass
class RecordedSample:
    """One paid sample and its recorded boundaries."""

    scenario: str
    repeat: int
    method: str
    model_calls: int
    actual_input_tokens: int
    complete_total_tokens: int
    success: bool
    elided_sources: int
    restore_failures: int
    evidence_file: str
    boundaries: list[RecordedBoundary] = field(default_factory=list)
    evidence_records: list[dict] = field(default_factory=list)


def load_recorded_batch(batch: Path | None = None) -> dict[tuple[int, str], RecordedSample]:
    """Load the paid batch's samples and their per-boundary evidence."""
    root = Path(batch) if batch is not None else V9_BATCH
    rows = _read_jsonl(root / "samples.jsonl")
    samples: dict[tuple[int, str], RecordedSample] = {}
    for row in rows:
        records = _read_jsonl(root / str(row["input_evidence_file"]))
        boundaries = [
            RecordedBoundary(
                index=int(record["index"]),
                stage=str(record["stage"]),
                item_count=int(record["item_count"]),
                input_bytes=int(record["input_bytes"]),
                input_sha256=str(record["input_sha256"]),
                message_items=int(record.get("recency_message_items", 0)),
                tool_items=int(record.get("recency_tool_items", 0))
                or max(0, int(record["item_count"]) - int(record.get("recency_message_items", 0))),
                tool_outputs=int(record.get("recency_output_items", 0)),
                turn_count=int(record.get("recency_turn_count", 0)),
                elidable_indices=list(record.get("recency_elidable_indices", [])),
                registered_literal_counts=dict(record.get("registered_literal_counts", {})),
            )
            for record in records
            if record.get("stage") == "model_input"
        ]
        samples[(int(row["repeat"]), str(row["method"]))] = RecordedSample(
            scenario=str(row["scenario"]),
            repeat=int(row["repeat"]),
            method=str(row["method"]),
            model_calls=int(row["model_calls"]),
            actual_input_tokens=int(row["actual_input_tokens"]),
            complete_total_tokens=int(row["all_arm_total_tokens"]),
            success=bool(row["success"]),
            elided_sources=int(row.get("long_baseline_elided_sources", 0)),
            restore_failures=int(row.get("task_anchor_restore_failures", 0)),
            evidence_file=str(row["input_evidence_file"]),
            boundaries=boundaries,
            evidence_records=records,
        )
    return samples


def realistic_boundaries(repeat: int = 0) -> list[list[dict]]:
    """The real payload sequence: frozen history plus one call/output pair per turn.

    Every message the batch sent is present (the three history messages) and **no message
    is inserted between tool items**, which is the structural fact the paid batch recorded.
    """
    case = v8.build_case(TASK_ID, repeat)
    payload: list[dict] = [dict(message) for message in case.history]
    boundaries: list[list[dict]] = [list(payload)]
    for index, name in enumerate(READ_ORDER * 2):
        payload = payload + [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"codename": case.codename}),
                "call_id": f"call_real_{index}_{name}",
            },
            {
                "type": "function_call_output",
                "call_id": f"call_real_{index}_{name}",
                "output": v1.source_view(long_registry.SHORT_TASK_ID, index % 3),
            },
        ]
        boundaries.append(list(payload))
    return boundaries


def contiguous_tool_runs(items: Sequence[Any]) -> int:
    """Number of contiguous runs of tool items (message items separate runs)."""
    from context_pruner.adapters.openai_agents import _is_message_item, _item_type

    runs = 0
    in_run = False
    for item in items:
        item_type = str(_item_type(item))
        is_tool = item_type.endswith(("_call", "_output"))
        if is_tool and not in_run:
            runs += 1
            in_run = True
        elif not is_tool and not _is_message_item(item):
            # A non-tool, non-message item would break the run; count it as a boundary.
            in_run = False
        elif _is_message_item(item):
            in_run = False
    return runs


def message_item_count(items: Sequence[Any]) -> int:
    from context_pruner.adapters.openai_agents import _is_message_item

    return sum(1 for item in items if _is_message_item(item))


def tool_item_count(items: Sequence[Any]) -> int:
    from context_pruner.adapters.openai_agents import _item_type

    return sum(1 for item in items if str(_item_type(item)).endswith(("_call", "_output")))


def tool_output_count(items: Sequence[Any]) -> int:
    from context_pruner.adapters.openai_agents import _item_type

    return sum(1 for item in items if str(_item_type(item)).endswith("_output"))


def fixture_report(batch: Path | None = None, repeat: int = 0) -> dict[str, Any]:
    """Compare the rebuilt fixtures with the recorded structure of the paid batch."""
    samples = load_recorded_batch(batch)
    plugin = samples[(repeat, "pruner_v1")]
    baseline = samples[(repeat, "none")]
    boundaries = realistic_boundaries(repeat)
    checks = []
    mismatches: list[str] = []
    for index, payload in enumerate(boundaries):
        if index >= len(plugin.boundaries):
            mismatches.append(f"boundary {index}: not recorded in the paid batch")
            continue
        recorded = plugin.boundaries[index]
        observed = {
            "item_count": len(payload),
            "message_items": message_item_count(payload),
            "tool_items": tool_item_count(payload),
            "tool_outputs": tool_output_count(payload),
            "tool_runs": contiguous_tool_runs(payload),
        }
        expected = {
            "item_count": recorded.item_count,
            "message_items": recorded.message_items,
            "tool_items": recorded.tool_items,
            "tool_outputs": recorded.tool_outputs,
            "tool_runs": max(1, recorded.turn_count) if recorded.tool_items else 0,
        }
        entry = {"index": index, "observed": observed, "recorded": expected, "ok": True}
        for field_name, value in observed.items():
            if value != expected[field_name]:
                entry["ok"] = False
                mismatches.append(
                    f"boundary {index}: {field_name} fixture={value} recorded={expected[field_name]}"
                )
        checks.append(entry)
    baseline_boundaries = [
        {
            "index": boundary.index,
            "item_count": boundary.item_count,
            "message_items": boundary.message_items,
            "tool_items": boundary.tool_items,
            "tool_outputs": boundary.tool_outputs,
            "turn_count": boundary.turn_count,
        }
        for boundary in baseline.boundaries
    ]
    return {
        "batch": str((Path(batch) if batch is not None else V9_BATCH).name),
        "repeat": repeat,
        "fixture_boundaries": len(boundaries),
        "recorded_boundaries": len(plugin.boundaries),
        "checks": checks,
        "mismatches": mismatches,
        "ok": not mismatches,
        "tool_items_contiguous": all(
            entry["observed"]["tool_runs"] <= 1 for entry in checks
        ),
        "recorded_turn_count_by_boundary": [
            recorded.turn_count for recorded in plugin.boundaries
        ],
        "recorded_elidable_indices": [
            recorded.elidable_indices for recorded in plugin.boundaries
        ],
        "recorded_baseline_structure": baseline_boundaries,
        "byte_note": (
            "fixture bytes differ from the recorded input_bytes because the paid run used "
            "the chat-completions transport; the replay measures structure and payload "
            "content, not transport bytes"
        ),
    }


def recorded_structure_fingerprint(batch: Path | None = None, repeat: int = 0) -> str:
    """Digest of the recorded structure the fixture must reproduce (audit cross-check)."""
    samples = load_recorded_batch(batch)
    plugin = samples[(repeat, "pruner_v1")]
    return sha256(
        [
            {
                "index": boundary.index,
                "item_count": boundary.item_count,
                "message_items": boundary.message_items,
                "tool_outputs": boundary.tool_outputs,
                "turn_count": boundary.turn_count,
            }
            for boundary in plugin.boundaries
        ]
    )


__all__ = [
    "READ_ORDER",
    "RecordedBoundary",
    "RecordedSample",
    "TASK_ID",
    "V9_BATCH",
    "canonical",
    "contiguous_tool_runs",
    "fixture_report",
    "load_recorded_batch",
    "message_item_count",
    "realistic_boundaries",
    "recorded_structure_fingerprint",
    "sha256",
    "tool_item_count",
    "tool_output_count",
]
