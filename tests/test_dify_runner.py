"""Exercise the Dify runner with a simulated Workflow API, not Dify itself."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from context_pruner.sidecar import ContextSidecarService
from experiments.runners.run_dify_lifecycle_validation import (
    _invoke,
    _parse_workflow_response,
    main,
)


class DifyRunnerTest(unittest.TestCase):
    def test_http_error_shows_safe_dify_code_without_echoing_key(self) -> None:
        class DenyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.dumps({
                    "code": "forbidden",
                    "message": "Token lacks scope for fake-test-key",
                }).encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DenyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(RuntimeError) as raised:
                _invoke(
                    f"http://127.0.0.1:{server.server_port}/v1/workflows/run",
                    "fake-test-key", "dify:test", "get_state", {}, timeout=5,
                )
            self.assertIn("Dify workflow HTTP 403: forbidden", str(raised.exception))
            self.assertNotIn("fake-test-key", str(raised.exception))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_non_json_http_error_reports_only_safe_headers(self) -> None:
        class DenyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("CF-Ray", "test-ray")
                self.end_headers()
                self.wfile.write(b"<html>fake-test-key must not appear</html>")

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DenyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(RuntimeError) as raised:
                _invoke(
                    f"http://127.0.0.1:{server.server_port}/v1/workflows/run",
                    "fake-test-key", "dify:test", "get_state", {}, timeout=5,
                )
            message = str(raised.exception)
            self.assertIn("content-type=text/html", message)
            self.assertIn("cf-ray=test-ray", message)
            self.assertNotIn("fake-test-key", message)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_response_requires_successful_workflow_and_sidecar_envelope(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "did not succeed"):
            _parse_workflow_response({"data": {"status": "failed"}})
        with self.assertRaisesRegex(RuntimeError, "not valid JSON"):
            _parse_workflow_response({
                "data": {"status": "succeeded", "outputs": {"response_json": "{"}},
            })
        with self.assertRaisesRegex(RuntimeError, "not a Sidecar"):
            _parse_workflow_response({
                "data": {"status": "succeeded", "outputs": {"response_json": "{}"}},
            })

    def test_plan_needs_no_key_and_enforces_call_cap(self) -> None:
        with patch.dict(os.environ, {"DIFY_API_KEY": ""}), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, main(["--plan"]))
        self.assertIn('"planned_dify_workflow_runs": 24', stdout.getvalue())
        with self.assertRaisesRegex(SystemExit, "exceed"):
            main(["--plan", "--repeats", "2"])
        with self.assertRaisesRegex(SystemExit, "plain URL"):
            main(["--plan", "--dify-base-url", "[https://api.dify.ai/v1](https://api.dify.ai/v1)"])

    def test_runner_reaches_sidecar_through_simulated_workflow_transport(self) -> None:
        service = ContextSidecarService()
        calls: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/v1/workflows/run" or self.headers.get("Authorization") != "Bearer fake-test-key":
                    self.send_error(401)
                    return
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length))
                self.assert_request(request)
                inputs = request["inputs"]
                calls.append(inputs["operation"])
                result = service.lifecycle({
                    "session_id": inputs["session_id"],
                    "operation": inputs["operation"],
                    "payload": json.loads(inputs["payload_json"]),
                })
                response = json.dumps({
                    "data": {
                        "status": "succeeded",
                        "outputs": {"response_json": json.dumps(result)},
                    },
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            @staticmethod
            def assert_request(request: dict) -> None:
                assert request["response_mode"] == "blocking"
                assert request["user"] == "context-pruner-dify-validation"

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
                    "--experiment-id", "fake-workflow-transport",
                    "--confirm-send-synthetic-data",
                ]
                with patch.dict(os.environ, {"DIFY_API_KEY": "fake-test-key"}), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, main(args))
                report = json.loads((Path(directory) / "fake-workflow-transport" / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(24, len(calls))
                self.assertEqual(6, report["summary"]["case_count"])
                self.assertEqual(3, report["summary"]["paired_n"])
                self.assertTrue(report["summary"]["all_preserved"])
                self.assertTrue(report["summary"]["all_finalized"])
                self.assertTrue(report["summary"]["all_deleted"])
                self.assertEqual(0, service.session_count)
                self.assertNotIn("fake-test-key", json.dumps(report))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
