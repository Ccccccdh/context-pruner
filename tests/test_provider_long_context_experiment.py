from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from experiments.audits.compare_provider_long_context import compare_reports
from experiments.runners.run_provider_long_context_experiment import (
    DEFAULT_TASKS,
    PROVIDERS,
    RequestBudget,
    _api_request,
    _latest_rows,
    _resume_compatible,
    build_initial_history,
    build_model_messages,
    load_tasks,
    main,
    resolve_provider,
)


class _Completions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=3,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
            ),
        )


class _Client:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_Completions())


class ProviderLongContextExperimentTest(unittest.TestCase):
    def test_tasks_are_long_and_evaluation_field_is_not_in_messages(self) -> None:
        tasks = load_tasks(DEFAULT_TASKS)
        self.assertEqual(6, len(tasks))
        self.assertEqual(6, len({task["domain"] for task in tasks}))
        for task in tasks:
            self.assertGreaterEqual(len(build_initial_history(task)), 26)
            encoded = json.dumps(build_model_messages(task), ensure_ascii=False)
            self.assertNotIn("expected_terms", encoded)
            self.assertEqual(2, encoded.count("VERIFIED TOOL RESULT"))

    def test_provider_defaults_and_provider_specific_request_body(self) -> None:
        args = argparse.Namespace(provider="zhipu", model=None, base_url=None, api_key_env=None)
        provider = resolve_provider(args)
        self.assertEqual("GLM-5.3-Flash", provider.model)
        self.assertEqual("ZHIPU_API_KEY", provider.key_env)
        client = _Client()
        result = _api_request(client, provider, [{"role": "user", "content": "hello"}], max_output_tokens=10)
        self.assertNotIn("extra_body", client.chat.completions.kwargs)
        self.assertEqual(2, result[3])
        deepseek = _Client()
        _api_request(deepseek, PROVIDERS["deepseek"], [{"role": "user", "content": "hello"}], max_output_tokens=10)
        self.assertEqual({"thinking": {"type": "disabled"}}, deepseek.chat.completions.kwargs["extra_body"])

    def test_mock_run_and_resume_are_complete_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = [
                "--mode", "mock", "--provider", "zhipu", "--repeats", "1",
                "--out", str(root), "--experiment-id", "case", "--max-api-requests", "1",
            ]
            self.assertEqual(0, main(args))
            results = root / "case" / "results.jsonl"
            self.assertEqual(12, len(results.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(0, main([*args, "--resume"]))
            self.assertEqual(12, len(results.read_text(encoding="utf-8").splitlines()))
            report = json.loads((root / "case" / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["summary"]["all_success"])
            self.assertEqual(6, report["summary"]["paired"]["positive_savings_pairs"])
            self.assertEqual(0, report["summary"]["methods"]["pruner_v1"]["budget_violation_count"])

    def test_resume_helpers_and_request_cap(self) -> None:
        rows = [
            {"task_id": "a", "repeat": 0, "method": "none", "success": False},
            {"task_id": "a", "repeat": 0, "method": "none", "success": True},
        ]
        self.assertTrue(_latest_rows(rows)[0]["success"])
        self.assertTrue(_resume_compatible({"created_at": "a", "runner_sha256": "1", "x": 2}, {"created_at": "b", "runner_sha256": "2", "x": 2}))
        budget = RequestBudget(1)
        budget.consume()
        with self.assertRaises(RuntimeError):
            budget.consume()

    def test_cross_provider_audit_rejects_protocol_mismatch(self) -> None:
        base = {
            "manifest": {
                "protocol_version": "p", "task_sha256": "t", "tasks": ["a"],
                "methods": ["none", "pruner_v1"], "repeats": 1,
                "budget": {"soft": 2}, "max_output_tokens": 10,
                "provider": "one", "model": "m1",
            },
            "summary": {
                "methods": {
                    "none": {"success_rate": 1, "budget_violation_count": 1},
                    "pruner_v1": {"success_rate": 1, "budget_violation_count": 0},
                },
                "paired": {
                    "n": 1, "success_delta_mean": 0,
                    "provider_input_savings_rate_mean": .5,
                    "provider_input_savings_ci_low": .5,
                    "provider_input_savings_ci_high": .5,
                    "provider_total_token_savings_rate_mean": .4,
                    "provider_total_token_savings_ci_low": .4,
                    "provider_total_token_savings_ci_high": .4,
                    "provider_completion_token_change_rate_mean": .1,
                    "provider_reasoning_tokens_delta_mean": 2,
                    "positive_savings_pairs": 1,
                },
            },
        }
        second = json.loads(json.dumps(base))
        second["manifest"].update(provider="two", model="m2")
        self.assertTrue(compare_reports([base, second])["comparable"])
        second["manifest"]["task_sha256"] = "different"
        self.assertFalse(compare_reports([base, second])["comparable"])


if __name__ == "__main__":
    unittest.main()
