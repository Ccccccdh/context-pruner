"""Zero-API gate for public-repository diagnostic candidates."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_pruner import ContextBudget
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as candidate
from experiments.audits import audit_openai_agents_repo_diagnostic_v1 as independent


class RepoDiagnosticGate(unittest.TestCase):
    def test_fixed_repeat_and_baseline_provenance(self):
        self.assertEqual(3, len(candidate.TASKS))
        hashes = candidate.input_hashes()
        self.assertEqual(10, len(hashes))
        self.assertTrue(all("reference.patch" not in path and "host-tests.patch" not in path for path in hashes))
        for task in candidate.TASKS:
            first = candidate.build_case(task, 0)
            for repeat in (1, 2):
                other = candidate.build_case(task, repeat)
                self.assertEqual(first.history, other.history)
                self.assertEqual(first.expected_tool_names, other.expected_tool_names)
                self.assertEqual(first.answer_pattern, other.answer_pattern)
            self.assertEqual(3, len(first.tools))
            self.assertNotIn("RESULT ", first.history[-1]["content"])
            for index in range(3):
                view = candidate.source_view(task, index)
                self.assertIn("(baseline)", view)
                self.assertGreater(len(view), 100)

    def test_real_sdk_dispatch_and_three_arm_structure_without_api(self):
        from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

        gate = Path(__file__).resolve().parents[1] / ".tooling" / "gate_openai_agents_3arm.py"
        spec = importlib.util.spec_from_file_location("openai_repo_gate_helpers", gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)

        answers = {
            "pytest_mro": "RESULT issue=pytest-10356 cause=getattr inherits base mark fix=read own __dict__ then traverse MRO",
            "pylint_regex_csv": "RESULT issue=pylint-8898 cause=_splitstrip splits every comma fix=keep comma inside quantifier braces",
            "django_count_annotations": "RESULT issue=django-16263 cause=existing_annotations force subquery fix=prune only unreferenced annotations",
        }

        class Stub(helpers.StubApiModel):
            def _tool_call(self, name, index):
                return ResponseFunctionToolCall(arguments="{}", call_id=f"call_{self.case.scenario}_{index}_{name}", name=name, type="function_call")

            def _final_message(self):
                return ResponseOutputMessage(
                    id=f"msg_{self.case.scenario}",
                    content=[ResponseOutputText(annotations=[], text=answers[self.case.scenario], type="output_text")],
                    role="assistant", status="completed", type="message",
                )

        async def run_all():
            rows = []
            with patch.object(helpers, "StubApiModel", Stub):
                for task in candidate.TASKS:
                    case = candidate.build_case(task, 0)
                    for method in ("none", "native_summary", "pruner_v1"):
                        rows.append(await helpers._run_arm_case(
                            task, 0, method, budget=ContextBudget(5435, 16304, 4076), case=case
                        ))
            return rows

        rows = asyncio.run(run_all())
        self.assertEqual(9, len(rows))
        for row in rows:
            self.assertTrue(row["success"], (row["scenario"], row["method"], row["error_message"]))
            self.assertEqual(3, row["tool_calls"])
            self.assertTrue(row["structure_safe"])
        by_key = {(row["scenario"], row["method"]): row for row in rows}
        self.assertEqual(0, by_key["pytest_mro", "pruner_v1"]["compressed_calls"])
        self.assertEqual(0, by_key["pytest_mro", "native_summary"]["summary_calls"])
        for task in ("pylint_regex_csv", "django_count_annotations"):
            self.assertGreaterEqual(by_key[task, "pruner_v1"]["compressed_calls"], 1)
            self.assertGreaterEqual(by_key[task, "native_summary"]["summary_calls"], 1)

    def test_independent_auditor_detects_quality_and_usage_tampering(self):
        answers = {
            "pytest_mro": "RESULT issue=pytest-10356 cause=getattr inherits mark fix=read own __dict__ through MRO",
            "pylint_regex_csv": "RESULT issue=pylint-8898 cause=_splitstrip divides comma fix=respect quantifier braces",
            "django_count_annotations": "RESULT issue=django-16263 cause=existing_annotations force subquery fix=keep referenced annotations",
        }
        names = {name: spec[0] for name, spec in independent.CONTRACTS.items()}
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp) / "audit-selftest"
            batch.mkdir()
            freeze = {
                "batch": batch.name, "scenarios": list(candidate.TASKS), "repeats": 1,
                "max_api_requests": 30, "max_output_tokens": 1024,
                "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com",
                "provider_budget_tokens": {"soft": 2000, "hard": 6000, "target": 1500},
                "source_sha256": {}, "public_input_sha256": {},
            }
            manifest = {
                "experiment_id": batch.name, "scenarios": list(candidate.TASKS),
                "methods": list(independent.METHODS), "repeats": 1,
                "maximum_api_requests": 30, "max_output_tokens": 1024,
                "model": freeze["model"], "base_url": freeze["base_url"],
                "trigger_policy": "symmetric_budget",
                "budget_calibration": {"provider_tokens": freeze["provider_budget_tokens"]},
            }
            rows = []
            for task in candidate.TASKS:
                for method, total in (("none", 100), ("pruner_v1", 90), ("native_summary", 110)):
                    rows.append({
                        "scenario": task, "repeat": 0, "method": method,
                        "final_output": answers[task], "tool_names": list(names[task]),
                        "success": True, "constraint_preserved": True,
                        "pairing_integrity": True, "structure_safe": True,
                        "actual_input_tokens_by_call": [total - 20], "actual_output_tokens_by_call": [20],
                        "actual_input_tokens": total - 20, "actual_output_tokens": 20,
                        "summary_input_tokens": 0, "summary_output_tokens": 0,
                        "all_arm_total_tokens": total, "api_request_attempts": 1,
                        "model_calls": 1, "summary_calls": 0, "error_type": "",
                    })
            report = {"request_budget": {"recorded_attempts_all_rows": 9}, "comparisons": {
                "pruner_v1_vs_none": {"paired_n": 3, "all_arm_total_token_savings_rate_vs_baseline": 0.1},
                "native_summary_vs_none": {"paired_n": 3, "all_arm_total_token_savings_rate_vs_baseline": -0.1},
            }}
            freeze_path = batch / "freeze.json"
            freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
            (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (batch / "report.json").write_text(json.dumps(report), encoding="utf-8")

            def write_rows():
                (batch / "samples.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            write_rows()
            self.assertTrue(independent.audit(batch, freeze_path)["complete"])
            rows[0]["success"] = False
            write_rows()
            self.assertIn("quality mismatch", " ".join(independent.audit(batch, freeze_path)["issues"]))
            rows[0]["success"] = True
            rows[0]["all_arm_total_tokens"] += 1
            write_rows()
            self.assertIn("usage mismatch", " ".join(independent.audit(batch, freeze_path)["issues"]))


if __name__ == "__main__":
    unittest.main()
