"""Capability catalog and broad Agent-category adapter tests."""

import json
import unittest

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import (
    AgentCategory,
    AgentSurface,
    ExplicitHistoryAdapter,
    GenericAgentAdapter,
    IntegrationLevel,
    assess_agent_surface,
    get_adapter_descriptor,
    list_adapter_descriptors,
)


def _history():
    messages = [{"role": "user", "content": "Keep constraint Aurora-17."}]
    for index in range(10):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"old analysis {index} " + "background " * 25,
                },
                {
                    "role": "user",
                    "content": f"old observation {index} " + "routine " * 20,
                },
            ]
        )
    return messages


class AdapterCatalogTest(unittest.TestCase):
    def test_implemented_catalog_covers_six_distinct_agent_categories(self):
        descriptors = list_adapter_descriptors()

        self.assertEqual(9, len(descriptors))
        self.assertEqual(9, len({item.adapter_id for item in descriptors}))
        self.assertEqual(
            {
                AgentCategory.CUSTOM_LOOP,
                AgentCategory.WORKFLOW_GRAPH,
                AgentCategory.AGENT_SDK,
                AgentCategory.MULTI_AGENT,
                AgentCategory.COMMERCIAL_PRODUCT,
                AgentCategory.LOW_CODE_CLOUD,
            },
            {item.category for item in descriptors},
        )
        self.assertTrue(all(item.implemented for item in descriptors))

    def test_planned_catalog_is_explicit_and_never_reported_as_implemented(self):
        all_descriptors = list_adapter_descriptors(include_planned=True)
        planned = [item for item in all_descriptors if not item.implemented]

        self.assertEqual(set(), {item.adapter_id for item in planned})
        sidecar = get_adapter_descriptor("http_sidecar")
        self.assertTrue(sidecar.implemented)
        self.assertEqual("real_api", sidecar.validation_level.value)
        self.assertTrue(any("one model provider" in value for value in sidecar.limitations))
        microsoft = get_adapter_descriptor("microsoft_agent_framework")
        self.assertTrue(microsoft.implemented)
        self.assertEqual("local_runner", microsoft.validation_level.value)
        self.assertTrue(microsoft.capabilities.lossless_tool_groups)
        self.assertFalse(microsoft.capabilities.multi_agent_namespaces)
        crewai = get_adapter_descriptor("crewai")
        self.assertTrue(crewai.implemented)
        self.assertEqual("local_runner", crewai.validation_level.value)
        self.assertTrue(crewai.capabilities.lossless_tool_groups)
        self.assertFalse(crewai.capabilities.multi_agent_namespaces)

    def test_descriptor_serialization_preserves_honest_autogen_boundary(self):
        descriptor = get_adapter_descriptor("autogen_agentchat")
        data = descriptor.to_dict()

        self.assertEqual("multi_agent", data["category"])
        self.assertEqual("real_api", data["validation_level"])
        self.assertFalse(data["capabilities"]["multi_agent_namespaces"])
        self.assertTrue(any("team-level" in value for value in data["limitations"]))

        team = get_adapter_descriptor("autogen_team").to_dict()
        self.assertTrue(team["capabilities"]["multi_agent_namespaces"])
        self.assertEqual("real_api", team["validation_level"])
        self.assertTrue(any("one model provider" in value for value in team["limitations"]))

    def test_capability_assessment_selects_native_bridge_explicit_and_unsupported(self):
        native = assess_agent_surface(
            AgentSurface(full_history_visible=True, pre_model_hook=True)
        )
        incremental = assess_agent_surface(AgentSurface(pre_model_hook=True))
        proxy = assess_agent_surface(
            AgentSurface(configurable_model_endpoint=True)
        )
        explicit = assess_agent_surface(AgentSurface(exported_history=True))
        unsupported = assess_agent_surface(AgentSurface())

        self.assertEqual(IntegrationLevel.NATIVE, native.integration_level)
        self.assertTrue(native.automatic_context_control)
        self.assertEqual(IntegrationLevel.BRIDGE, incremental.integration_level)
        self.assertEqual(IntegrationLevel.BRIDGE, proxy.integration_level)
        self.assertEqual(IntegrationLevel.EXPLICIT, explicit.integration_level)
        self.assertFalse(explicit.automatic_context_control)
        self.assertEqual(IntegrationLevel.UNSUPPORTED, unsupported.integration_level)

    def test_multi_agent_assessment_requires_namespace_and_external_state_safety(self):
        assessment = assess_agent_surface(
            AgentSurface(
                full_history_visible=True,
                pre_model_hook=True,
                tool_events_visible=True,
                multi_agent=True,
            )
        )

        self.assertTrue(
            any("namespace coordinator" in value for value in assessment.required_components)
        )
        self.assertTrue(
            any("private, shared, and handoff" in value for value in assessment.safety_constraints)
        )
        self.assertTrue(
            any("outside the Agent" in value for value in assessment.safety_constraints)
        )

    def test_generic_loop_adapter_compresses_and_round_trips_state(self):
        config = ContextPluginConfig(budget=ContextBudget(300, 500, 250))
        adapter = GenericAgentAdapter(config, task_state="Keep Aurora-17")

        first = adapter.before_model(_history(), reserved_tokens=50)
        restored = GenericAgentAdapter.from_state(
            json.loads(json.dumps(adapter.export_state())),
            config=config,
            task_state="Keep Aurora-17",
        )
        second = restored.before_model(_history(), reserved_tokens=50)

        self.assertLess(len(first.messages), len(_history()))
        self.assertEqual(first.messages, second.messages)
        self.assertEqual(0, second.metrics["resync_count"])
        self.assertEqual(2, second.metrics["before_model_calls"])

    def test_explicit_history_adapter_marks_nonautomatic_commercial_bridge(self):
        adapter = ExplicitHistoryAdapter(
            ContextPluginConfig(budget=ContextBudget(700, 1000, 560)),
            task_state="Keep Aurora-17",
        )

        result = adapter.prepare_export(
            {"messages": _history(), "task_state": "Keep Aurora-17"},
            reserved_tokens=50,
        )

        self.assertFalse(result["automatic_context_control"])
        self.assertEqual("commercial_product", result["adapter"]["category"])
        self.assertEqual("explicit", result["adapter"]["integration_level"])
        self.assertIn("Aurora-17", json.dumps(result["messages"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
