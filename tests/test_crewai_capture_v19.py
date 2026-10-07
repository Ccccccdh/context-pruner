"""Zero-API controls for complete r19 provider-attempt capture and request cap."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from experiments.runners.crewai_capture_v19 import CapturedOpenAICompatCrewAILLM
from experiments.runners.run_crewai_experiment import RequestBudget


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100 + self.calls, completion_tokens=12),
        )


class CaptureControls(unittest.TestCase):
    def make_llm(self, responses, *, cap=3):
        trace = []
        completions = FakeCompletions(responses)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        llm = CapturedOpenAICompatCrewAILLM(
            client=client, model="fake", request_budget=RequestBudget(cap),
            max_output_tokens=512, thinking_mode="disabled",
            executed_tool_trace=trace, frozen_tool_names=("read_one", "read_two"),
        )
        return llm, trace, completions

    def test_full_attempt_and_host_execution_binding(self):
        llm, trace, client = self.make_llm([
            "Thought: call\nAction: read_one\nAction Input: {}",
            "Thought: call\nAction: read_two\nAction Input: {}",
        ])
        first_input = [{"role": "user", "content": "synthetic marker-q19"}]
        self.assertIn("Action: read_one", llm.call(first_input))
        trace.append("read_one")
        prior = [{"role": "assistant", "content": "Action: read_one\nAction Input: {}\nObservation: {}"}]
        self.assertIn("Action: read_two", llm.call([*first_input, *prior]))
        trace.append("read_two")
        llm.bind_executed_tools()
        self.assertEqual(client.calls, 2)
        self.assertEqual([x["request_slot"] for x in llm.capture], [1, 2])
        self.assertEqual([x["host_parsed_action"] for x in llm.capture], ["read_one", "read_two"])
        self.assertEqual([x["host_executed_tool"] for x in llm.capture], ["read_one", "read_two"])
        self.assertEqual(llm.capture[0]["full_input_messages"][0]["content"], "synthetic marker-q19")
        self.assertEqual(llm.capture[1]["v18_offline_verdict"], "advance")
        self.assertEqual(sum(x["input_tokens"] + x["output_tokens"] for x in llm.capture),
                         sum(x["input_tokens"] + x["output_tokens"] for x in llm.attempt_records))

    def test_request_cap_prevents_provider_call(self):
        llm, _, client = self.make_llm(["HANDOFF", "HANDOFF"], cap=1)
        llm.call([{"role": "user", "content": "synthetic"}])
        with self.assertRaises(Exception):
            llm.call([{"role": "user", "content": "synthetic"}])
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(llm.capture), 1)

    def test_provider_error_is_captured_and_charged(self):
        llm, _, client = self.make_llm([RuntimeError("transport failed")], cap=1)
        with self.assertRaises(RuntimeError):
            llm.call([{"role": "user", "content": "synthetic"}])
        self.assertEqual(client.calls, 1)
        self.assertEqual(llm.request_budget.used, 1)
        self.assertEqual(llm.capture[0]["status"], "error")
        self.assertEqual(llm.capture[0]["error_type"], "RuntimeError")
        self.assertEqual(len(llm.attempt_records), 1)


if __name__ == "__main__":
    unittest.main()
