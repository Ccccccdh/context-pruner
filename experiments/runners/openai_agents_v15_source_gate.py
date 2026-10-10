"""Zero-API source/contract gate before any v15 payload-acquisition request.

This projects from pinned public Git blobs. It is deliberately not a real
provider-payload replay, a token-saving measurement, or a quality result.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from experiments.runners import openai_agents_multitask_replay_gate_v14 as replay
from experiments.runners import openai_agents_v15_registry as registry
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate


OUT = registry.REPO / "integrations/openai_agents/V15_SOURCE_CONTRACT_GATE_20261006.json"
WITNESSES = {
    "django_field_error_messages_copy": "RESULT issue=django-11880 cause=Field.__deepcopy__ shares error_messages fix=deepcopy error_messages per copy",
    "django_method_decorator_partial": "RESULT issue=django-14787 cause=partial lacks __name__ fix=update_wrapper(bound_method, method)",
    "django_textchoices_string_value": "RESULT issue=django-11964 cause=TextChoices inherits Enum __str__ fix=return value in TextChoices.__str__",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pairing(items: list[dict]) -> bool:
    calls = [x["call_id"] for x in items if x.get("type") == "function_call"]
    outputs = [x["call_id"] for x in items if x.get("type") == "function_call_output"]
    return calls == outputs and len(calls) == len(set(calls))


def analyse(task_id: str) -> dict:
    entry = registry.task(task_id)
    problems = registry.verify(task_id)
    witness = WITNESSES[task_id]
    witness_checks = {
        "one_line": "\n" not in witness,
        "max_160_chars": len(witness) <= 160,
        "required_terms": all(term.lower() in witness.lower() for term in entry["required_terms"]),
        "full_regex": bool(re.fullmatch(entry["answer_pattern"], witness, flags=re.IGNORECASE)),
    }
    if not all(witness_checks.values()):
        problems.append("contract_has_no_checked_short_witness")
    texts = registry.view_texts(task_id)
    literal_evidence = {
        label: {phrase: [index for index, text in enumerate(texts) if phrase in text] for phrase in phrases}
        for label, phrases in entry["literals"].items()
    }
    if any(not indices for phrases in literal_evidence.values() for indices in phrases.values()):
        problems.append("registered_literal_missing_from_views")
    boundaries = replay.boundaries(task_id)
    if len(boundaries) != 7:
        problems.append("wrong_boundary_count")
    rows = []
    for index, payload in enumerate(boundaries):
        reduced, replacements = deduplicate(payload)
        pairs_ok = _pairing(payload) and _pairing(reduced)
        tool_structure_ok = replay.tool_group_hash(payload) == replay.tool_group_hash(reduced)
        source_digests = replay.full_copy_digests(reduced)
        distinct = {_sha(x["output"]) for x in replay.outputs(payload)}
        full_source_ok = distinct <= set(source_digests)
        pointer_ok = all(
            reduced[r["old_index"]]["output"] == replay.pointer_text(
                payload[r["new_index"]]["call_id"], _sha(payload[r["new_index"]]["output"])
            )
            and reduced[r["new_index"]]["output"] == payload[r["new_index"]]["output"]
            for r in replacements
        )
        lost = replay.lost_presence(payload, reduced, task_id)
        if not pairs_ok or not tool_structure_ok or not full_source_ok or not pointer_ok or lost:
            problems.append(f"boundary_{index}_invariant")
        rows.append({
            "index": index,
            "tool_calls": len(replay.calls(payload)),
            "output_count": len(replay.outputs(payload)),
            "baseline_bytes": replay.payload_bytes(payload),
            "plugin_projected_bytes": replay.payload_bytes(reduced),
            "exact_duplicate_replacements": len(replacements),
            "pairing_ok": pairs_ok,
            "tool_structure_ok": tool_structure_ok,
            "full_source_ok": full_source_ok,
            "pointer_ok": pointer_ok,
            "lost_presence": lost,
        })
    # A deliberately broken reduction must be rejected for losing the only
    # full copy of a unique source. This is a fail-closed negative control.
    final = boundaries[-1]
    broken, _ = deduplicate(final)
    newest = next(i for i, x in enumerate(broken) if x.get("type") == "function_call_output" and not x["output"].startswith("[Exact duplicate"))
    broken[newest] = {**broken[newest], "output": "[broken pointer]"}
    distinct = {_sha(x["output"]) for x in replay.outputs(final)}
    negative_caught = not distinct <= set(replay.full_copy_digests(broken))
    if not negative_caught:
        problems.append("negative_unique_source_not_caught")
    projected = sum(r["baseline_bytes"] - r["plugin_projected_bytes"] for r in rows) / sum(r["baseline_bytes"] for r in rows)
    if projected < 0.03 or rows[-1]["exact_duplicate_replacements"] < 1:
        problems.append("no_projected_safe_space")
    return {
        "task": task_id,
        "instance_id": entry["instance_id"],
        "fingerprint": registry.fingerprint(task_id),
        "witness_is_illustrative_not_model_output": True,
        "witness_chars": len(witness),
        "witness_checks": witness_checks,
        "registered_literal_view_indices": literal_evidence,
        "boundaries": rows,
        "projected_byte_saving_rate_not_provider_tokens": projected,
        "negative_unique_source_caught": negative_caught,
        "problems": problems,
        "source_contract_gate_pass": not problems,
    }


def main() -> None:
    replay.registry = registry
    results = {task_id: analyse(task_id) for task_id in registry.task_ids()}
    source_before = registry.source_bytes
    first_task = registry.task_ids()[0]
    def tampered_source(task_id: str, path: str) -> bytes:
        raw = source_before(task_id, path)
        return raw + b"\n# synthetic drift\n" if task_id == first_task else raw
    registry.source_bytes = tampered_source
    try:
        drift_caught = any("source_sha256" in problem for problem in registry.verify(first_task))
    finally:
        registry.source_bytes = source_before
    if not drift_caught:
        results[first_task]["problems"].append("source_drift_negative_control_not_caught")
        results[first_task]["source_contract_gate_pass"] = False
    output = {
        "schema": "openai_agents_v15_source_contract_gate",
        "paid_requests": 0,
        "registry_file_sha256": hashlib.sha256(registry.TASKS_FILE.read_bytes()).hexdigest(),
        "source_contract_gate_pass": all(result["source_contract_gate_pass"] for result in results.values()),
        "source_drift_negative_control_caught": drift_caught,
        "workload_role": "controlled six-read repeated-source diagnostic; no natural-agent or broad-efficacy claim",
        "real_payload_replay_done": False,
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "per_task": results,
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paid_requests": 0, "source_contract_gate_pass": output["source_contract_gate_pass"], "per_task": {k: {"problems": v["problems"], "projected_bytes": round(v["projected_byte_saving_rate_not_provider_tokens"], 4)} for k, v in results.items()}}))


if __name__ == "__main__":
    main()
