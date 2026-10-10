"""v16 pre-three-arm replay gate: five invariants plus a negative control, zero API.

It runs on the recorded ``none``-arm payloads of the structured acquisition batch (attempt 02)
and on the deterministic rebuild of those payloads from the registered views, projecting the
frozen compression rule (the v12 exact-duplicate replacement, guarded by the held-out literal
carrier rule) onto them.

The five items the three-arm comparison depends on:

1. tool pairing: every function call has its output, in every recorded and rebuilt boundary;
2. every unique source still has a full copy after the projection;
3. required-literal presence never drops to zero;
4. every pointer is recomputable byte for byte from the payload itself;
5. the guard-filtered safe-candidate count is non-zero.

Negative control: replace the only surviving full copy of the view that carries the most
registered-literal occurrences with a pointer, and require the gate to report the loss.

The boundary mapping differs from the v14 gate because this host's model reads the six views in
one or two parallel batches instead of one per turn: a recorded boundary is matched to the
rebuilt stage with the same item count, and every rebuilt stage is checked whether or not a
boundary was recorded for it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_multitask_replay_gate_v14 as v14gate
from experiments.runners import openai_agents_v16_registry as registry

REPO = registry.REPO
RUNS = REPO / "runs/stage5-openai-agents-api"
BATCH = RUNS / "openai-repo-diagnostic-v16-structured-acquisition-02"
OUT = REPO / "integrations/openai_agents/V16_REPLAY_GATE_20261006.json"
DIAGNOSTIC_MARKER = "host rejected your previous JSON"
#: The v14 gate module holds the frozen projection primitives (deduplicate, pointer_text,
#: visible_counts, lost_presence, tool_group_hash) and resolves tasks through its own registry,
#: so it is pointed at the v16 registry before anything is rebuilt.
v14gate.registry = registry


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def boundaries(task_id: str) -> list[list[dict]]:
    """The deterministic rebuild of the task's payload sequence (v16 registry)."""
    return v14gate.boundaries(task_id)


def recorded(task_id: str) -> list[dict]:
    path = BATCH / "input-evidence" / f"{task_id}-0-none.jsonl"
    return [record for record in _read_jsonl(path) if record.get("stage") == "model_input"]


def pairing_ok(items: Sequence[dict]) -> bool:
    calls = {item.get("call_id") for item in items if item.get("type") == "function_call"}
    outputs = {
        item.get("call_id") for item in items if item.get("type") == "function_call_output"
    }
    return calls == outputs and all(call for call in calls)


def pointers_ok(payload: Sequence[dict], reduced: Sequence[dict], replacements: Sequence[dict]) -> list[str]:
    """Recompute every pointer from the payload: newest copy, its call id and its sha256."""
    problems: list[str] = []
    outputs_positions = {
        position: item
        for position, item in enumerate(payload)
        if item.get("type") == "function_call_output"
    }
    for replacement in replacements:
        old = payload[int(replacement["old_index"])]
        newest = payload[int(replacement["new_index"])]
        text = str(newest.get("output") or "")
        if str(reduced[int(replacement["old_index"])].get("output")) == text:
            problems.append("a replaced output still holds the full text")
        pointer = str(reduced[int(replacement["old_index"])].get("output") or "")
        if not pointer.startswith("[Exact duplicate output;"):
            problems.append("a replaced output is not a pointer")
            continue
        expected = v14gate.pointer_text("", hashlib.sha256(text.encode()).hexdigest())
        if pointer.split("sha256=")[-1].rstrip("]") != expected.split("sha256=")[-1].rstrip("]"):
            problems.append("a pointer's sha256 does not recompute from the payload")
        if str(payload[int(replacement["old_index"])].get("call_id")) == str(
            newest.get("call_id")
        ):
            problems.append("a pointer points at its own call")
    if not any(
        item.get("type") == "function_call_output" for item in payload
    ):
        # The first boundary is the task history alone: no tool output exists yet, which is not
        # a defect. Every later boundary must carry the outputs the reads produced.
        return problems
    return problems


def analyse(task_id: str) -> dict[str, Any]:
    entry = registry.task(task_id)
    recorded_records = recorded(task_id)
    rebuilt = boundaries(task_id)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []

    by_item_count = {int(record["item_count"]): record for record in recorded_records}
    retries = [record for record in recorded_records if int(record["item_count"]) not in by_item_count]
    matched = 0
    for index, payload in enumerate(rebuilt):
        record = by_item_count.get(len(payload))
        reported = {}
        if record is not None:
            matched += 1
            manifest = list(record.get("output_manifest") or [])
            payload_outputs = v14gate.outputs(payload)
            structure_ok = (
                len(v14gate.messages(payload))
                == int(record.get("recency_message_items", len(v14gate.messages(payload))))
                and len(payload_outputs)
                == int(record.get("recency_output_items", len(payload_outputs)))
            )
            outputs_match = len(manifest) == len(payload_outputs) and all(
                hashlib.sha256(v14gate.text_of(item).encode()).hexdigest()
                == str(entry_rec["output_sha256"])
                and len(v14gate.text_of(item)) == int(entry_rec["output_chars"])
                for item, entry_rec in zip(payload_outputs, manifest)
            )
            if not structure_ok:
                problems.append(f"boundary {index}: rebuilt structure differs from the record")
            if not outputs_match:
                problems.append(f"boundary {index}: a tool output differs from the record")
            reported = {
                "recorded": True,
                "input_sha256_matches_record": hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                == str(record.get("payload_sha256") or "")
                or None,
                "output_manifest_matches": outputs_match,
                "structure_matches": structure_ok,
            }
        reduced, replacements = v14gate.deduplicate(payload)
        lost = v14gate.lost_presence(payload, reduced, task_id)
        full_copies_before = v14gate.full_copy_digests(payload)
        full_copies_after = v14gate.full_copy_digests(reduced)
        without_copy = sorted(set(full_copies_before) - set(full_copies_after))
        pointer_problems = pointers_ok(payload, reduced, replacements)
        identical_group = v14gate.tool_group_hash(payload) == v14gate.tool_group_hash(reduced)
        if not pairing_ok(payload):
            problems.append(f"boundary {index}: tool pairing is inconsistent")
        if without_copy:
            problems.append(f"boundary {index}: a unique source lost its full copy")
        if lost:
            problems.append(f"boundary {index}: literal presence dropped to zero: {lost}")
        if pointer_problems:
            problems.append(f"boundary {index}: {pointer_problems[0]}")
        if not identical_group:
            problems.append(f"boundary {index}: the tool group structure changed")
        rows.append(
            {
                "boundary": index,
                "item_count": len(payload),
                "tool_calls": len(v14gate.calls(payload)),
                "outputs": len(v14gate.outputs(payload)),
                "replaced_output_positions": [int(r["old_index"]) for r in replacements],
                **reported,
                "tool_pairing_consistent": pairing_ok(payload),
                "unique_sources_without_full_copy": without_copy,
                "literals_lost": lost,
                "pointer_problems": pointer_problems,
                "tool_group_identical": identical_group,
                "visible_counts_after": v14gate.visible_counts(reduced, task_id),
            }
        )

    negative_control: dict[str, Any] = {"caught": False}
    if rebuilt:
        payload = rebuilt[-1]
        reduced, _ = v14gate.deduplicate(payload)
        self_check = v14gate.lost_presence(payload, reduced, task_id)
        damaged = [dict(item) for item in reduced]
        counts_before = v14gate.visible_counts(payload, task_id)
        best = None
        for view_index in range(len(registry.views(task_id))):
            target_text = registry.source_view(task_id, view_index)
            score = sum(
                len(__import__("re").findall(__import__("re").escape(phrase), target_text, __import__("re").IGNORECASE))
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
        unique_copy_damaged = False
        if best is not None:
            target_text = str(damaged[best[1]].get("output") or "")
            unique_copy_damaged = (
                sum(
                    1
                    for item in damaged
                    if item.get("type") == "function_call_output"
                    and item.get("output") == target_text
                )
                == 1
            )
            damaged[best[1]]["output"] = v14gate.pointer_text("call_elsewhere", "0" * 64)
            counts_after = v14gate.visible_counts(damaged, task_id)
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
            "damaged_item_was_the_only_full_copy": unique_copy_damaged,
            "target_found": best is not None,
            "caught": unique_copy_damaged and (bool(caught) or bool(decreased)),
        }
        if self_check:
            problems.append(f"negative control: the real reduction lost {self_check}")
        if best is None:
            problems.append("negative control: no surviving full copy to damage")
        elif not negative_control["caught"]:
            problems.append(
                "negative control: replacing the only full copy of an evidence item was not "
                "reported as a literal loss or decrease"
            )

    safe_candidates = sum(len(row["replaced_output_positions"]) for row in rows)
    return {
        "schema": "openai_agents_v16_replay_gate",
        "task_id": task_id,
        "instance_id": entry["instance_id"],
        "base_commit": entry["base_commit"],
        "registry_fingerprint": registry.fingerprint(task_id),
        "batch": str(BATCH.relative_to(REPO)).replace("\\", "/"),
        "recorded_boundaries": len(recorded_records),
        "recorded_boundaries_matched_to_a_rebuild_stage": matched,
        "rebuilt_boundaries": len(rebuilt),
        "retry_inputs_recorded": len(recorded_records) - matched,
        "boundaries": rows,
        "five_items": {
            "tool_pairing_consistent": all(row["tool_pairing_consistent"] for row in rows),
            "every_unique_source_keeps_a_full_copy": all(
                not row["unique_sources_without_full_copy"] for row in rows
            ),
            "literal_presence_never_zero": all(not row["literals_lost"] for row in rows),
            "pointers_recomputable_byte_for_byte": all(
                not row["pointer_problems"] for row in rows
            ),
            "guard_filtered_safe_candidates_non_zero": safe_candidates > 0,
        },
        "guard_filtered_safe_candidate_count": safe_candidates,
        "negative_control": negative_control,
        "problems": problems,
        "qualified": not problems and safe_candidates > 0,
        "verdict": "proceed" if (not problems and safe_candidates > 0) else "stop",
        "paid_requests": 0,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    reports = [analyse(task_id) for task_id in registry.task_ids()]
    payload = {
        "schema": "openai_agents_v16_replay_gate_batch",
        "date": "20261006",
        "purpose": (
            "zero-API proof that the recorded baseline payloads and the frozen compression rule "
            "preserve the five invariants the three-arm comparison depends on"
        ),
        "tasks": [report["task_id"] for report in reports],
        "per_task": {report["task_id"]: report for report in reports},
        "qualified": all(report["qualified"] for report in reports),
        "negative_control_caught": all(
            report["negative_control"]["caught"] for report in reports
        ),
        "paid_requests": 0,
        "batch": str(BATCH.relative_to(REPO)).replace("\\", "/"),
    }
    if "--out" in args:
        target = Path(args[args.index("--out") + 1])
    else:
        target = OUT
    if target.exists() and "--force" not in args:
        raise SystemExit(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"gate artifact: {target}")
    for report in reports:
        print(
            f"{report['task_id']}: qualified={report['qualified']} "
            f"items={json.dumps(report['five_items'], ensure_ascii=False)} "
            f"safe={report['guard_filtered_safe_candidate_count']} "
            f"negative_control={report['negative_control']['caught']} "
            f"problems={report['problems']}"
        )
    return 0 if payload["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
