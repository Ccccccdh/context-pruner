from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from experiments.audits.audit_openai_agents_strict_v15 import audit_rows, verify_sources
from experiments.runners.openai_agents_bounded_final_v15 import BoundedFinalModel


def _response(text: str, inp: int = 10, out: int = 5):
    return SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])],
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out),
    )


class FakeMeteredModel:
    def __init__(self, responses):
        self.pending = list(responses)
        self.calls = []
        self.responses = []
        self.inputs = []
        self.instructions = []
        self.retry_count = 0

    async def get_response(self, system_instructions, input, settings, tools, schema, handoffs, tracing, **kwargs):
        self.calls.append((list(input), list(tools)))
        self.inputs.append(list(input))
        self.instructions.append(system_instructions)
        result = self.pending.pop(0)
        self.responses.append(result)
        return result


def _call(model):
    return asyncio.run(model.get_response("system", [{"role": "user", "content": "issue=x"}], None, ["tool"], None, None, None, previous_response_id=None, conversation_id=None, prompt=None))


def test_all_arm_wrapper_counts_one_repair_and_disables_tools():
    original = "RESULT issue=x cause=" + "long " * 35 + "fix=keep facts"
    revised = "RESULT issue=x cause=long fix=keep facts"
    inner = FakeMeteredModel([_response(original), _response(revised, 20, 4)])
    model = BoundedFinalModel(inner)
    assert _call(model) is inner.responses[1]
    assert len(inner.responses) == len(inner.inputs) == 2
    assert inner.calls[1][1] == []
    assert model.repair_ledger[0]["repair_usage"] == {"input_tokens": 20, "output_tokens": 4}
    assert model.repair_ledger[0]["accepted_format"] is True


def test_invalid_repair_returns_original_and_still_counts_request():
    original = "RESULT issue=x cause=" + "long " * 35 + "fix=keep facts"
    inner = FakeMeteredModel([_response(original), _response("bad reply")])
    model = BoundedFinalModel(inner)
    assert _call(model) is inner.responses[0]
    assert len(inner.responses) == 2
    assert model.repair_ledger[0]["accepted_format"] is False


def test_tool_call_and_short_final_do_not_retry():
    tool = SimpleNamespace(output=[SimpleNamespace(type="function_call")], usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    inner = FakeMeteredModel([tool, _response("RESULT issue=x cause=a fix=b")])
    model = BoundedFinalModel(inner)
    assert _call(model) is tool
    assert _call(model) is inner.responses[1]
    assert model.repair_ledger == []


def _row(method: str):
    return {
        "scenario": "task", "repeat": 0, "method": method,
        "bounded_final_schema": "all_arm_one_retry_v15",
        "bounded_final_usage_in_recorded_model_totals": True,
        "bounded_final_repair_calls": 1,
        "bounded_final_repair_ledger": [{
            "attempted": True, "repair_tool_count": 0,
            "repair_input_sha256": "a" * 64,
            "repair_usage": {"input_tokens": 20, "output_tokens": 4},
        }],
        "actual_input_tokens_by_call": [10, 20],
        "actual_output_tokens_by_call": [5, 4],
        "actual_input_tokens": 30, "actual_output_tokens": 9,
        "model_calls": 2, "summary_calls": 0, "api_request_attempts": 2,
        "summary_input_tokens": 0, "summary_output_tokens": 0,
        "all_arm_total_tokens": 39,
    }


def test_independent_auditor_positive_and_negative_controls():
    rows = [_row(method) for method in ("none", "pruner_v1", "native_summary")]
    assert audit_rows(rows) == []
    bad = copy.deepcopy(rows)
    bad[1]["actual_input_tokens"] -= 1
    assert any("provider_token_sum" in p for p in audit_rows(bad))
    bad = copy.deepcopy(rows)
    bad[2]["bounded_final_repair_ledger"][0]["repair_usage"]["input_tokens"] -= 1
    assert any("repair_usage_missing" in p for p in audit_rows(bad))
    bad = copy.deepcopy(rows)
    bad[0]["api_request_attempts"] = 1
    assert any("request_attempt_undercount" in p for p in audit_rows(bad))
    assert any("incomplete_three_arm_group" in p for p in audit_rows(rows[:2]))


def test_source_drift_and_escape_controls(tmp_path: Path):
    path = tmp_path / "frozen.py"
    path.write_text("safe\n", encoding="utf-8")
    good = hashlib.sha256(path.read_bytes()).hexdigest()
    assert verify_sources(tmp_path, {"frozen.py": good}) == []
    path.write_text("drift\n", encoding="utf-8")
    assert verify_sources(tmp_path, {"frozen.py": good}) == ["source_drift:frozen.py"]
    assert any("source_path_escape" in x for x in verify_sources(tmp_path, {"../escape": good}))


def test_repair_input_evidence_hash_negative_control(tmp_path: Path):
    rows = [_row(method) for method in ("none", "pruner_v1", "native_summary")]
    original = "RESULT issue=x cause=" + "long " * 35 + "fix=keep facts"
    items = [
        {"role": "user", "content": "issue=x"},
        {"role": "assistant", "content": original},
        {"role": "user", "content": "Rewrite to 160 characters"},
    ]
    digest = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    for row in rows:
        rel = f"{row['method']}.jsonl"
        row["bounded_final_repair_evidence_file"] = rel
        entry = row["bounded_final_repair_ledger"][0]
        entry["repair_input_sha256"] = digest
        entry["trigger_answer_sha256"] = hashlib.sha256(original.encode("utf-8")).hexdigest()
        (tmp_path / rel).write_text(json.dumps({"input_items": items, "input_sha256": digest, "original_answer": original, "repair_answer": None}) + "\n", encoding="utf-8")
    assert audit_rows(rows, evidence_root=tmp_path) == []
    path = tmp_path / "pruner_v1.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["input_items"][0]["content"] = "tampered"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert any("repair_input_hash_mismatch" in p for p in audit_rows(rows, evidence_root=tmp_path))
