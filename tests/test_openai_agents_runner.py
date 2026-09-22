"""Offline end-to-end test using the real OpenAI Agents SDK Runner."""

import importlib.util
import json
import unittest

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter


@unittest.skipUnless(
    importlib.util.find_spec("agents") is not None,
    "openai-agents optional dependency is not installed",
)
class OpenAIAgentsRunnerTest(unittest.TestCase):
    def test_real_runner_completes_local_model_tool_model_loop(self):
        from agents import Agent, ModelResponse, RunConfig, Runner, function_tool
        from agents.models.interface import Model
        from agents.usage import Usage
        from openai.types.responses import (
            ResponseFunctionToolCall,
            ResponseOutputMessage,
            ResponseOutputText,
        )

        class LocalToolModel(Model):
            def __init__(self):
                self.calls = []

            async def get_response(
                self,
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                *,
                previous_response_id,
                conversation_id,
                prompt,
            ):
                self.calls.append(list(input) if isinstance(input, list) else input)
                if len(self.calls) == 1:
                    return ModelResponse(
                        output=[
                            ResponseFunctionToolCall(
                                arguments=json.dumps({"codename": "Aurora-17"}),
                                call_id="call_local_lookup",
                                name="lookup_project",
                                type="function_call",
                            )
                        ],
                        usage=Usage(requests=1),
                        response_id="mock-response-1",
                    )
                return ModelResponse(
                    output=[
                        ResponseOutputMessage(
                            id="mock-message-2",
                            content=[
                                ResponseOutputText(
                                    annotations=[],
                                    text="Verified project Aurora-17.",
                                    type="output_text",
                                )
                            ],
                            role="assistant",
                            status="completed",
                            type="message",
                        )
                    ],
                    usage=Usage(requests=1),
                    response_id="mock-response-2",
                )

            def stream_response(self, *args, **kwargs):
                async def empty_stream():
                    if False:
                        yield None

                return empty_stream()

        @function_tool
        def lookup_project(codename: str) -> str:
            """Return the verified status for a project codename."""
            return json.dumps({"codename": codename, "status": "verified"})

        model = LocalToolModel()
        context_filter = OpenAIAgentsContextFilter(
            ContextPluginConfig(budget=ContextBudget(700, 1200, 550)),
            task_state="Verify and preserve Aurora-17",
            fixed_reserved_tokens=100,
        )
        history = [
            {
                "role": "system",
                "content": "Hard constraint: project codename Aurora-17 must remain.",
            }
        ]
        for index in range(10):
            history.extend(
                [
                    {
                        "role": "user",
                        "content": f"background {index} " + "noise " * 25,
                    },
                    {
                        "role": "assistant",
                        "content": f"analysis {index} " + "routine " * 25,
                    },
                ]
            )
        history.append(
            {"role": "user", "content": "Use the tool and verify Aurora-17."}
        )
        agent = Agent(
            name="Offline Context-Pruner Runner",
            instructions="Use the tool, then answer with the verified codename.",
            model=model,
            tools=[lookup_project],
        )

        result = Runner.run_sync(
            agent,
            history,
            max_turns=4,
            run_config=RunConfig(
                call_model_input_filter=context_filter,
                tracing_disabled=True,
            ),
        )

        self.assertEqual("Verified project Aurora-17.", result.final_output)
        self.assertEqual(2, len(model.calls))
        second_types = [
            str(item.get("type") or "") if isinstance(item, dict) else str(item.type)
            for item in model.calls[1]
        ]
        self.assertIn("function_call", second_types)
        self.assertIn("function_call_output", second_types)
        metrics = context_filter.metrics_dict()
        self.assertGreaterEqual(metrics["openai_agents_protected_group_count"], 1)
        self.assertEqual(0, metrics["openai_agents_group_restore_failure_count"])
        self.assertEqual(0, metrics["openai_agents_unmatched_call_count"])


if __name__ == "__main__":
    unittest.main()
