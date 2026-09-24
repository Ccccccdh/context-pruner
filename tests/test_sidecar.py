"""Contract and live HTTP tests for the language-neutral sidecar."""

import json
import http.client
import threading
import unittest
import urllib.error
import urllib.request

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.sidecar import (
    ContextSidecarService,
    SidecarRequestError,
    SidecarServerConfig,
    create_sidecar_server,
)


def _history():
    messages = [
        {
            "role": "system",
            "content": "Hard constraint: preserve project Aurora-17.",
        }
    ]
    for index in range(12):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"old analysis {index} " + "background " * 28,
                },
                {
                    "role": "user",
                    "content": f"old note {index} " + "routine " * 24,
                },
            ]
        )
    messages.append(
        {
            "role": "user",
            "content": "Current task facts: delay=96-hours channel=priority.",
        }
    )
    return messages


def _config(**kwargs):
    return SidecarServerConfig(
        default_plugin=ContextPluginConfig(
            budget=ContextBudget(500, 700, 400)
        ),
        **kwargs,
    )


class ContextSidecarServiceTest(unittest.TestCase):
    def test_low_code_lifecycle_dispatch_preserves_state_and_rejects_invalid_operations(self):
        service = ContextSidecarService(_config())
        first = service.lifecycle({
            "session_id": "low-code:source",
            "operation": "before_model",
            "payload": {"messages": _history(), "task_state": "Aurora-17"},
        })
        self.assertLess(len(first["messages"]), len(_history()))
        state = service.lifecycle({"session_id": "low-code:source", "operation": "get_state"})
        restored = service.lifecycle({
            "session_id": "low-code:restored",
            "operation": "restore_state",
            "payload": {"lifecycle_state": state["lifecycle_state"], "task_state": "Aurora-17"},
        })
        self.assertEqual(1, restored["metrics"]["before_model_calls"])
        service.lifecycle({
            "session_id": "low-code:restored", "operation": "after_tool",
            "payload": {"output": {"role": "tool", "content": "VERIFIED TOOL RESULT: Aurora-17"}},
        })
        recovered = service.lifecycle({
            "session_id": "low-code:restored", "operation": "on_error",
            "payload": {"error": "missing fact", "query": "Aurora-17", "recover": True},
        })
        self.assertIn("metrics", recovered)
        service.lifecycle({
            "session_id": "low-code:restored", "operation": "after_model",
            "payload": {"output": {"role": "assistant", "content": "Aurora-17"}},
        })
        final = service.lifecycle({"session_id": "low-code:restored", "operation": "finalize"})
        self.assertTrue(final["lifecycle_state"]["plugin"]["finalized"])
        self.assertTrue(service.lifecycle({
            "session_id": "low-code:source", "operation": "delete_session"
        })["deleted"])
        with self.assertRaisesRegex(SidecarRequestError, "unsupported lifecycle"):
            service.lifecycle({"session_id": "low-code:x", "operation": "arbitrary"})
        with self.assertRaisesRegex(SidecarRequestError, "payload must"):
            service.lifecycle({"session_id": "low-code:x", "operation": "before_model", "payload": "{}"})

    def test_before_model_compresses_and_keeps_sessions_isolated(self):
        service = ContextSidecarService(_config(max_sessions=2))

        first = service.before_model(
            "tenant-a:agent-1",
            {
                "messages": _history(),
                "task_state": "Preserve Aurora-17 and current delivery facts",
                "reserved_tokens": 40,
            },
        )
        second = service.before_model(
            "tenant-b:agent-1",
            {"messages": [{"role": "user", "content": "Independent task B."}]},
        )

        self.assertLess(len(first["messages"]), len(_history()))
        rendered = json.dumps(first["messages"], ensure_ascii=False)
        self.assertIn("Aurora-17", rendered)
        self.assertIn("delay=96-hours", rendered)
        self.assertEqual("tenant-a:agent-1", first["session_id"])
        self.assertEqual(1, first["metrics"]["before_model_calls"])
        self.assertEqual(1, second["metrics"]["before_model_calls"])
        self.assertEqual(2, service.session_count)

    def test_state_can_move_to_a_fresh_sidecar_process(self):
        first_service = ContextSidecarService(_config())
        first = first_service.before_model(
            "portable-session",
            {"messages": _history(), "task_state": "Aurora-17"},
        )
        second_service = ContextSidecarService(_config())

        restored = second_service.restore(
            "portable-session",
            {
                "lifecycle_state": json.loads(
                    json.dumps(first["lifecycle_state"])
                ),
                "task_state": "Aurora-17",
            },
        )
        resumed = second_service.before_model(
            "portable-session",
            {"messages": _history(), "task_state": "Aurora-17"},
        )

        self.assertEqual(1, restored["metrics"]["before_model_calls"])
        self.assertEqual(2, resumed["metrics"]["before_model_calls"])
        self.assertEqual(0, resumed["metrics"]["resync_count"])

    def test_lifecycle_event_hooks_finalize_and_delete(self):
        service = ContextSidecarService(_config())
        service.before_model(
            "hook-session",
            {"messages": [{"role": "user", "content": "Resolve INC-7."}]},
        )

        model = service.after_model(
            "hook-session",
            {"output": {"role": "assistant", "content": "Checking INC-7."}},
        )
        tool = service.after_tool(
            "hook-session",
            {"output": {"role": "tool", "content": "INC-7 is mitigated."}},
        )
        recovered = service.on_error(
            "hook-session",
            {"error": "missing fact", "query": "INC-7", "recover": True},
        )
        final = service.finalize("hook-session", {"metadata": {"success": True}})
        deleted = service.delete("hook-session")

        self.assertTrue(model["messages"])
        self.assertTrue(tool["messages"])
        self.assertIn("metrics", recovered)
        self.assertIn("lifecycle_state", final)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(0, service.session_count)

    def test_capacity_invalid_ids_and_config_changes_are_rejected(self):
        service = ContextSidecarService(_config(max_sessions=1))
        service.before_model(
            "first",
            {"messages": [{"role": "user", "content": "task"}]},
        )

        with self.assertRaisesRegex(SidecarRequestError, "maximum"):
            service.before_model(
                "second",
                {"messages": [{"role": "user", "content": "task"}]},
            )
        with self.assertRaisesRegex(SidecarRequestError, "session_id"):
            service.delete("invalid/session")
        with self.assertRaisesRegex(SidecarRequestError, "changing"):
            service.before_model(
                "first",
                {
                    "messages": [{"role": "user", "content": "task"}],
                    "method": "none",
                },
            )
        with self.assertRaisesRegex(SidecarRequestError, "boolean"):
            service.before_model(
                "first",
                {
                    "messages": [{"role": "user", "content": "task"}],
                    "enabled": "false",
                },
            )

    def test_non_loopback_binding_requires_authentication(self):
        with self.assertRaisesRegex(ValueError, "bearer token"):
            SidecarServerConfig(host="0.0.0.0", auth_token="")


class ContextSidecarHttpTest(unittest.TestCase):
    def setUp(self):
        self.config = _config(
            port=0,
            auth_token="test-token",
            max_request_bytes=1_048_576,
        )
        self.server = create_sidecar_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.host = host
        self.port = port
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, payload=None, *, token="test-token"):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_live_http_contract_auth_state_and_delete(self):
        status, health = self.request("GET", "/health", token=None)
        self.assertEqual(200, status)
        self.assertEqual("ok", health["status"])

        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            self.request("GET", "/v1/capabilities", token=None)
        self.assertEqual(401, unauthorized.exception.code)

        status, capabilities = self.request("GET", "/v1/capabilities")
        self.assertEqual(200, status)
        self.assertTrue(capabilities["adapter"]["implemented"])
        self.assertEqual("http_sidecar", capabilities["adapter"]["adapter_id"])

        status, compressed = self.request(
            "POST",
            "/v1/sessions/demo/before-model",
            {"messages": _history(), "task_state": "Aurora-17"},
        )
        self.assertEqual(200, status)
        self.assertLess(len(compressed["messages"]), len(_history()))

        status, state = self.request("GET", "/v1/sessions/demo/state")
        self.assertEqual(200, status)
        self.assertEqual(1, state["metrics"]["before_model_calls"])

        status, restored = self.request(
            "PUT",
            "/v1/sessions/restored/state",
            {"lifecycle_state": state["lifecycle_state"]},
        )
        self.assertEqual(200, status)
        self.assertEqual(1, restored["metrics"]["before_model_calls"])

        status, deleted = self.request("DELETE", "/v1/sessions/demo")
        self.assertEqual(200, status)
        self.assertTrue(deleted["deleted"])

    def test_low_code_lifecycle_http_route_requires_auth_and_valid_json(self):
        path = "/v1/lifecycle"
        payload = {
            "operation": "before_model",
            "session_id": "dify:sample",
            "payload": {"messages": _history(), "task_state": "Aurora-17"},
        }
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            self.request("POST", path, payload, token=None)
        self.assertEqual(401, unauthorized.exception.code)
        status, first = self.request("POST", path, payload)
        self.assertEqual(200, status)
        self.assertLess(len(first["messages"]), len(_history()))
        status, final = self.request("POST", path, {
            "operation": "finalize", "session_id": "dify:sample", "payload": {},
        })
        self.assertEqual(200, status)
        self.assertTrue(final["lifecycle_state"]["plugin"]["finalized"])
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("POST", path, {"operation": "before_model", "session_id": "bad/id"})
        self.assertEqual(400, invalid.exception.code)

    def test_live_http_rejects_wrong_content_type_and_oversized_body(self):
        request = urllib.request.Request(
            self.base + "/v1/sessions/demo/before-model",
            data=b"{}",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "text/plain",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as unsupported:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(415, unsupported.exception.code)

        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.putrequest("POST", "/v1/sessions/demo/before-model")
        connection.putheader("Authorization", "Bearer test-token")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(1_048_577))
        connection.endheaders()
        response = connection.getresponse()
        try:
            self.assertEqual(413, response.status)
            error = json.loads(response.read().decode("utf-8"))
            self.assertEqual("request_too_large", error["error"]["code"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
