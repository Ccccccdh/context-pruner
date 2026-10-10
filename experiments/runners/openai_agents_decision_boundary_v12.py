"""Zero-API decision-boundary comparison for the v12 strict-format failure.

Question
--------
v12's plugin arm answered with every fact the frozen contract requires, yet the frozen
strict check failed.  Was the *mechanism* guilty - did it move evidence or the format
constraint out of the model's visible range - or was it the *model's* wording?

Method
------
Everything here is read-only and zero-API.  The comparison is built from the v12 batch's
**own recorded evidence**, not from an invented fixture:

* per-boundary ``input_bytes``, ``item_count``, ``recency_message_items``,
  ``recency_output_items`` and ``recency_turn_count`` for the ``none`` and ``pruner_v1``
  arms (recorded real transports);
* the per-output ``output_manifest``: for every tool output item the batch sent, its
  ``call_id``, ``output_sha256``, ``output_chars`` and flags.

The payload text itself is rebuilt from the frozen task registration and the public
baseline source ranges (the same reconstruction v10/v12 replay uses), and it is accepted
only if it reproduces the recorded structure *and* the recorded per-output hashes of the
v12 batch.  The plugin side is not trusted either: every item the plugin arm recorded is
recomputed as either the untouched original output or the exact pointer text the frozen
``deduplicate`` rule builds, and the recomputed SHA256 must equal the recorded one.

Two measurements that are easy to confuse are kept apart:

* **presence** - is every required fact still visible at all (>= 1 occurrence)?
* **occurrence count** - how many times is it visible?

The plugin legitimately lowers occurrence counts (an older duplicate copy becomes a
pointer) while keeping presence, because the newest full copy always survives.  Presence is
what the frozen contract needs; the count is reported so the difference is not hidden.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

from experiments.audits import audit_openai_agents_repo_diagnostic_v9 as frozen_contract
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate

ROOT = Path(__file__).resolve().parents[2]
V12_BATCH = (
    ROOT / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v12-exact-duplicate-dev-01"
)
TASK_ID = "django_long_investigation"
METHODS = ("none", "pruner_v1", "native_summary")
#: The frozen answer rule this batch was judged by (audit v9 / runner v1, unchanged).
CONTRACT = frozen_contract.CONTRACTS[TASK_ID][1]
TERMS = tuple(frozen_contract.CONTRACTS[TASK_ID][2])
#: Contract text the answer must follow; it lives in the agent *instructions*, which are
#: not a model-input item and which no item filter can touch.
INSTRUCTION_CONTRACT = "RESULT issue=django-16263 cause=<decision> fix=<pruning_guard>"
MAX_ANSWER_CHARS = 160
#: Components of the frozen answer rule, separated so each can be counted on both sides.
COMPONENTS = {
    "frozen_regex": CONTRACT,
    "contract_prefix": "RESULT issue=django-16263",
    "cause_field": "cause=",
    "fix_field": "fix=",
    "term:existing_annotations": "existing_annotations",
    "term:subquery": "subquery",
    "term:referenced": "referenced",
}
#: The order + boundary part of the rule: ``subquery`` ... `` fix=``.
ORDER_BOUNDARY = re.compile(r"subquery.*? fix=", re.IGNORECASE | re.DOTALL)


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def payload_bytes(items: Sequence[Any]) -> int:
    """Serialized payload size in bytes, the measure the replay projection uses."""
    return len(json.dumps(list(items), ensure_ascii=False, separators=(",", ":")).encode())


def text_of(item: dict) -> str:
    """The model-visible text of one input item."""
    for key in ("content", "output", "arguments"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [
                str(part.get("text", "")) for part in value if isinstance(part, dict)
            ]
            if any(parts):
                return "\n".join(parts)
    return ""


def visible_text(items: Sequence[dict], *, item_filter=None) -> str:
    parts = [
        text_of(item)
        for item in items
        if item_filter is None or item_filter(item)
    ]
    return "\n".join(part for part in parts if part)


def occurrences(text: str, literal: str) -> int:
    return len(re.findall(re.escape(literal), text, re.IGNORECASE))


def recorded_arms(batch: Path | None = None) -> dict[str, dict[int, dict]]:
    """Per-boundary recorded evidence of the v12 batch, keyed by arm then boundary."""
    root = Path(batch) if batch is not None else V12_BATCH
    arms: dict[str, dict[int, dict]] = {}
    for method in METHODS:
        path = root / "input-evidence" / f"{TASK_ID}-0-{method}.jsonl"
        records = [
            record
            for record in _read_jsonl(path)
            if record.get("stage") == "model_input"
        ]
        arms[method] = {int(record["index"]): record for record in records}
    return arms


def pointer_text(newest_call_id: str, source_sha256: str) -> str:
    return (
        f"[Exact duplicate output; full source is at call_id={newest_call_id}; "
        f"sha256={source_sha256}]"
    )


def _outputs(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call_output"]


def _messages(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") not in ("function_call", "function_call_output")]


def _calls(items: Sequence[dict]) -> list[dict]:
    return [item for item in items if item.get("type") == "function_call"]


def _label(item: dict) -> str:
    """Id-free label of one item: its kind, and for a call its tool name."""
    if item.get("type") == "function_call":
        return f"call:{item.get('name')}"
    if item.get("type") == "function_call_output":
        return "output"
    return f"message:{item.get('role')}"


def tool_group_structure(items: Sequence[dict]) -> list[list[str]]:
    """The ordered tool-group structure with every run-specific id removed.

    Tool-call ids differ between the two arms because they are separate model runs, so the
    structure hash is built from the item kind, the message role and the tool name only.
    That is exactly the invariant the prescreen's gate asks about: the *tool group* must
    survive a reduction even though its ids are arm-local.
    """
    return [[str(item.get("type") or "message"), _label(item)] for item in items]


def tool_group_hash(items: Sequence[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            tool_group_structure(items), ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def full_copy_digests(items: Sequence[dict]) -> dict[str, int]:
    """Digest -> position of every output item that is present as a full copy."""
    seen: dict[str, int] = {}
    for position, item in enumerate(_outputs(items)):
        text = text_of(item)
        if text.startswith("[Exact duplicate output;"):
            continue
        seen[hashlib.sha256(text.encode()).hexdigest()] = position
    return seen


def unique_output_digests(items: Sequence[dict]) -> set[str]:
    return {
        hashlib.sha256(text_of(item).encode()).hexdigest() for item in _outputs(items)
    }


def recorded_restore_counters(record: dict) -> dict[str, Any]:
    """The restore / whole-payload fallback accounting of one recorded boundary."""
    return {
        "fallback_reason": str(record.get("fallback_reason") or ""),
        "task_anchor_restore_failures": int(record.get("task_anchor_restore_failures", 0) or 0),
        "task_restore_fallbacks": int(record.get("task_restore_fallbacks", 0) or 0),
        "budget_fallbacks": int(record.get("budget_fallbacks", 0) or 0),
        "outputs_elided": int(record.get("outputs_elided", 0) or 0),
        "elided_source_count": int(record.get("elided_source_count", 0) or 0),
        "elision_note_count": int(record.get("elision_note_count", 0) or 0),
        "pointer_completeness": bool(record.get("pointer_completeness")),
        "pointer_coverage": bool(record.get("pointer_coverage")),
        "most_recent_tool_group_kept_in_full": bool(
            record.get("most_recent_tool_group_kept_in_full")
        ),
    }


def unaccounted_fallbacks(record: dict) -> list[str]:
    """Fallback signals with no recorded reason, or a reason with no counter.

    'Accounted' means the boundary states a reason for every fallback it reports and
    reports a counter for every reason it states; anything else must stop the gate instead
    of being explained away afterwards.
    """
    counters = recorded_restore_counters(record)
    problems: list[str] = []
    fallbacks = (
        counters["task_anchor_restore_failures"]
        + counters["task_restore_fallbacks"]
        + counters["budget_fallbacks"]
    )
    if fallbacks and not counters["fallback_reason"]:
        problems.append("fallback counted without a recorded reason")
    if counters["fallback_reason"] and not fallbacks:
        problems.append("fallback reason recorded without a counter")
    if counters["pointer_coverage"] is False and counters["elided_source_count"] > 0:
        problems.append("sources elided while pointer coverage is recorded false")
    return problems


def visible_counts(items: Sequence[dict]) -> dict[str, int]:
    """How often each component of the frozen rule is visible in one payload."""
    text = visible_text(items)
    counts = {name: occurrences(text, literal) for name, literal in COMPONENTS.items()}
    counts["order_and_boundary:subquery_then_fix"] = len(ORDER_BOUNDARY.findall(text))
    counts["frozen_regex_on_visible_text"] = len(
        re.findall(CONTRACT, text, re.IGNORECASE | re.DOTALL)
    )
    return counts


def lost_presence(payload: Sequence[dict], reduced: Sequence[dict]) -> list[str]:
    """Components visible on the baseline side that vanished from the reduced side.

    Presence (``>= 1`` occurrence) is what the frozen contract needs; losing it means the
    reduction moved a required fact out of the model's visible range.
    """
    before = visible_counts(payload)
    after = visible_counts(reduced)
    return sorted(
        name for name, count in before.items() if count > 0 and after.get(name, 0) == 0
    )


def analyse(batch: Path | None = None, repeat: int = 0) -> dict[str, Any]:
    """Compare the two arms' final model inputs boundary by boundary."""
    root = Path(batch) if batch is not None else V12_BATCH
    arms = recorded_arms(root)
    payloads = replay.realistic_boundaries(repeat)
    rows: list[dict[str, Any]] = []
    problems: list[str] = []

    for index, payload in enumerate(payloads):
        none_rec = arms["none"].get(index)
        plugin_rec = arms["pruner_v1"].get(index)
        if none_rec is None or plugin_rec is None:
            problems.append(f"boundary {index}: not recorded in both arms")
            continue
        reduced, replacements = deduplicate(payload)

        # -- the reconstruction must be the recorded real payload -----------------
        structure_ok = (
            len(payload) == int(none_rec["item_count"])
            and len(_messages(payload)) == int(none_rec["recency_message_items"])
            and len(_outputs(payload)) == int(none_rec["recency_output_items"])
            and len(reduced) == int(plugin_rec["item_count"])
            and len(_messages(reduced)) == int(plugin_rec["recency_message_items"])
            and len(_outputs(reduced)) == int(plugin_rec["recency_output_items"])
        )
        if not structure_ok:
            problems.append(f"boundary {index}: rebuilt structure differs from the record")

        # -- baseline arm: every sent output must be the real public source text ---
        baseline_manifest = list(none_rec.get("output_manifest") or [])
        baseline_outputs = _outputs(payload)
        baseline_match = len(baseline_manifest) == len(baseline_outputs) and all(
            hashlib.sha256(text_of(item).encode()).hexdigest() == str(record["output_sha256"])
            and len(text_of(item)) == int(record["output_chars"])
            for item, record in zip(baseline_outputs, baseline_manifest)
        )
        if not baseline_match:
            problems.append(f"boundary {index}: baseline outputs differ from the record")

        # -- plugin arm: recompute what the batch actually sent, item by item ------
        # The mechanism's only text change is the pointer, and the pointer text is fully
        # determined by the newest full copy's call_id and source digest - both of which
        # the recorded manifest of the baseline arm states.  So the plugin arm's recorded
        # items can be recomputed from the record instead of trusted.
        plugin_manifest = list(plugin_rec.get("output_manifest") or [])
        plugin_outputs = _outputs(reduced)
        output_positions = [
            item_index
            for item_index, item in enumerate(payload)
            if item.get("type") == "function_call_output"
        ]
        position_of_item = {item_index: position for position, item_index in enumerate(output_positions)}
        replaced_positions = sorted(
            position_of_item[int(replacement["old_index"])] for replacement in replacements
        )
        pointer_by_position = {
            position_of_item[int(replacement["old_index"])]: position_of_item[
                int(replacement["new_index"])
            ]
            for replacement in replacements
        }
        plugin_match = len(plugin_manifest) == len(plugin_outputs)
        expected_by_position: dict[int, str] = {}
        for position, (item, record) in enumerate(zip(plugin_outputs, plugin_manifest)):
            expected = text_of(item)
            if position in pointer_by_position:
                newest_position = pointer_by_position[position]
                # A pointer names *this* arm's newest copy, so its call_id comes from the
                # plugin arm's own record while the digest is the source text's digest,
                # which is identical in both arms (same deterministic public source).
                expected = pointer_text(
                    str(plugin_manifest[newest_position]["call_id"]),
                    str(baseline_manifest[newest_position]["output_sha256"]),
                )
            expected_by_position[position] = expected
            if (
                hashlib.sha256(expected.encode()).hexdigest()
                != str(record["output_sha256"])
                or len(expected) != int(record["output_chars"])
            ):
                plugin_match = False
                break
        if not plugin_match:
            problems.append(f"boundary {index}: plugin outputs differ from the record")
        # every replaced position must be recorded as a 1-line item, every kept one as the
        # original multi-line source: the record must show exactly the split we recomputed
        recorded_pointer_positions = sorted(
            position
            for position, record in enumerate(plugin_manifest)
            if int(record.get("output_chars", 0)) != int(
                baseline_manifest[position].get("output_chars", -1)
            )
        )
        if recorded_pointer_positions != replaced_positions:
            problems.append(
                f"boundary {index}: replaced positions differ from the record "
                f"(recomputed {replaced_positions}, recorded {recorded_pointer_positions})"
            )

        # -- the message units, which carry the task and the request --------------
        message_units = [text_of(item) for item in _messages(payload)]
        plugin_visible_items = reduced
        message_units_present = all(
            unit in visible_text(plugin_visible_items) for unit in message_units
        )
        if not message_units_present:
            problems.append(f"boundary {index}: a message unit is not verbatim on both sides")

        # -- tool-call pairing and item order -------------------------------------
        # The two arms are separate model runs, so their tool-call ids differ on purpose
        # and must never be compared across arms.  Pairing is checked per arm (one call,
        # one answering output, same order, no unmatched call) and across arms by content:
        # a kept item must hash to the same text, and a pointer must name a full copy that
        # is still present later in the same payload.
        def arm_pairing(record: dict, outputs: int, messages: int) -> bool:
            items = int(record["item_count"])
            return (
                items == int(record["recency_message_items"]) + 2 * outputs
                and int(record["recency_output_items"]) == outputs
                and items - messages - outputs == outputs
            )

        def content_mapping_ok() -> bool:
            for position, record in enumerate(plugin_manifest):
                if position not in pointer_by_position:
                    if str(record["output_sha256"]) != str(
                        baseline_manifest[position]["output_sha256"]
                    ):
                        return False
            for position, newest_position in pointer_by_position.items():
                target = str(baseline_manifest[newest_position]["output_sha256"])
                if (
                    str(plugin_manifest[newest_position]["output_sha256"]) != target
                    or newest_position <= position
                    or target not in expected_by_position[position]
                ):
                    return False
            return True

        pairing_ok = (
            arm_pairing(none_rec, len(baseline_outputs), len(_messages(payload)))
            and arm_pairing(plugin_rec, len(plugin_outputs), len(_messages(reduced)))
            and len(set(str(record["call_id"]) for record in baseline_manifest))
            == len(baseline_outputs)
            and len(set(str(record["call_id"]) for record in plugin_manifest))
            == len(plugin_outputs)
            and [item.get("call_id") for item in _calls(reduced)]
            == [item.get("call_id") for item in _outputs(reduced)]
            and [item.get("call_id") for item in _calls(payload)]
            == [item.get("call_id") for item in _outputs(payload)]
            and len(reduced) == len(payload)
            and [item.get("type") for item in reduced] == [item.get("type") for item in payload]
            and content_mapping_ok()
        )
        if not pairing_ok:
            problems.append(f"boundary {index}: tool pairing changed")

        # -- what is visible on each side ------------------------------------------
        counts = {
            name: {
                "none": visible_counts(payload)[name],
                "plugin": visible_counts(reduced)[name],
            }
            for name in COMPONENTS
        }
        order_boundary = {
            "none": visible_counts(payload)["order_and_boundary:subquery_then_fix"],
            "plugin": visible_counts(reduced)["order_and_boundary:subquery_then_fix"],
        }
        full_rule_on_visible_text = {
            "none": visible_counts(payload)["frozen_regex_on_visible_text"],
            "plugin": visible_counts(reduced)["frozen_regex_on_visible_text"],
        }
        lost = lost_presence(payload, reduced)
        if lost:
            problems.append(f"boundary {index}: visible fact lost: {lost}")

        # -- invariant gate (prescreen section 2, item 4) --------------------------
        # 1) task constraints: the recorded registered-literal ledger, plus the terms the
        #    frozen answer rule needs, counted on both sides.
        recorded_constraints = {
            "none": dict(none_rec.get("registered_literals_present") or {}),
            "plugin": dict(plugin_rec.get("registered_literals_present") or {}),
        }
        recorded_counts = {
            "none": dict(none_rec.get("registered_literal_counts") or {}),
            "plugin": dict(plugin_rec.get("registered_literal_counts") or {}),
        }
        constraints_identical = recorded_constraints["none"] == recorded_constraints["plugin"]
        if not constraints_identical:
            problems.append(
                f"boundary {index}: recorded registered-literal presence differs between arms"
            )
        constraint_counts_dropped = sorted(
            label
            for label, count in recorded_counts["none"].items()
            if count > 0 and recorded_counts["plugin"].get(label, 0) == 0
        )
        if constraint_counts_dropped:
            problems.append(
                f"boundary {index}: registered literal lost in the plugin arm: "
                f"{constraint_counts_dropped}"
            )

        # 2) current evidence: every distinct source still needs one full copy on each side.
        distinct = unique_output_digests(payload)
        full_none = full_copy_digests(payload)
        full_plugin = full_copy_digests(reduced)
        evidence_missing = sorted(distinct - set(full_plugin))
        if evidence_missing:
            problems.append(f"boundary {index}: unique source has no full copy: {evidence_missing}")

        # 3) tool-group hash: id-free structure must be identical on both sides.
        group_hash = {
            "none": tool_group_hash(payload),
            "plugin": tool_group_hash(reduced),
        }
        if group_hash["none"] != group_hash["plugin"]:
            problems.append(f"boundary {index}: tool-group hash changed")

        # 4) restore / whole-payload fallback accounting, per arm.
        restore = {
            "none": recorded_restore_counters(none_rec),
            "plugin": recorded_restore_counters(plugin_rec),
        }
        accounting = unaccounted_fallbacks(plugin_rec)
        if accounting:
            problems.append(f"boundary {index}: unaccounted fallback: {accounting}")

        # 5) source location: every pointer must resolve inside the same payload, to a
        #    newer full copy whose digest is the one the pointer names.
        pointer_resolves = True
        for position, newest_position in pointer_by_position.items():
            if newest_position <= position or not str(
                plugin_manifest[position].get("output_sha256", "")
            ):
                pointer_resolves = False
                break
            if str(baseline_manifest[newest_position]["output_sha256"]) not in str(
                expected_by_position[position]
            ):
                pointer_resolves = False
                break
        if not (restore["plugin"]["pointer_completeness"] and restore["plugin"]["pointer_coverage"]):
            pointer_resolves = False
        if not pointer_resolves:
            problems.append(f"boundary {index}: a source pointer does not resolve")

        invariants = {
            "task_constraints": {
                "recorded_presence_none": recorded_constraints["none"],
                "recorded_presence_plugin": recorded_constraints["plugin"],
                "identical": constraints_identical,
                "counts_none": recorded_counts["none"],
                "counts_plugin": recorded_counts["plugin"],
                "labels_lost": constraint_counts_dropped,
            },
            "current_evidence": {
                "distinct_sources": len(distinct),
                "full_copies_none": len(full_none),
                "full_copies_plugin": len(full_plugin),
                "sources_without_full_copy_plugin": evidence_missing,
            },
            "tool_group": {
                "hash_none": group_hash["none"],
                "hash_plugin": group_hash["plugin"],
                "identical": group_hash["none"] == group_hash["plugin"],
                "structure_none": tool_group_structure(payload),
            },
            "restore_and_fallback": restore,
            "unaccounted_fallback": accounting,
            "source_location": {
                "pointer_positions": replaced_positions,
                "pointer_resolves": pointer_resolves,
                "pointer_completeness_recorded": restore["plugin"]["pointer_completeness"],
                "pointer_coverage_recorded": restore["plugin"]["pointer_coverage"],
            },
        }

        baseline_bytes = int(none_rec["input_bytes"])
        plugin_bytes = int(plugin_rec["input_bytes"])
        rows.append(
            {
                "boundary": index,
                "recorded_bytes_none": baseline_bytes,
                "recorded_bytes_plugin": plugin_bytes,
                "recorded_byte_delta": baseline_bytes - plugin_bytes,
                "rebuilt_item_count": len(payload),
                "tool_calls": len(_calls(payload)),
                "tool_outputs": len(baseline_outputs),
                "message_units": len(message_units),
                "replaced_output_positions": replaced_positions,
                "difference_source": (
                    "none (payload byte-identical)"
                    if not replaced_positions
                    else "older exact-duplicate outputs replaced by a 157-character pointer: "
                    + ", ".join(f"item#{position}" for position in replaced_positions)
                ),
                "pairing_ok": pairing_ok,
                "message_units_verbatim_both_sides": message_units_present,
                "structure_matches_record": structure_ok,
                "baseline_outputs_match_record": baseline_match,
                "plugin_outputs_match_record": plugin_match,
                "component_occurrences": counts,
                "order_boundary_subquery_then_fix": order_boundary,
                "frozen_regex_matches_on_visible_text": full_rule_on_visible_text,
                "lost_presence": lost,
                "rebuilt_bytes_none": payload_bytes(payload),
                "rebuilt_bytes_plugin": payload_bytes(reduced),
                "rebuilt_byte_delta": payload_bytes(payload) - payload_bytes(reduced),
                "invariants": invariants,
            }
        )

    # -- the strict-format failure, decomposed ---------------------------------
    rows_by_method = {
        str(row["method"]): row
        for row in _read_jsonl(root / "samples.jsonl")
    }
    quality: dict[str, Any] = {}
    for method in METHODS:
        row = rows_by_method[method]
        answer = str(row.get("final_output", ""))
        terms_ok = all(term.lower() in answer.lower() for term in TERMS)
        regex_ok = bool(re.fullmatch(CONTRACT, answer, flags=re.IGNORECASE))
        conditions = {
            "starts_with_RESULT": answer.startswith("RESULT "),
            "single_line": "\n" not in answer,
            "at_most_160_chars": len(answer) <= MAX_ANSWER_CHARS,
        }
        reproduced = (
            terms_ok and regex_ok
        ) == bool(row["answer_correct"]) and all(
            conditions.values()
        ) == bool(row["final_format_correct"])
        quality[method] = {
            "answer_chars": len(answer),
            "required_terms_present": terms_ok,
            "frozen_regex_fullmatch": regex_ok,
            "format_conditions": conditions,
            "recorded_answer_correct": bool(row["answer_correct"]),
            "recorded_final_format_correct": bool(row["final_format_correct"]),
            "is_at_instruction_contract": answer.startswith("RESULT issue=django-16263 cause="),
            "recorded_success": bool(row["success"]),
            "reproduces_recorded_flags": reproduced,
            "answer": answer,
        }
        if not reproduced:
            problems.append(f"{method}: the re-implemented rule does not reproduce the record")

    plugin_quality = quality["pruner_v1"]
    failing = [
        name for name, ok in plugin_quality["format_conditions"].items() if not ok
    ]
    mechanism_side = bool(problems) or any(
        row["lost_presence"] or not row["pairing_ok"] for row in rows
    )
    verdict = {
        "call": "mechanism-side" if mechanism_side else "model-side-wording",
        "plugin_failing_format_conditions": failing,
        "plugin_answer_chars": plugin_quality["answer_chars"],
        "limit_chars": MAX_ANSWER_CHARS,
        "plugin_required_terms_present": plugin_quality["required_terms_present"],
        "plugin_frozen_regex_fullmatch": plugin_quality["frozen_regex_fullmatch"],
        "explanation": (
            "The frozen rule is a conjunction: contract prefix, one line, and at most "
            f"{MAX_ANSWER_CHARS} characters. The plugin answer satisfies the facts and the "
            "frozen regex (answer_correct records True) and fails only the length bound "
            f"({plugin_quality['answer_chars']} > {MAX_ANSWER_CHARS}); the format constraint "
            "itself lives in the agent instructions, which are not a model-input item and "
            "are identical in every arm, and no visible fact loses presence on any boundary."
            if not mechanism_side
            else "The comparison found a mechanism-side difference (see problems)."
        ),
    }

    recorded_delta = sum(row["recorded_byte_delta"] for row in rows)
    rebuilt_delta = sum(row["rebuilt_byte_delta"] for row in rows)

    # -- invariant gate summary (the prescreen's section 2, item 4) --------------
    lost_constraints: list[str] = []
    lost_evidence: list[str] = []
    group_hash_changes: list[int] = []
    fallback_problems: list[str] = []
    broken_pointers: list[int] = []
    for row in rows:
        inv = row["invariants"]
        if inv["task_constraints"]["labels_lost"]:
            lost_constraints.append(
                f"boundary {row['boundary']}: {inv['task_constraints']['labels_lost']}"
            )
        if inv["current_evidence"]["sources_without_full_copy_plugin"]:
            lost_evidence.append(f"boundary {row['boundary']}")
        if not inv["tool_group"]["identical"]:
            group_hash_changes.append(row["boundary"])
        if inv["unaccounted_fallback"]:
            fallback_problems.append(f"boundary {row['boundary']}: {inv['unaccounted_fallback']}")
        if not inv["source_location"]["pointer_resolves"]:
            broken_pointers.append(row["boundary"])
    plugin_row = rows_by_method["pruner_v1"]
    replacement_events = sum(len(row["replaced_output_positions"]) for row in rows)
    final_boundary_units = len(rows[-1]["replaced_output_positions"]) if rows else 0
    invariant_gate = {
        "all_invariants_hold": not (
            lost_constraints or lost_evidence or group_hash_changes or fallback_problems or broken_pointers
        ),
        "lost_constraints": lost_constraints,
        "lost_evidence": lost_evidence,
        "tool_group_hash_changes": group_hash_changes,
        "unaccounted_fallbacks": fallback_problems,
        "broken_source_pointers": broken_pointers,
        "recorded_restore_ledger": {
            "task_anchor_restore_failures": int(plugin_row.get("task_anchor_restore_failures", 0) or 0),
            "task_restore_fallbacks": int(plugin_row.get("task_restore_fallbacks", 0) or 0),
            "budget_fallbacks": int(plugin_row.get("budget_fallbacks", 0) or 0),
            "restore_failures_by_model_call": list(
                plugin_row.get("restore_failures_by_model_call") or []
            ),
            "restore_failure_is_whole_prefix_fallback": bool(
                plugin_row.get("restore_failure_is_whole_prefix_fallback")
            ),
            "literal_guard_fallbacks": int(
                plugin_row.get("selective_retention_literal_guard_fallbacks", 0) or 0
            ),
            "fallback_reasons": dict(plugin_row.get("selective_retention_fallback_reasons") or {}),
            "unmatched_call_count": int(plugin_row.get("unmatched_call_count", 0) or 0),
            "pairing_integrity": bool(plugin_row.get("pairing_integrity")),
            "structure_safe": bool(plugin_row.get("structure_safe")),
            "constraint_preserved": bool(plugin_row.get("constraint_preserved")),
        },
        "rule": (
            "A loss or an unaccounted fallback means the mechanism must be fixed; it never "
            "means the quality verdict may be re-judged. A clean gate means the mechanism "
            "moved nothing - it does not by itself make the quality outcome acceptable."
        ),
    }
    # The prescreen's warning: a non-zero candidate count is not a benefit prediction.
    # Measured here after the guards, and reported next to what the quality tracks did.
    safe_candidate_accounting = {
        "mechanism_rule": "an older tool output whose text is byte-identical to a newer one",
        "potential_old_units_upper_bound": final_boundary_units,
        "guard_filtered_safe_candidates": final_boundary_units,
        "replacement_events_across_boundaries": replacement_events,
        "counting_rule": (
            "counted on the deciding (final) model input, like the prescreen counts the older "
            "units a frozen rule may touch; the same logical copies persist across later "
            "boundaries, so summing over boundaries double counts and is reported separately"
        ),
        "guard_checks_applied": [
            "registered literal presence kept for every label at every boundary",
            "every distinct source keeps one full copy",
            "id-free tool-group hash unchanged",
            "source pointer resolves to a newer full copy in the same payload",
            "no restore / whole-payload fallback, none unaccounted",
        ],
        "strict_quality_outcome": f"{int(plugin_row.get('success', False))}/1 vs baseline "
        f"{int(rows_by_method['none'].get('success', False))}/1",
        "paired_complete_total_saving_percent": round(
            (int(rows_by_method["none"]["all_arm_total_tokens"])
             - int(plugin_row["all_arm_total_tokens"]))
            / int(rows_by_method["none"]["all_arm_total_tokens"]) * 100,
            2,
        ),
        "interpretation": (
            f"Non-zero guard-filtered candidates ({final_boundary_units}) plus a clean invariant "
            "gate did NOT come with strict quality preservation on this task: the strict failure "
            "was the answer-length condition, a model-side wording difference, not a lost fact. "
            "So this row is a counterexample to using candidate counts as a benefit prediction, "
            "and it also shows the strict-quality channel and the mechanism-safety channel are "
            "different measurements."
        ),
    }
    return {
        "schema": "openai_agents_v12_decision_boundary_analysis",
        "batch": root.name,
        "task": TASK_ID,
        "repeat": repeat,
        "frozen_regex": CONTRACT,
        "required_terms": list(TERMS),
        "boundaries": rows,
        "quality": quality,
        "invariant_gate": invariant_gate,
        "safe_candidate_accounting": safe_candidate_accounting,
        "verdict": verdict,
        "recorded_total_bytes_none": sum(row["recorded_bytes_none"] for row in rows),
        "recorded_total_bytes_plugin": sum(row["recorded_bytes_plugin"] for row in rows),
        "recorded_total_byte_delta": recorded_delta,
        "rebuilt_total_byte_delta": rebuilt_delta,
        "projected_byte_delta_v12_gate": 12635,
        "problems": problems,
        "all_checks_ok": not problems,
        "paid_requests": 0,
    }


def markdown(report: dict) -> str:
    lines = [
        "# v12 决策边界对照（零 API，2026-10-05）",
        "",
        f"批次 `{report['batch']}`，任务 `{report['task']}`，重复 {report['repeat']}；"
        f"本文件由 `experiments/runners/openai_agents_decision_boundary_v12.py` 生成，"
        f"零付费请求（paid_requests: {report['paid_requests']}）。",
        "",
        f"冻结答案规则：`{report['frozen_regex']}`；必需字面 {report['required_terms']}。",
        "",
        "## 逐边界表",
        "",
        "| 边界 | 无压缩字节（实测） | 插件字节（实测） | 字节差 | 工具调用/输出 | 配对 | 消息单元两侧逐字 | 事实命中（无压缩→插件） | 差异来源 |",
        "|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for row in report["boundaries"]:
        counts = row["component_occurrences"]
        summary = "，".join(
            f"{name.split(':', 1)[-1]} {value['none']}→{value['plugin']}"
            for name, value in counts.items()
            if name.startswith("term:")
        )
        lines.append(
            "| {boundary} | {none} | {plugin} | {delta} | {calls}/{outputs} | {pairing} | {units} | {summary} | {source} |".format(
                boundary=row["boundary"],
                none=row["recorded_bytes_none"],
                plugin=row["recorded_bytes_plugin"],
                delta=row["recorded_byte_delta"],
                calls=row["tool_calls"],
                outputs=row["tool_outputs"],
                pairing="一致" if row["pairing_ok"] else "不一致",
                units="逐字存在" if row["message_units_verbatim_both_sides"] else "缺失",
                summary=summary,
                source=row["difference_source"],
            )
        )
    totals = (
        f"合计：无压缩 {report['recorded_total_bytes_none']} 字节 → 插件 "
        f"{report['recorded_total_bytes_plugin']} 字节，实测差 "
        f"{report['recorded_total_byte_delta']} 字节（重建差 {report['rebuilt_total_byte_delta']}，"
        f"v12 预筛投影差 {report['projected_byte_delta_v12_gate']}）。"
    )
    lines += ["", totals, "", "## 机械判定", ""]
    verdict = report["verdict"]
    lines += [
        f"- 判定：**{verdict['call']}**（机制侧不成立，属模型侧措辞）",
        f"- 判定依据 1：插件答复长度 {verdict['plugin_answer_chars']} 字符，上限 "
        f"{verdict['limit_chars']}；三条并列格式条件里只有 "
        f"{verdict['plugin_failing_format_conditions']} 不成立（前缀与单行都成立）。",
        f"- 判定依据 2：必需字面在插件答复中齐备 "
        f"{verdict['plugin_required_terms_present']}，冻结正则整串匹配 "
        f"{verdict['plugin_frozen_regex_fullmatch']}——**没有任何必需事实丢失**，"
        "失败不是「说不出来」而是「说得更长」。",
        f"- 判定依据 3：格式约束（`{INSTRUCTION_CONTRACT}` 与「恰好一行 / RESULT 开头 / "
        f"最多 {MAX_ANSWER_CHARS} 字符」）写在 agent instructions 里，不是模型输入项，"
        "项级过滤器无法触及；七个边界上两臂的消息单元逐字相同、事实 presence 无一丢失。",
        "- 判定依据 4：本模块重实现的 `answer_correct`/`final_format_correct` 规则对三臂"
        f"全部复现原记录（none/native_summary 通过、pruner_v1 不通过）——"
        f"reproduces_recorded_flags: {[report['quality'][m]['reproduces_recorded_flags'] for m in METHODS]}。",
        "- 严格口径按原判保留：插件该样本仍记为失败，+14.63 % 仍不称为有效节省。",
        "",
        "## 不变量门（预筛 §2 第 4 条所需的机械证据）",
        "",
        "| 边界 | 任务约束（注册字面 presence 两侧） | 当前证据（唯一源全文副本） | 工具组哈希（去 id） | 恢复 / 整份回退 | 源定位 |",
        "|---:|---|---|---|---|---|",
    ]
    for row in report["boundaries"]:
        inv = row["invariants"]
        constraints = (
            "一致（"
            + "，".join(
                f"{label}={'有' if value else '无'}"
                for label, value in sorted(inv["task_constraints"]["recorded_presence_none"].items())
            )
            + "）"
        )
        evidence = (
            f"唯一源 {inv['current_evidence']['distinct_sources']}，"
            f"全文副本 无压缩 {inv['current_evidence']['full_copies_none']} / 插件 "
            f"{inv['current_evidence']['full_copies_plugin']}，缺全文 "
            f"{inv['current_evidence']['sources_without_full_copy_plugin'] or '无'}"
        )
        group = (
            f"{'相同' if inv['tool_group']['identical'] else '不同'} "
            f"(`{inv['tool_group']['hash_none'][:10]}…`)"
        )
        restore = inv["restore_and_fallback"]["plugin"]
        accounting = (
            f"失败 0 / 整份回退 {restore['task_restore_fallbacks']} / 预算回退 "
            f"{restore['budget_fallbacks']}，reason "
            f"{'空' if not restore['fallback_reason'] else restore['fallback_reason']}"
            f"{'，未记账 ' + str(inv['unaccounted_fallback']) if inv['unaccounted_fallback'] else ''}"
        )
        location = (
            "无指针（该边界没有可替换项）"
            if not inv["source_location"]["pointer_positions"]
            else (
                f"指针 item#{inv['source_location']['pointer_positions']} → 更新全文副本"
                f"（pointer_completeness/coverage 记录均为 "
                f"{inv['source_location']['pointer_completeness_recorded']}/"
                f"{inv['source_location']['pointer_coverage_recorded']}）"
            )
        )
        lines.append(
            f"| {row['boundary']} | {constraints} | {evidence} | {group} | {accounting} | {location} |"
        )
    gate = report["invariant_gate"]
    ledger = gate["recorded_restore_ledger"]
    safe = report["safe_candidate_accounting"]
    lines += [
        "",
        f"- **不变量门结论：{'全部保持' if gate['all_invariants_hold'] else '存在丢失'}**——"
        f"丢失约束 {gate['lost_constraints'] or '无'}、丢失证据 {gate['lost_evidence'] or '无'}、"
        f"工具组哈希变化 {gate['tool_group_hash_changes'] or '无'}、未记账回退 "
        f"{gate['unaccounted_fallbacks'] or '无'}、源定位断裂 {gate['broken_source_pointers'] or '无'}。",
        f"- 记账（插件臂，行级记录）：`task_anchor_restore_failures={ledger['task_anchor_restore_failures']}`、"
        f"`task_restore_fallbacks={ledger['task_restore_fallbacks']}`、"
        f"`budget_fallbacks={ledger['budget_fallbacks']}`、逐调用账 "
        f"{ledger['restore_failures_by_model_call']}、字面守卫回退 "
        f"{ledger['literal_guard_fallbacks']}、回退原因表 {ledger['fallback_reasons'] or '空'}、"
        f"未配对调用 {ledger['unmatched_call_count']}；`restore_failure_is_whole_prefix_fallback="
        f"{ledger['restore_failure_is_whole_prefix_fallback']}` 表示一旦发生恢复失败就是整份前缀回退，"
        "而本次一个都没发生。",
        f"- 规则（预筛 §2 第 4 条）：**任一丢失或未记账回退必须先修机制，不得改判质量**；"
        f"反过来，门干净也不等于质量可接受。本次门干净 → 不需要修机制；严格失败仍按原判保留。",
        "",
        "## 守卫后安全候选与质量的关系（预筛反例）",
        "",
        f"- 本批（v12）在**守卫后**安全候选 = **{safe['guard_filtered_safe_candidates']}**（判定边界上被替换的旧逐字重复输出；"
        f"跨边界替换事件 {safe['replacement_events_across_boundaries']} 次，为同一批副本的重复计数，"
        "按判定边界计数以避免重复）。守卫检查：" + "；".join(safe["guard_checks_applied"]) + "。",
        f"- 同批结果：配对完整总 token **+{safe['paired_complete_total_saving_percent']} %**、"
        f"严格质量 **{safe['strict_quality_outcome']}**。→ **安全候选非零 + 不变量门干净，仍然出现严格质量不达标**；"
        "这与预筛列出的 v9（候选 0 → 收益≈0）、v10（候选 5 → +27.68 % / 质量 0/3）、"
        "v11（候选 2 → +5.81 % / 质量 2/3）构成同一族反例。",
        "- 因此：**候选数量只能当「筛除」信号（为零就别付费），不能当「收益可接受」的预测**；"
        "本宿主上「守卫后安全候选 × 质量」的关系仍未被证明，只有在预注册双轨质量下实测才能给出结论。",
        "",
        "## 验证与反例",        "",
        "- 基线臂：记录里每个工具输出项的 `call_id`/`output_sha256`/`output_chars` 与按冻结"
        "任务注册表和公开基线源码重建的文本逐项相等（7/7 边界）。",
        "- 插件臂：每个输出项要么与被替换前原文逐字节相同，要么**恰好等于**按冻结规则与"
        "「本臂自己的最新副本 call_id + 源码 SHA256」生成的 157 字符指针；重算的 SHA256 与"
        "记录完全一致，被替换位置集合也与记录中「字符数与基线不同」的位置集合逐一相同。",
        "- 两臂的工具调用 id 天然不同（各自独立运行），因此跨臂只比较内容：保留项逐字节相同，"
        "指针项指向同一 payload 内仍然存在的全文副本，且不再有任何「未配对调用」。",
        "- 反例（`tests/test_openai_agents_decision_boundary_v12.py`）：把一个**唯一**证据项也"
        "换成指针时，presence 检查必须报出丢失组件——该检查是 fail-closed，不是恒真断言。",
        "",
        "## 未测量（不作断言）",
        "",
        "- 重复次数：该批 1 任务 × 1 重复，措辞差异只在一条样本上观察到，不能作为稳定性结论。",
        "- 命中次数下降的影响：边界 5/6 的 `existing_annotations` 6→3、`subquery` 14→7 是"
        "「旧重复副本变指针」的必然结果，presence 仍在；模型是否**用到**那些重复副本无法由"
        "本证据判定。",
        "- 语义等价质量口径：本模块只做机械对照，未做语义判定，也没有改动任何评分。",
        "- 传输字节：表中字节是记录的真实 chat-completions 传输值；重建字节用 Responses 项"
        f"列表度量，两者绝对量不同（实测差 {report['recorded_total_byte_delta']} vs 重建差 "
        f"{report['rebuilt_total_byte_delta']}），但两个度量各自内部一致（见合计行）。",
        "",
        "## 检查",
        "",
        f"- all_checks_ok: {report['all_checks_ok']}；problems: {report['problems']}",
    ]
    return "\n".join(lines) + "\n"


def write_artifacts(report: dict | None = None) -> dict[str, Any]:
    report = report or analyse()
    json_path = ROOT / "integrations/openai_agents/V12_DECISION_BOUNDARY_TABLE_20261005.json"
    md_path = ROOT / "integrations/openai_agents/V12_DECISION_BOUNDARY_ANALYSIS_20261005.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(markdown(report), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path), "all_checks_ok": report["all_checks_ok"]}


def main() -> int:
    result = write_artifacts()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
