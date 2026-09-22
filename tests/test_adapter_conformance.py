"""Cross-adapter conformance test for the shared lifecycle core."""

import json
import tempfile
import unittest
from pathlib import Path

from context_pruner import ContextBudget, ContextPluginConfig, ContextPrunerMiddleware
from context_pruner.adapters import LangGraphContextAdapter, OpenAIAgentsContextFilter
from context_pruner.cli import main as cli_main
from context_pruner.sidecar import ContextSidecarService, SidecarServerConfig


def _history():
    messages = [
        {
            "role": "system",
            "content": "Hard constraint: preserve project codename Aurora-17.",
        },
        {"role": "user", "content": "Analyze the task history."},
    ]
    for index in range(12):
        messages.append(
            {"role": "assistant", "content": f"analysis {index} " + "background " * 30}
        )
        messages.append(
            {"role": "user", "content": f"observation {index} " + "noise " * 24}
        )
    return messages


class AdapterConformanceTest(unittest.TestCase):
    def test_all_message_adapters_render_the_same_model_view(self):
        config = ContextPluginConfig(budget=ContextBudget(280, 400, 230))
        history = _history()
        task = "Remember Aurora-17"

        generic = ContextPrunerMiddleware(config, task_state=task).before_model(history)
        langgraph = LangGraphContextAdapter(config).before_model_node(
            {"messages": history, "task": task}
        )
        agents = OpenAIAgentsContextFilter(config, task_state=task).filter_items(
            history,
            None,
        )
        sidecar = ContextSidecarService(
            SidecarServerConfig(default_plugin=config)
        ).before_model(
            "conformance",
            {"messages": history, "task_state": task},
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(
                json.dumps({"messages": history, "task_state": task}),
                encoding="utf-8",
            )
            cli_main(
                [
                    "compress",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--soft",
                    "280",
                    "--hard",
                    "400",
                    "--target",
                    "230",
                ]
            )
            cli_messages = json.loads(output_path.read_text(encoding="utf-8"))[
                "messages"
            ]

        self.assertEqual(generic.messages, langgraph["context_messages"])
        self.assertEqual(generic.messages, agents.input_items)
        self.assertEqual(generic.messages, cli_messages)
        self.assertEqual(generic.messages, sidecar["messages"])
        rendered = json.dumps(generic.messages, ensure_ascii=False)
        self.assertIn("Aurora-17", rendered)


if __name__ == "__main__":
    unittest.main()
