"""AutoGen AgentChat adapter tests using the actual optional framework."""

import asyncio
import importlib.util
import json
import unittest

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import AutoGenContextPruner
from context_pruner.plugin import message_to_turn


AUTOGEN_INSTALLED = importlib.util.find_spec("autogen_agentchat") is not None


@unittest.skipUnless(AUTOGEN_INSTALLED, "AutoGen optional dependency is not installed")
class AutoGenContextPrunerTest(unittest.TestCase):
    def setUp(self):
        from autogen_core.models import AssistantMessage, UserMessage

        self.history = [UserMessage(content="Keep incident ID INC-2048.", source="user")]
        for index in range(12):
            self.history.extend(
                [
                    AssistantMessage(
                        content=f"old analysis {index} " + "background " * 32,
                        source="assistant",
                    ),
                    UserMessage(
                        content=f"old update {index} " + "routine detail " * 26,
                        source="user",
                    ),
                ]
            )

    def adapter(self, initial_messages=None):
        return AutoGenContextPruner(
            ContextPluginConfig(budget=ContextBudget(700, 1000, 560)),
            initial_messages=initial_messages or self.history,
            task_state="Resolve INC-2048",
            fixed_reserved_tokens=80,
        )

    def test_message_history_is_compressed_to_autogen_types(self):
        from autogen_core.models import (
            AssistantMessage,
            FunctionExecutionResultMessage,
            SystemMessage,
            UserMessage,
        )

        context = self.adapter()
        result = asyncio.run(context.get_messages())

        self.assertLess(len(result), len(self.history))
        self.assertTrue(
            all(
                isinstance(
                    message,
                    (
                        SystemMessage,
                        UserMessage,
                        AssistantMessage,
                        FunctionExecutionResultMessage,
                    ),
                )
                for message in result
            )
        )
        rendered = json.dumps(
            [message.model_dump() for message in result], ensure_ascii=False
        )
        self.assertIn("INC-2048", rendered)
        self.assertEqual(1, context.metrics_dict()["autogen_compressed_calls"])

    def test_latest_user_task_survives_hard_pressure_losslessly(self):
        from autogen_core.models import AssistantMessage, SystemMessage, UserMessage

        current_task = UserMessage(
            content=(
                "Evaluate current facts: delay=96-hours, delivered=false, "
                "case_channel=priority."
            ),
            source="user",
        )
        context = self.adapter(self.history + [current_task])

        self.assertEqual("user", message_to_turn(current_task)["role"])
        self.assertEqual(
            "assistant",
            message_to_turn(AssistantMessage(content="ok", source="assistant"))["role"],
        )
        self.assertEqual(
            "system",
            message_to_turn(SystemMessage(content="rule"))["role"],
        )

        result = asyncio.run(context.get_messages())

        self.assertTrue(any(message is current_task for message in result))
        rendered = json.dumps(
            [message.model_dump() for message in result], ensure_ascii=False
        )
        self.assertIn("delay=96-hours", rendered)
        self.assertIn("case_channel=priority", rendered)

    def test_parallel_tool_call_and_results_are_restored_by_identity(self):
        from autogen_core import FunctionCall
        from autogen_core.models import (
            AssistantMessage,
            FunctionExecutionResult,
            FunctionExecutionResultMessage,
        )

        call_message = AssistantMessage(
            content=[
                FunctionCall(id="call-health", name="read_health", arguments="{}"),
                FunctionCall(id="call-deploy", name="read_deploy", arguments="{}"),
            ],
            source="assistant",
        )
        result_message = FunctionExecutionResultMessage(
            content=[
                FunctionExecutionResult(
                    content="degraded", name="read_health", call_id="call-health"
                ),
                FunctionExecutionResult(
                    content="release-91", name="read_deploy", call_id="call-deploy"
                ),
            ]
        )
        context = self.adapter(self.history + [call_message, result_message])

        result = asyncio.run(context.get_messages())

        self.assertIs(call_message, result[-2])
        self.assertIs(result_message, result[-1])
        metrics = context.metrics_dict()
        self.assertEqual(1, metrics["autogen_protected_group_count"])
        self.assertEqual(2, metrics["autogen_protected_message_count"])
        self.assertEqual(0, metrics["autogen_unmatched_call_count"])
        self.assertEqual(0, metrics["autogen_group_restore_failure_count"])

    def test_state_round_trip_preserves_history_plugin_state_and_metrics(self):
        context = self.adapter()
        asyncio.run(context.get_messages())
        state = asyncio.run(context.save_state())
        restored = self.adapter([])

        asyncio.run(restored.load_state(state))
        result = asyncio.run(restored.get_messages())

        self.assertTrue(result)
        self.assertEqual(len(context._messages), len(restored._messages))
        self.assertEqual(2, restored.metrics_dict()["autogen_get_messages_calls"])
        self.assertEqual(0, restored.metrics_dict()["resync_count"])

    def test_real_assistant_agent_runs_tool_loop_with_pruned_context(self):
        from autogen_agentchat.agents import AssistantAgent
        from autogen_core import FunctionCall
        from autogen_core.models import CreateResult, ModelFamily, RequestUsage
        from autogen_ext.models.replay import ReplayChatCompletionClient

        def read_incident(incident_id: str) -> str:
            """Read the current status for an incident."""
            return json.dumps({"incident_id": incident_id, "status": "mitigated"})

        responses = [
            CreateResult(
                finish_reason="function_calls",
                content=[
                    FunctionCall(
                        id="call-incident",
                        name="read_incident",
                        arguments='{"incident_id":"INC-2048"}',
                    )
                ],
                usage=RequestUsage(prompt_tokens=10, completion_tokens=4),
                cached=False,
            ),
            "RESULT incident=INC-2048 status=mitigated",
        ]
        client = ReplayChatCompletionClient(
            responses,
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "family": ModelFamily.UNKNOWN,
                "structured_output": False,
            },
        )
        context = self.adapter()
        agent = AssistantAgent(
            "incident_agent",
            client,
            tools=[read_incident],
            model_context=context,
            reflect_on_tool_use=True,
            max_tool_iterations=2,
            system_message="Verify the incident with tools and answer in one line.",
        )

        result = asyncio.run(agent.run(task="What is the current state of INC-2048?"))

        self.assertEqual(
            "RESULT incident=INC-2048 status=mitigated",
            result.messages[-1].content,
        )
        self.assertEqual(2, len(client.create_calls))
        second = client.create_calls[1]["messages"]
        self.assertTrue(any(message.type == "AssistantMessage" for message in second))
        self.assertTrue(
            any(message.type == "FunctionExecutionResultMessage" for message in second)
        )
        metrics = context.metrics_dict()
        self.assertGreaterEqual(metrics["autogen_protected_group_count"], 1)
        self.assertEqual(0, metrics["autogen_group_restore_failure_count"])


if __name__ == "__main__":
    unittest.main()
