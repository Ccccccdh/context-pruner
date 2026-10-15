import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.runners.run_agents_lifecycle_v1 import run_local_trial


class SDKLifecycleTest(unittest.TestCase):
    def test_failed_sdk_trial_retains_failure_and_observed_inputs(self):
        base = Path(__file__).resolve().parents[1] / ".tooling/agents-lifecycle-20261014"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            out = Path(tmp)/"failed"
            async def fail(*args, **kwargs):
                raise RuntimeError("controlled model failure")
            with patch("experiments.runners.run_agents_lifecycle_v1.LocalDependencyModel.get_response", fail):
                with self.assertRaises(RuntimeError):
                    run_local_trial("A2", "memory", out)
            failure = json.loads((out/"failure.json").read_text())
            self.assertEqual("RuntimeError", failure["error_type"])
            self.assertTrue((out/"model_inputs.json").exists())
            self.assertEqual(0, failure["provider_requests"])

    def test_server_managed_history_options_rejected_before_any_model_call(self):
        from agents import Agent
        from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle
        from experiments.runners.run_agents_lifecycle_v1 import LocalDependencyModel
        for flag, value in (("previous_response_id", "fake"), ("conversation_id", "fake"), ("auto_previous_response_id", True)):
            a = OpenAIAgentsEvidenceLifecycle(namespace="guard-test", trusted_tools=set())
            model = LocalDependencyModel()
            agent = Agent(name="guard-test", model=model)
            with self.assertRaises(ValueError):
                a.run_sync(agent, "input", **{flag: value})
            self.assertEqual([], model.inputs)

    def test_real_runner_memory_and_sqlite_three_arm_closure(self):
        base = Path(__file__).resolve().parents[1] / ".tooling/agents-lifecycle-20261014"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            for backend in ("memory", "sqlite"):
                for arm in ("A0", "A1", "A2"):
                    with self.subTest(arm=arm, backend=backend):
                        report = run_local_trial(arm, backend, Path(tmp)/f"{backend}-{arm}")
                        self.assertEqual(arm != "A1", report["independent_dependency_passed"])
                        self.assertEqual(0, report["provider_requests"])
                        self.assertEqual(7, report["sdk_model_calls"])
                        self.assertEqual(6, report["sdk_tool_calls"])
                        self.assertEqual(arm == "A2", report["recovery_original_delivered"])
                        self.assertTrue(report["all_tool_groups_paired"])
                        if arm == "A2":
                            self.assertTrue(report["ttl_original_removed"])
                            self.assertEqual(1, report["metrics"]["recoveries"])
                        data = json.loads((Path(tmp)/f"{backend}-{arm}"/"model_inputs.json").read_text())
                        self.assertEqual(7, len(data))


if __name__ == "__main__":
    unittest.main()
