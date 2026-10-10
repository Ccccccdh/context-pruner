"""Independent accounting checks for a future v15 strict-format pilot.

This auditor does not relax or re-score the frozen task answer pattern. It
checks the new repair ledger against provider usage and refuses source drift.
The existing v14 audit remains the source for replay and evidence invariants;
this module must be combined with a v15 task-specific replay audit before pay.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


METHODS = {"none", "pruner_v1", "native_summary"}


def verify_sources(root: Path, declared: dict[str, str]) -> list[str]:
    problems: list[str] = []
    root = root.resolve()
    for name, expected in declared.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            problems.append(f"source_path_escape:{name}")
            continue
        if not path.is_file():
            problems.append(f"source_missing:{name}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"source_drift:{name}")
    return problems


def audit_rows(rows: Iterable[dict[str, Any]], *, max_repair_calls: int = 1, evidence_root: Path | None = None, required_methods: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    rows = list(rows)
    required_methods = METHODS if required_methods is None else set(required_methods)
    groups: dict[tuple[str, int], set[str]] = {}
    for index, row in enumerate(rows):
        prefix = f"row[{index}]"
        task = str(row.get("scenario", ""))
        repeat = int(row.get("repeat", -1))
        method = str(row.get("method", ""))
        if not task or method not in METHODS:
            problems.append(f"{prefix}:bad_task_or_method")
        groups.setdefault((task, repeat), set()).add(method)
        if row.get("bounded_final_schema") != "all_arm_one_retry_v15":
            problems.append(f"{prefix}:wrapper_not_installed")
        if row.get("bounded_final_usage_in_recorded_model_totals") is not True:
            problems.append(f"{prefix}:repair_usage_not_recorded")
        ledger = row.get("bounded_final_repair_ledger")
        if not isinstance(ledger, list):
            problems.append(f"{prefix}:ledger_missing")
            continue
        n = row.get("bounded_final_repair_calls")
        if n != len(ledger) or len(ledger) > max_repair_calls:
            problems.append(f"{prefix}:repair_cap_or_count")
        inputs = row.get("actual_input_tokens_by_call", [])
        outputs = row.get("actual_output_tokens_by_call", [])
        if not isinstance(inputs, list) or not isinstance(outputs, list) or len(inputs) != len(outputs):
            problems.append(f"{prefix}:provider_arrays")
            continue
        if sum(inputs) != row.get("actual_input_tokens") or sum(outputs) != row.get("actual_output_tokens"):
            problems.append(f"{prefix}:provider_token_sum")
        if len(inputs) != row.get("model_calls"):
            problems.append(f"{prefix}:model_call_count")
        if row.get("api_request_attempts", -1) < len(inputs) + int(row.get("summary_calls", 0)):
            problems.append(f"{prefix}:request_attempt_undercount")
        if row.get("all_arm_total_tokens") != (
            row.get("actual_input_tokens", 0) + row.get("actual_output_tokens", 0)
            + row.get("summary_input_tokens", 0) + row.get("summary_output_tokens", 0)
        ):
            problems.append(f"{prefix}:all_arm_token_sum")
        for entry in ledger:
            if not entry.get("attempted") or entry.get("repair_tool_count") != 0:
                problems.append(f"{prefix}:repair_not_bounded_or_tool_free")
            if not isinstance(entry.get("repair_input_sha256"), str) or len(entry["repair_input_sha256"]) != 64:
                problems.append(f"{prefix}:repair_input_not_hashed")
            usage = entry.get("repair_usage")
            if usage is None:
                # A transport failure has no provider usage; the request must
                # still appear in api_request_attempts above.
                if not entry.get("error_type"):
                    problems.append(f"{prefix}:missing_repair_usage_or_error")
            elif not inputs or inputs[-1] != usage.get("input_tokens") or outputs[-1] != usage.get("output_tokens"):
                problems.append(f"{prefix}:repair_usage_missing_from_model_totals")
        if evidence_root is not None:
            root = evidence_root.resolve()
            rel = row.get("bounded_final_repair_evidence_file", "")
            path = (root / rel).resolve() if isinstance(rel, str) else root.parent
            if not rel or not path.is_relative_to(root) or not path.is_file():
                problems.append(f"{prefix}:repair_evidence_missing_or_escape")
            else:
                try:
                    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
                except (OSError, ValueError):
                    records = []
                    problems.append(f"{prefix}:repair_evidence_unreadable")
                if len(records) != len(ledger):
                    problems.append(f"{prefix}:repair_evidence_count")
                for entry, record in zip(ledger, records):
                    items = record.get("input_items")
                    if not isinstance(items, list) or len(items) < 2:
                        problems.append(f"{prefix}:repair_input_invalid")
                        continue
                    calculated = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
                    if calculated != entry.get("repair_input_sha256") or calculated != record.get("input_sha256"):
                        problems.append(f"{prefix}:repair_input_hash_mismatch")
                    original = record.get("original_answer")
                    if not isinstance(original, str) or hashlib.sha256(original.encode("utf-8")).hexdigest() != entry.get("trigger_answer_sha256"):
                        problems.append(f"{prefix}:trigger_answer_hash_mismatch")
                    if items[-2] != {"role": "assistant", "content": original}:
                        problems.append(f"{prefix}:repair_input_missing_trigger_answer")
                    if not isinstance(items[-1], dict) or items[-1].get("role") != "user" or "160" not in str(items[-1].get("content", "")):
                        problems.append(f"{prefix}:repair_instruction_missing")
                    revised = record.get("repair_answer")
                    if revised is not None and (
                        not isinstance(revised, str) or hashlib.sha256(revised.encode("utf-8")).hexdigest() != entry.get("repair_answer_sha256")
                    ):
                        problems.append(f"{prefix}:repair_answer_hash_mismatch")
    for key, methods in groups.items():
        if methods != required_methods:
            problems.append(f"incomplete_three_arm_group:{key}")
    return problems
