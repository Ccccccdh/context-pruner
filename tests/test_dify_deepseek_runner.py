"""Offline contract tests for the Dify-hosted DeepSeek paired runner."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from context_pruner.sidecar import ContextSidecarService
from experiments.runners.run_dify_deepseek_ab import DIVERSE_TASKS, TASKS, _matches_answer, _selected_tasks, _visible_answer, main


class DifyDeepSeekRunnerTest(unittest.TestCase):
    def test_reasoning_is_never_scored_as_final_answer(self) -> None:
        self.assertEqual(
            "",
            _visible_answer("<think>incident_id=INC-901; rollback_trigger=true</think>"),
        )
        self.assertEqual(
            "rollback_trigger",
            _visible_answer("<think>incident_id=INC-901; rollback_trigger=true</think>rollback_trigger"),
        )
        self.assertEqual(
            "migration_id=CYGNUS-4\ntarget_region=ap-s",
            _visible_answer("<think>target_region=ap-south</think>migration_id=CYGNUS-4\ntarget_region=ap-s"),
        )
        self.assertEqual("", _visible_answer("<think>incident_id=INC-901"))
        self.assertEqual(
            "incident_id=INC-901\nrollback_trigger=true",
            _visible_answer("<think>obsolete draft</think>incident_id=INC-901\nrollback_trigger=true"),
        )
        incident_patterns = TASKS[0][2]
        reasoning_only = "<think>incident_id=INC-901; rollback_trigger=true</think>rollback_trigger"
        self.assertFalse(_matches_answer(_visible_answer(reasoning_only), incident_patterns))
        self.assertFalse(_matches_answer("incident_id=INC-901; rollback_trigger=true; rollback_trigger=false", incident_patterns))
        self.assertFalse(_matches_answer("incident_id=INC-901; rollback_trigger=true; extra=yes", incident_patterns))
        self.assertTrue(_matches_answer("incident_id=INC-901\nrollback_trigger=true", incident_patterns))

    def test_plan_and_call_cap_do_not_need_api_key(self) -> None:
        with patch.dict(os.environ, {"DIFY_DEEPSEEK_API_KEY": ""}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(["--plan"]))
        self.assertIn('"maximum_model_calls": 6', output.getvalue())
        with self.assertRaisesRegex(SystemExit, "exceed"):
            main(["--plan", "--repeats", "2"])
        with self.assertRaisesRegex(SystemExit, "plain URL"):
            main(["--plan", "--dify-base-url", "[https://api.dify.ai/v1](https://api.dify.ai/v1)"])

    def test_diverse_suite_is_preregistered_and_keeps_required_evidence(self) -> None:
        with patch.dict(os.environ, {"DIFY_DEEPSEEK_API_KEY": ""}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(["--suite", "diverse", "--max-workflow-runs", "12", "--plan"]))
        plan = json.loads(output.getvalue().split("\n计划模式", 1)[0])
        self.assertEqual(12, plan["maximum_model_calls"])
        self.assertEqual(6, len(plan["tasks"]))
        self.assertEqual("diverse", plan["suite"])
        self.assertEqual(64, len(plan["taskset_sha256"]))
        self.assertEqual("visible-exact-two-fields-v1", plan["scorer_version"])
        with self.assertRaisesRegex(SystemExit, "exceed"):
            main(["--suite", "diverse", "--plan"])

        service = ContextSidecarService()
        evidence_by_id = {task_id: evidence for task_id, _, _, _, evidence in DIVERSE_TASKS}
        for task_id, state, messages, _ in _selected_tasks("diverse"):
            self.assertEqual("user", messages[-1]["role"])
            self.assertGreater(len(messages), 20)
            for method in ("none", "pruner_v1"):
                session_id = f"offline:{task_id}:{method}"
                response = service.lifecycle({
                    "session_id": session_id,
                    "operation": "before_model",
                    "payload": {
                        "messages": messages,
                        "task_state": state,
                        "method": method,
                        "budget": {"soft": 900, "hard": 1200, "target": 650},
                        "reserved_tokens": 180,
                    },
                })
                model_view = "\n".join(message["content"] for message in response["messages"])
                for fragment in evidence_by_id[task_id]:
                    self.assertIn(fragment, model_view, (task_id, method, fragment))
                self.assertIn(messages[-1]["content"], model_view)
                if method == "pruner_v1":
                    self.assertEqual(0, response["metrics"]["model_input_budget_violation_count"], task_id)
                    self.assertLessEqual(response["metrics"]["peak_model_input_tokens"], 1200, task_id)
                    if task_id in {"two_source_join", "policy_filter"}:
                        self.assertNotIn("this draft was never verified", model_view)
                service.lifecycle({"session_id": session_id, "operation": "finalize"})
                service.lifecycle({"session_id": session_id, "operation": "delete_session"})
        self.assertEqual(0, service.session_count)

    def test_fake_workflow_preserves_pairing_and_resume(self) -> None:
        service = ContextSidecarService()
        calls = 0
        fail_after_first = True

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                nonlocal calls, fail_after_first
                if self.path != "/v1/workflows/run" or self.headers.get("Authorization") != "Bearer fake-dify-key":
                    self.send_error(401)
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls += 1
                if fail_after_first and calls == 2:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                inputs = request["inputs"]
                history = json.loads(inputs["messages_json"])
                before = service.lifecycle({
                    "session_id": inputs["session_id"],
                    "operation": "before_model",
                    "payload": {
                        "messages": history,
                        "task_state": inputs["task_state"],
                        "method": inputs["method"],
                        "budget": {"soft": 900, "hard": 1200, "target": 650},
                        "reserved_tokens": 180,
                    },
                })
                current = history[-1]["content"]
                answer = "; ".join(re.findall(
                    r"(?:incident_id|rollback_trigger|release_id|security_gate|migration_id|target_region)=[\w-]+",
                    current,
                ))
                service.lifecycle({
                    "session_id": inputs["session_id"],
                    "operation": "after_model",
                    "payload": {"output": {"role": "assistant", "content": answer}},
                })
                final = service.lifecycle({"session_id": inputs["session_id"], "operation": "finalize"})
                deleted = service.lifecycle({"session_id": inputs["session_id"], "operation": "delete_session"})
                metrics = before["metrics"]
                response = json.dumps({
                    "workflow_run_id": f"fake-{calls}",
                    "data": {
                        "status": "succeeded",
                        "total_tokens": metrics["last_served_context_tokens"] + 12,
                        "outputs": {
                            "answer": answer,
                            "full_context_tokens": metrics["last_full_context_tokens"],
                            "served_context_tokens": metrics["last_served_context_tokens"],
                            "budget_violations": metrics["model_input_budget_violation_count"],
                            "finalized": final["lifecycle_state"]["plugin"]["finalized"],
                            "deleted": deleted["deleted"],
                        },
                    },
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                args = [
                    "--dify-base-url", f"http://127.0.0.1:{server.server_port}/v1",
                    "--out", directory,
                    "--experiment-id", "offline-pair",
                    "--confirm-send-synthetic-data",
                ]
                with patch.dict(os.environ, {"DIFY_DEEPSEEK_API_KEY": "fake-dify-key"}), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                        main(args)
                    partial = Path(directory) / "offline-pair" / "results.jsonl"
                    self.assertEqual(1, len(partial.read_text(encoding="utf-8").splitlines()))
                    fail_after_first = False
                    self.assertEqual(0, main([*args, "--resume"]))
                report = json.loads((Path(directory) / "offline-pair" / "report.json").read_text(encoding="utf-8"))
                summary = report["summary"]
                self.assertEqual(6, summary["case_count"])
                self.assertEqual(3, summary["paired_n"])
                self.assertEqual(3, summary["baseline_success_n"])
                self.assertEqual(3, summary["treated_success_n"])
                self.assertEqual(3, summary["paired_both_success_n"])
                self.assertEqual(0, summary["paired_treated_regression_n"])
                self.assertTrue(summary["all_finalized"])
                self.assertTrue(summary["all_deleted"])
                self.assertGreater(summary["paired_served_context_savings_rate_mean"], 0)
                self.assertEqual(0, service.session_count)
                self.assertNotIn("fake-dify-key", json.dumps(report))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_diverse_suite_runs_all_pairs_with_mocked_workflow(self) -> None:
        answers = {
            "verified_tool": "owner_team=delta\nrollback_trigger=true",
            "corrected_tool": "release_id=ATLAS-8\nsecurity_gate=failed",
            "two_source_join": "owner_team=platform\ntarget_region=ap-south",
            "arithmetic": "batch_id=BATCH-42\ntotal_items=20",
            "error_retry": "ticket_id=TCK-88\napproval=denied",
            "policy_filter": "chosen_region=eu-west\ndata_class=restricted",
        }
        service = ContextSidecarService()

        def fake_workflow(_endpoint: str, _key: str, inputs: dict[str, str], _timeout: float) -> dict:
            task_id = inputs["session_id"].split(":")[2]
            before = service.lifecycle({
                "session_id": inputs["session_id"],
                "operation": "before_model",
                "payload": {
                    "messages": json.loads(inputs["messages_json"]),
                    "task_state": inputs["task_state"],
                    "method": inputs["method"],
                    "budget": {"soft": 900, "hard": 1200, "target": 650},
                    "reserved_tokens": 180,
                },
            })
            answer = f"<think>obsolete draft</think>{answers[task_id]}"
            service.lifecycle({"session_id": inputs["session_id"], "operation": "after_model", "payload": {"output": answer}})
            final = service.lifecycle({"session_id": inputs["session_id"], "operation": "finalize"})
            deleted = service.lifecycle({"session_id": inputs["session_id"], "operation": "delete_session"})
            metrics = before["metrics"]
            return {
                "workflow_run_id": "mock-" + task_id,
                "answer": answer,
                "total_model_tokens": metrics["last_served_context_tokens"] + 20,
                "full_context_tokens": metrics["last_full_context_tokens"],
                "served_context_tokens": metrics["last_served_context_tokens"],
                "budget_violations": metrics["model_input_budget_violation_count"],
                "finalized": final["lifecycle_state"]["plugin"]["finalized"],
                "deleted": deleted["deleted"],
            }

        with tempfile.TemporaryDirectory() as directory:
            args = [
                "--suite", "diverse", "--max-workflow-runs", "12",
                "--out", directory, "--experiment-id", "offline-diverse",
                "--confirm-send-synthetic-data",
            ]
            with patch.dict(os.environ, {"DIFY_DEEPSEEK_API_KEY": "fake-key"}), \
                    patch("experiments.runners.run_dify_deepseek_ab._run_workflow", side_effect=fake_workflow), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(args))
            report = json.loads((Path(directory) / "offline-diverse" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(12, report["summary"]["case_count"])
            self.assertEqual(6, report["summary"]["paired_n"])
            self.assertEqual(6, report["summary"]["baseline_success_n"])
            self.assertEqual(6, report["summary"]["treated_success_n"])
            self.assertEqual(6, report["summary"]["paired_both_success_n"])
            self.assertEqual(0, report["summary"]["paired_treated_regression_n"])
            self.assertEqual(6, len(report["summary"]["per_task"]))
            self.assertTrue(all("<think>" not in row["answer"] for row in report["rows"]))
            self.assertEqual(0, service.session_count)


if __name__ == "__main__":
    unittest.main()
