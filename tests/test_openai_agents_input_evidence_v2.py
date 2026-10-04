"""Zero-API noninterference and privacy gate for v2 evidence capture."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.run import CallModelData, ModelInputData
from openai.types.responses import ResponseFunctionToolCall

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter
from experiments.runners.openai_agents_input_evidence_v2 import (
    EvidenceCaptureFilter,
    EvidenceModelProxy,
    InputEvidenceRecorder,
    snapshot,
)
from experiments.runners.trigger_gate import BudgetTriggeredFilter
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as candidate


def call_data(items):
    return CallModelData(
        model_data=ModelInputData(input=items, instructions="Use public source only."),
        agent=Agent(name="Offline evidence gate"),
        context=None,
    )


def items():
    history = [{"role": "user", "content": "Django issue: prune when not referenced by filters, other annotations, or ordering."}]
    for i in range(8):
        history.extend([
            {"role": "assistant", "content": f"public prior analysis {i}: " + "ordinary detail " * 20},
            {"role": "user", "content": f"public follow-up {i}: " + "ordinary detail " * 15},
        ])
    history.extend([
        {"type": "function_call", "name": "read_django_aggregation", "call_id": "call_1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "existing_annotations force subquery; TOPSECRET_TEST_SENTINEL"},
    ])
    return history


def adapter():
    inner = OpenAIAgentsContextFilter(
        ContextPluginConfig(budget=ContextBudget(750, 2000, 600)),
        task_state="Django count annotation diagnosis",
        fixed_reserved_tokens=60,
    )
    return BudgetTriggeredFilter(inner, soft_limit_tokens=500)


class InputEvidenceGate(unittest.TestCase):
    def test_capture_returns_exact_same_sdk_messages(self):
        source = items()
        direct = asyncio.run(adapter()(call_data(source)))
        recorder = InputEvidenceRecorder("django_count_annotations")
        observed = asyncio.run(EvidenceCaptureFilter(adapter(), recorder)(call_data(items())))
        self.assertEqual(direct.input, observed.input)
        self.assertEqual(direct.instructions, observed.instructions)
        self.assertEqual(2, len(recorder.records))
        before, after = recorder.records
        self.assertNotEqual(before["input_sha256"], after["input_sha256"])
        self.assertTrue(before["constraint_present"]["ordering_references"])
        self.assertTrue(before["constraint_present_in_unprotected_messages"]["ordering_references"])
        self.assertTrue(before["constraint_present_in_protected_groups"]["aggregation_decision"])
        self.assertEqual(before["protected_groups"], after["protected_groups"])
        self.assertEqual(1, before["protected_groups"][0]["tool_output_count"])
        recorder.observe_model_input(observed.input, observed.instructions)
        self.assertEqual(after["input_sha256"], recorder.records[-1]["input_sha256"])

    def test_saved_evidence_has_no_message_or_secret_text(self):
        recorder = InputEvidenceRecorder("django_count_annotations")
        recorder.observe_model_input(items(), "Use public source only.")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.jsonl"
            recorder.save(path)
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("TOPSECRET_TEST_SENTINEL", content)
            self.assertNotIn("existing_annotations force", content)
            self.assertNotIn("Use public source only", content)
            parsed = json.loads(content.strip())
            self.assertTrue(parsed["constraint_present"]["aggregation_decision"])
            self.assertEqual(64, len(parsed["input_sha256"]))

    def test_constraint_presence_detects_missing_rule_without_storing_it(self):
        full = snapshot("django_count_annotations", items(), None, "model_input", 0)
        missing = snapshot("django_count_annotations", [{"role": "user", "content": "Django count issue"}], None, "model_input", 1)
        self.assertTrue(full["constraint_present"]["ordering_references"])
        self.assertFalse(missing["constraint_present"]["ordering_references"])

    def test_wrapper_returns_exact_inner_object(self):
        sentinel = ModelInputData(input=[{"role": "user", "content": "done"}], instructions="same")

        class Inner:
            def __call__(self, data):
                return sentinel

        wrapped = EvidenceCaptureFilter(Inner(), InputEvidenceRecorder("django_count_annotations"))
        self.assertIs(sentinel, asyncio.run(wrapped(call_data(items()))))

    def test_model_proxy_preserves_provider_arguments_and_response(self):
        response = object()
        received = []

        class Inner:
            async def get_response(self, *args, **kwargs):
                received.append((args, kwargs))
                return response

        recorder = InputEvidenceRecorder("django_count_annotations")
        proxy = EvidenceModelProxy(Inner(), recorder)
        settings = object()
        tools = object()
        source = items()
        result = asyncio.run(proxy.get_response(
            "Use public source only.", source, settings, tools, None, [], None,
            previous_response_id=None, conversation_id=None, prompt=None,
        ))
        self.assertIs(response, result)
        self.assertIs(source, received[0][0][1])
        self.assertIs(settings, received[0][0][2])
        self.assertIs(tools, received[0][0][3])
        self.assertEqual("model_input", recorder.records[0]["stage"])
        self.assertEqual(snapshot("django_count_annotations", source, "Use public source only.", "model_input", 0)["input_sha256"], recorder.records[0]["input_sha256"])

    def test_full_sdk_runner_sees_same_inputs_with_capture(self):
        gate = Path(__file__).resolve().parents[1] / ".tooling" / "gate_openai_agents_3arm.py"
        spec = importlib.util.spec_from_file_location("openai_v2_evidence_stub", gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        case = candidate.build_case("django_count_annotations", 0)

        class Stub(helpers.StubApiModel):
            def _tool_call(self, name, index):
                return ResponseFunctionToolCall(
                    arguments="{}", call_id=f"call_{self.case.scenario}_{index}_{name}",
                    name=name, type="function_call",
                )

        async def run_one(record):
            stub = Stub(case)
            recorder = InputEvidenceRecorder(case.scenario)
            model = EvidenceModelProxy(stub, recorder) if record else stub
            filtered = EvidenceCaptureFilter(adapter(), recorder) if record else adapter()
            agent = Agent(
                name="Offline v2 source diagnosis", model=model, tools=case.tools,
                instructions="Use public source tools in order.",
                model_settings=ModelSettings(temperature=0, max_tokens=1024),
            )
            result = await Runner.run(
                agent, case.history, max_turns=6,
                run_config=RunConfig(call_model_input_filter=filtered, tracing_disabled=True),
            )
            return stub.inputs, stub.instructions, result.final_output, recorder

        plain = asyncio.run(run_one(False))
        traced = asyncio.run(run_one(True))
        self.assertEqual(plain[:3], traced[:3])
        recorder = traced[3]
        self.assertEqual(len(traced[0]), recorder.model_calls)
        after = [r for r in recorder.records if r["stage"] == "filter_after"]
        before = [r for r in recorder.records if r["stage"] == "filter_before"]
        model = [r for r in recorder.records if r["stage"] == "model_input"]
        self.assertEqual(recorder.filter_calls, len(after))
        self.assertEqual(len(before), len(after))
        for previous, following in zip(before, after):
            self.assertEqual(previous["protected_groups"], following["protected_groups"])
        self.assertTrue(all(record["input_sha256"] in {m["input_sha256"] for m in model} for record in after))
        self.assertTrue(recorder.filter_calls >= 1)


if __name__ == "__main__":
    unittest.main()
