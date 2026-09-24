"""Static Dify DSL checks plus a real authenticated Sidecar HTTP boundary."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from context_pruner.sidecar import SidecarServerConfig, create_sidecar_server

try:
    import yaml
except ImportError:  # The Dify template needs no Python runtime dependency.
    yaml = None


DSL = Path(__file__).resolve().parents[1] / "integrations" / "dify" / "context_pruner_lifecycle.yml"


@unittest.skipIf(yaml is None, "PyYAML is only needed for DSL validation")
class DifyLifecycleBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsl = yaml.safe_load(DSL.read_text(encoding="utf-8"))
        cls.workflow = cls.dsl["workflow"]
        cls.nodes = {node["id"]: node["data"] for node in cls.workflow["graph"]["nodes"]}

    def test_template_is_secret_free_and_connects_code_http_end(self) -> None:
        self.assertEqual("workflow", self.dsl["app"]["mode"])
        self.assertEqual(
            [("start", "prepare"), ("prepare", "invoke"), ("invoke", "end")],
            [(edge["source"], edge["target"]) for edge in self.workflow["graph"]["edges"]],
        )
        self.assertEqual("http-request", self.nodes["invoke"]["type"])
        self.assertEqual("{{#prepare.body#}}", self.nodes["invoke"]["body"]["data"])
        self.assertEqual(
            "{{#env.CONTEXT_PRUNER_BASE_URL#}}/v1/lifecycle",
            self.nodes["invoke"]["url"],
        )
        self.assertIn("{{#env.CONTEXT_PRUNER_AUTH_TOKEN#}}", self.nodes["invoke"]["headers"])
        variables = {item["name"]: item for item in self.workflow["environment_variables"]}
        self.assertEqual("secret", variables["CONTEXT_PRUNER_AUTH_TOKEN"]["value_type"])
        self.assertEqual("", variables["CONTEXT_PRUNER_AUTH_TOKEN"]["value"])
        self.assertEqual(
            ["invoke", "body"],
            self.nodes["end"]["outputs"][0]["value_selector"],
        )

    def test_dify_code_payload_round_trips_through_real_sidecar(self) -> None:
        namespace: dict[str, object] = {}
        exec(self.nodes["prepare"]["code"], namespace)
        prepare = namespace["main"]
        config = SidecarServerConfig(port=0, auth_token="dify-test-token")
        server = create_sidecar_server(config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/v1/lifecycle"

        def invoke(operation: str, payload: dict) -> dict:
            prepared = prepare(operation, "dify:test", json.dumps(payload, ensure_ascii=False))
            request = urllib.request.Request(
                url,
                data=prepared["body"].encode("utf-8"),
                headers={
                    "Authorization": "Bearer dify-test-token",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            history = [{"role": "system", "content": "Keep Aurora-17."}]
            for index in range(14):
                history.append({"role": "assistant", "content": (f"obsolete draft {index} ") * 30})
            history.append({"role": "user", "content": "Current: resolve Aurora-17."})
            first = invoke("before_model", {
                "messages": history,
                "task_state": "Resolve Aurora-17",
                "method": "pruner_v1",
                "budget": {"soft": 500, "hard": 700, "target": 400},
            })
            self.assertLess(len(first["messages"]), len(history))
            self.assertIn("Aurora-17", json.dumps(first["messages"]))
            self.assertEqual(1, invoke("get_state", {})["metrics"]["before_model_calls"])
            invoke("after_model", {"output": {"role": "assistant", "content": "Aurora-17 resolved."}})
            final = invoke("finalize", {"metadata": {"source": "dify-template-test"}})
            self.assertTrue(final["lifecycle_state"]["plugin"]["finalized"])
            self.assertTrue(invoke("delete_session", {})["deleted"])

            with self.assertRaises(ValueError):
                prepare("unknown", "dify:test", "{}")
            with self.assertRaises(ValueError):
                prepare("before_model", "dify:test", "[]")
            with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                urllib.request.urlopen(urllib.request.Request(
                    url,
                    data=prepare("get_state", "dify:test", "{}")["body"].encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ), timeout=5)
            self.assertEqual(401, unauthorized.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
