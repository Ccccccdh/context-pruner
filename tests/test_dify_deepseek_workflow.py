"""Static and local lifecycle checks for the Dify model-call workflow."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from context_pruner.sidecar import ContextSidecarService

try:
    import yaml
except ImportError:
    yaml = None


DSL = Path(__file__).resolve().parents[1] / "integrations" / "dify" / "context_pruner_deepseek_ab.yml"


@unittest.skipIf(yaml is None, "PyYAML is only needed for DSL validation")
class DifyDeepSeekWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsl = yaml.safe_load(DSL.read_text(encoding="utf-8"))
        cls.graph = cls.dsl["workflow"]["graph"]
        cls.nodes = {node["id"]: node["data"] for node in cls.graph["nodes"]}

    @staticmethod
    def _code(data: dict):
        namespace: dict[str, object] = {}
        exec(data["code"], namespace)
        return namespace["main"]

    def test_graph_has_one_model_call_and_secret_free_sidecar_hooks(self) -> None:
        self.assertEqual("workflow", self.dsl["app"]["mode"])
        self.assertEqual("deepseek-v4-flash", self.nodes["llm"]["model"]["name"])
        self.assertEqual("langgenius/deepseek/deepseek", self.nodes["llm"]["model"]["provider"])
        self.assertEqual(512, self.nodes["llm"]["model"]["completion_params"]["max_tokens"])
        self.assertEqual(["prepare_after", "answer"], self.nodes["end"]["outputs"][0]["value_selector"])
        self.assertEqual(1, sum(node["type"] == "llm" for node in self.nodes.values()))
        self.assertEqual(
            ["start", "prepare_before", "before", "extract", "llm", "prepare_after", "after", "final", "delete", "summarize", "end"],
            [self.graph["edges"][0]["source"]] + [edge["target"] for edge in self.graph["edges"]],
        )
        for node_id in ("before", "after", "final", "delete"):
            node = self.nodes[node_id]
            self.assertEqual("http-request", node["type"])
            self.assertEqual("{{#env.CONTEXT_PRUNER_BASE_URL#}}/v1/lifecycle", node["url"])
            self.assertIn("{{#env.CONTEXT_PRUNER_AUTH_TOKEN#}}", node["headers"])
            self.assertFalse(node["retry_config"]["retry_enabled"])
        variables = {item["name"]: item for item in self.dsl["workflow"]["environment_variables"]}
        self.assertEqual("", variables["CONTEXT_PRUNER_AUTH_TOKEN"]["value"])
        self.assertEqual("secret", variables["CONTEXT_PRUNER_AUTH_TOKEN"]["value_type"])
        self.assertNotIn("expected", json.dumps(self.nodes["llm"]))

    def test_code_nodes_round_trip_full_lifecycle_without_llm_secret(self) -> None:
        service = ContextSidecarService()
        prepare_before = self._code(self.nodes["prepare_before"])
        extract = self._code(self.nodes["extract"])
        prepare_after = self._code(self.nodes["prepare_after"])
        summarize = self._code(self.nodes["summarize"])
        history = [{"role": "system", "content": "Use only the latest verified record."}]
        for index in range(14):
            history.append({"role": "assistant", "content": (f"Old draft {index}, superseded. ") * 30})
        history.append({"role": "user", "content": "Current verified incident INC-901; rollback_trigger=true."})
        results = {}
        for method in ("none", "pruner_v1"):
            session_id = f"dify:local:{method}"
            request = json.loads(prepare_before(
                session_id, method, "Current verified incident INC-901", json.dumps(history),
            )["body"])
            before = service.lifecycle(request)
            prompt = extract(json.dumps(before))["prompt_text"]
            self.assertIn("INC-901", prompt)
            answer = "incident_id=INC-901; rollback_trigger=true"
            cleaned = prepare_after(session_id, "<think>Old incident_id=INC-100</think>" + answer)
            self.assertEqual(answer, cleaned["answer"])
            after = json.loads(cleaned["body"])
            self.assertEqual(answer, after["payload"]["output"]["content"])
            service.lifecycle(after)
            final = service.lifecycle({"session_id": session_id, "operation": "finalize"})
            deleted = service.lifecycle({"session_id": session_id, "operation": "delete_session"})
            results[method] = summarize(json.dumps(before), json.dumps(final), json.dumps(deleted))
            self.assertTrue(results[method]["finalized"])
            self.assertTrue(results[method]["deleted"])
        self.assertLess(
            results["pruner_v1"]["served_context_tokens"],
            results["none"]["served_context_tokens"],
        )
        self.assertEqual(0, service.session_count)
        with self.assertRaises(ValueError):
            prepare_before("bad", "other", "task", json.dumps(history))
        self.assertEqual("", prepare_after("bad", "<think>unfinished reasoning")["answer"])


if __name__ == "__main__":
    unittest.main()
