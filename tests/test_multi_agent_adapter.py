"""Multi-Agent private/shared/handoff namespace tests."""

import asyncio
import importlib.util
import json
import unittest

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import AutoGenTeamContextCoordinator, ContextScope


AUTOGEN_INSTALLED = importlib.util.find_spec("autogen_agentchat") is not None


def _render(messages):
    return "\n".join(str(getattr(message, "content", "")) for message in messages)


@unittest.skipUnless(AUTOGEN_INSTALLED, "AutoGen optional dependency is not installed")
class AutoGenTeamContextCoordinatorTest(unittest.TestCase):
    def coordinator(self):
        return AutoGenTeamContextCoordinator(
            ContextPluginConfig(budget=ContextBudget(900, 1400, 750)),
            team_id="incident-team",
            task_state="Resolve INC-2048 without crossing Agent context boundaries",
            fixed_reserved_tokens=80,
        )

    def test_private_shared_and_handoff_events_have_distinct_visibility(self):
        async def scenario():
            coordinator = self.coordinator()
            planner = coordinator.register_agent("planner")
            executor = coordinator.register_agent("executor")

            private_event = await coordinator.publish_private(
                "planner", "PRIVATE-NOTE root token is ALPHA-7"
            )
            shared_event = await coordinator.publish_shared(
                "planner", "SHARED-FACT incident is INC-2048"
            )
            handoff_event = await coordinator.publish_handoff(
                "planner", "executor", "HANDOFF-ACTION run mitigation RUNBOOK-9"
            )

            planner_view = _render(await planner.get_messages())
            executor_view = _render(await executor.get_messages())
            return coordinator, private_event, shared_event, handoff_event, planner_view, executor_view

        (
            coordinator,
            private_event,
            shared_event,
            handoff_event,
            planner_view,
            executor_view,
        ) = asyncio.run(scenario())

        self.assertEqual(ContextScope.PRIVATE, private_event.scope)
        self.assertEqual(ContextScope.SHARED, shared_event.scope)
        self.assertEqual(ContextScope.HANDOFF, handoff_event.scope)
        self.assertIn("PRIVATE-NOTE", planner_view)
        self.assertIn("SHARED-FACT", planner_view)
        self.assertNotIn("HANDOFF-ACTION", planner_view)
        self.assertNotIn("PRIVATE-NOTE", executor_view)
        self.assertIn("SHARED-FACT", executor_view)
        self.assertIn("HANDOFF-ACTION", executor_view)
        metrics = coordinator.metrics_dict()
        self.assertEqual(1, metrics["private_event_count"])
        self.assertEqual(1, metrics["shared_event_count"])
        self.assertEqual(1, metrics["handoff_event_count"])
        self.assertEqual(4, metrics["delivery_count"])

    def test_late_joiner_replays_shared_but_not_private_or_old_handoff(self):
        async def scenario():
            coordinator = self.coordinator()
            coordinator.register_agent("planner")
            coordinator.register_agent("executor")
            await coordinator.publish_private("planner", "PRIVATE-BEFORE-JOIN")
            await coordinator.publish_shared("planner", "SHARED-BEFORE-JOIN")
            await coordinator.publish_handoff(
                "planner", "executor", "HANDOFF-BEFORE-JOIN"
            )
            observer = coordinator.register_agent("observer")
            return coordinator, _render(await observer.get_messages())

        coordinator, observer_view = asyncio.run(scenario())

        self.assertIn("SHARED-BEFORE-JOIN", observer_view)
        self.assertNotIn("PRIVATE-BEFORE-JOIN", observer_view)
        self.assertNotIn("HANDOFF-BEFORE-JOIN", observer_view)
        self.assertEqual(1, coordinator.metrics_dict()["replay_delivery_count"])

    def test_state_round_trip_preserves_routes_without_duplicate_delivery(self):
        async def scenario():
            coordinator = self.coordinator()
            planner = coordinator.register_agent("planner")
            executor = coordinator.register_agent("executor")
            await coordinator.publish_private("planner", "PRIVATE-STATE")
            await coordinator.publish_shared("planner", "SHARED-STATE")
            await coordinator.publish_handoff("planner", "executor", "HANDOFF-STATE")
            await planner.get_messages()
            await executor.get_messages()
            state = json.loads(json.dumps(await coordinator.save_state()))
            restored = await AutoGenTeamContextCoordinator.from_state(
                state,
                config=coordinator.config,
            )
            before_lengths = {
                agent_id: len(restored.get_context(agent_id)._messages)
                for agent_id in restored.agent_ids
            }
            await restored.publish_shared("executor", "SHARED-AFTER-RESTORE")
            after_lengths = {
                agent_id: len(restored.get_context(agent_id)._messages)
                for agent_id in restored.agent_ids
            }
            return restored, before_lengths, after_lengths

        restored, before_lengths, after_lengths = asyncio.run(scenario())

        self.assertEqual(("planner", "executor"), restored.agent_ids)
        self.assertEqual(4, len(restored.events))
        self.assertEqual(
            {key: value + 1 for key, value in before_lengths.items()},
            after_lengths,
        )
        self.assertEqual(0, restored.get_context("planner").metrics_dict()["resync_count"])
        self.assertEqual(0, restored.get_context("executor").metrics_dict()["resync_count"])

    def test_two_real_assistant_agents_receive_only_their_routed_context(self):
        from autogen_agentchat.agents import AssistantAgent
        from autogen_core.models import ModelFamily
        from autogen_ext.models.replay import ReplayChatCompletionClient

        def client(reply):
            return ReplayChatCompletionClient(
                [reply],
                model_info={
                    "vision": False,
                    "function_calling": True,
                    "json_output": False,
                    "family": ModelFamily.UNKNOWN,
                    "structured_output": False,
                },
            )

        async def scenario():
            coordinator = self.coordinator()
            planner_context = coordinator.register_agent("planner")
            executor_context = coordinator.register_agent("executor")
            await coordinator.publish_private("planner", "PRIVATE-RUNNER-ONLY")
            await coordinator.publish_shared("planner", "SHARED-RUNNER")
            await coordinator.publish_handoff(
                "planner", "executor", "HANDOFF-RUNNER"
            )
            planner_client = client("planner complete")
            executor_client = client("executor complete")
            planner = AssistantAgent(
                "planner",
                planner_client,
                model_context=planner_context,
                system_message="Plan only.",
            )
            executor = AssistantAgent(
                "executor",
                executor_client,
                model_context=executor_context,
                system_message="Execute only.",
            )
            await planner.run(task="plan")
            await executor.run(task="execute")
            return (
                _render(planner_client.create_calls[0]["messages"]),
                _render(executor_client.create_calls[0]["messages"]),
            )

        planner_input, executor_input = asyncio.run(scenario())

        self.assertIn("PRIVATE-RUNNER-ONLY", planner_input)
        self.assertIn("SHARED-RUNNER", planner_input)
        self.assertNotIn("HANDOFF-RUNNER", planner_input)
        self.assertNotIn("PRIVATE-RUNNER-ONLY", executor_input)
        self.assertIn("SHARED-RUNNER", executor_input)
        self.assertIn("HANDOFF-RUNNER", executor_input)


if __name__ == "__main__":
    unittest.main()
