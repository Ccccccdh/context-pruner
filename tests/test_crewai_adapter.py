"""Tests against CrewAI's public global model/tool hook surface."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


# CrewAI initialises platform directories while importing.  Keep all optional
# test state outside the user's real AppData tree.
_CREWAI_TEST_STORAGE = Path(tempfile.gettempdir()) / "context-pruner-crewai-tests"
_CREWAI_TEST_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LOCALAPPDATA", str(_CREWAI_TEST_STORAGE))
os.environ.setdefault("CREWAI_STORAGE_DIR", str(_CREWAI_TEST_STORAGE))

CREWAI_INSTALLED = importlib.util.find_spec("crewai") is not None

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import CrewAIContextAdapter

if CREWAI_INSTALLED:
    from crewai import Agent, BaseLLM
    from crewai.hooks import (
        LLMCallHookContext,
        get_after_llm_call_hooks,
        get_after_tool_call_hooks,
        get_before_llm_call_hooks,
    )
    from crewai.tools import tool


class _ReplayLLM(BaseLLM if CREWAI_INSTALLED else object):
    """Deterministic CrewAI provider used to exercise a real Agent executor."""

    if CREWAI_INSTALLED:

        def __init__(self) -> None:
            super().__init__(model="context-pruner-replay", provider="custom")
            self.calls: list[list[dict]] = []
            self.responses = [
                (
                    "Thought: I need the incident record.\n"
                    "Action: lookup_incident\n"
                    'Action Input: {"incident_id": "INC-2048"}'
                ),
                (
                    "Thought: I have verified the record.\n"
                    "Final Answer: RESULT incident=INC-2048 decision=ROLLBACK "
                    "evidence=degraded"
                ),
            ]

        def call(
            self,
            messages,
            tools=None,
            callbacks=None,
            available_functions=None,
            from_task=None,
            from_agent=None,
            response_model=None,
        ):
            self.calls.append(list(messages) if isinstance(messages, list) else [messages])
            if not self.responses:
                raise AssertionError("CrewAI made more replay calls than expected")
            return self.responses.pop(0)


@unittest.skipUnless(CREWAI_INSTALLED, "CrewAI optional dependency is not installed")
class CrewAIContextAdapterTest(unittest.TestCase):
    @staticmethod
    def config(method: str = "pruner_v1") -> ContextPluginConfig:
        return ContextPluginConfig(
            method=method,
            budget=ContextBudget(
                soft_limit_tokens=180,
                hard_limit_tokens=260,
                target_tokens=140,
            ),
        )

    @staticmethod
    def long_history() -> list[dict]:
        history: list[dict] = [
            {"role": "system", "content": "Preserve AURORA-17 exactly."}
        ]
        for index in range(12):
            history.extend(
                [
                    {
                        "role": "user",
                        "content": f"old request {index} " + "background " * 28,
                    },
                    {
                        "role": "assistant",
                        "content": f"old answer {index} " + "routine " * 28,
                    },
                ]
            )
        history.append(
            {"role": "user", "content": "CURRENT_DECISION=GO; return it exactly."}
        )
        return history

    def make_context(self, messages: list[dict], *, response: str | None = None):
        return LLMCallHookContext(
            messages=messages,
            response=response,
            agent=SimpleNamespace(role="Incident analyst"),
        )

    def test_model_view_is_compressed_in_place_then_authoritative_history_restored(self):
        messages = self.long_history()
        original_list = messages
        original_items = list(messages)
        original_characters = sum(len(item["content"]) for item in messages)
        context = self.make_context(messages)
        adapter = CrewAIContextAdapter(
            self.config(),
            task_state="Preserve AURORA-17 and CURRENT_DECISION=GO",
            agent_roles=["Incident analyst"],
        )

        adapter.before_llm_call(context)

        self.assertIs(original_list, context.messages)
        self.assertLess(
            sum(len(str(item.get("content", ""))) for item in context.messages),
            original_characters,
        )
        self.assertIn(
            "CURRENT_DECISION=GO",
            json.dumps(context.messages, ensure_ascii=False),
        )

        context.response = "RESULT decision=GO"
        adapter.after_llm_call(context)

        self.assertIs(original_list, context.messages)
        self.assertEqual(original_items, context.messages)
        self.assertEqual(1, adapter.metrics.before_llm_calls)
        self.assertEqual(1, adapter.metrics.after_llm_calls)
        self.assertEqual(1, adapter.metrics.compressed_calls)
        self.assertEqual(0, adapter.metrics.active_pending_view_count)

    def test_structured_tool_group_is_restored_as_the_same_objects(self):
        tool_call = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"id":7}'},
                }
            ],
        }
        tool_result = {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"status":"ready"}',
        }
        messages = [*self.long_history(), tool_call, tool_result]
        context = self.make_context(messages)
        adapter = CrewAIContextAdapter(self.config())

        adapter.before_llm_call(context)

        self.assertIn(tool_call, context.messages)
        self.assertIn(tool_result, context.messages)
        self.assertIs(tool_call, context.messages[context.messages.index(tool_call)])
        self.assertIs(tool_result, context.messages[context.messages.index(tool_result)])
        adapter.after_llm_call(context)
        self.assertIs(tool_call, context.messages[-2])
        self.assertIs(tool_result, context.messages[-1])
        self.assertEqual(1, adapter.metrics.protected_group_count)
        self.assertEqual(2, adapter.metrics.protected_message_count)
        self.assertEqual(0, adapter.metrics.group_restore_failure_count)

    def test_text_react_tool_observation_is_losslessly_protected(self):
        text_trace = {
            "role": "assistant",
            "content": (
                "Thought: verify current health\n"
                "Action: get_service_health\n"
                'Action Input: {"service":"payments-api"}\n'
                'Observation: {"status":"degraded","error_rate_percent":3.8}'
            ),
        }
        messages = [*self.long_history(), text_trace]
        context = self.make_context(messages)
        adapter = CrewAIContextAdapter(self.config())

        adapter.before_llm_call(context)

        self.assertIn(text_trace, context.messages)
        self.assertIs(text_trace, context.messages[context.messages.index(text_trace)])
        adapter.after_llm_call(context)
        self.assertIs(text_trace, context.messages[-1])
        self.assertEqual(1, adapter.metrics.protected_group_count)
        self.assertEqual(1, adapter.metrics.protected_message_count)
        self.assertEqual(0, adapter.metrics.group_restore_failure_count)

    def test_next_pre_hook_reconciles_native_tool_messages_when_post_hook_is_skipped(self):
        messages = self.long_history()
        original = list(messages)
        context = self.make_context(messages)
        adapter = CrewAIContextAdapter(self.config())
        adapter.before_llm_call(context)

        structured_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "native-1", "type": "function"}],
        }
        structured_result = {
            "role": "tool",
            "tool_call_id": "native-1",
            "content": "native-result",
        }
        messages.extend([structured_call, structured_result])

        # CrewAI intentionally has no after-model callback for a native tool
        # payload.  A second pre-hook must merge those appended messages into
        # the authoritative shadow before installing the next model view.
        adapter.before_llm_call(context)
        adapter.after_llm_call(context)

        self.assertEqual([*original, structured_call, structured_result], messages)
        self.assertEqual(1, adapter.metrics.pending_reconciliations)
        self.assertEqual(0, adapter.metrics.authoritative_resync_count)

    def test_state_round_trip_and_target_filter(self):
        filtered_messages = self.long_history()
        filtered_context = LLMCallHookContext(
            messages=filtered_messages,
            agent=SimpleNamespace(role="Other role"),
        )
        adapter = CrewAIContextAdapter(
            self.config(method="none"), agent_roles=["Incident analyst"]
        )
        adapter.before_llm_call(filtered_context)
        self.assertEqual(1, adapter.metrics.filtered_calls)

        accepted = self.make_context(self.long_history(), response="ok")
        adapter.before_llm_call(accepted)
        adapter.after_llm_call(accepted)
        exported = json.loads(json.dumps(adapter.export_state()))

        restored = CrewAIContextAdapter(
            self.config(method="none"), agent_roles=["Incident analyst"]
        )
        restored.restore_state(exported)
        self.assertEqual(1, restored.metrics.before_llm_calls)
        self.assertEqual(1, restored.metrics.after_llm_calls)
        self.assertEqual(1, restored.metrics.filtered_calls)
        self.assertEqual(0, restored.metrics.active_pending_view_count)

    def test_attached_registers_and_removes_only_its_hooks(self):
        before_counts = (
            len(get_before_llm_call_hooks()),
            len(get_after_llm_call_hooks()),
            len(get_after_tool_call_hooks()),
        )
        adapter = CrewAIContextAdapter(self.config())

        with adapter.attached():
            self.assertEqual(before_counts[0] + 1, len(get_before_llm_call_hooks()))
            self.assertEqual(before_counts[1] + 1, len(get_after_llm_call_hooks()))
            self.assertEqual(before_counts[2] + 1, len(get_after_tool_call_hooks()))

        self.assertEqual(before_counts[0], len(get_before_llm_call_hooks()))
        self.assertEqual(before_counts[1], len(get_after_llm_call_hooks()))
        self.assertEqual(before_counts[2], len(get_after_tool_call_hooks()))

    def test_runs_inside_real_crewai_agent_with_tool_lifecycle(self):
        @tool("lookup_incident")
        def lookup_incident(incident_id: str) -> str:
            """Return the deterministic incident health record."""
            return f"{incident_id} degraded; required decision is ROLLBACK"

        replay = _ReplayLLM()
        agent = Agent(
            role="Incident analyst",
            goal="Use evidence to select the safe incident decision.",
            backstory="You are a deterministic operations analyst.",
            llm=replay,
            tools=[lookup_incident],
            allow_delegation=False,
            max_iter=4,
            verbose=False,
        )
        adapter = CrewAIContextAdapter(
            self.config(),
            task_state="Incident INC-2048 requires one evidence-backed decision.",
            agent_roles=["Incident analyst"],
        )

        with adapter.attached():
            output = agent.kickoff(
                "Inspect INC-2048 with the available tool and report the decision."
            )

        self.assertIn("ROLLBACK", str(output))
        self.assertEqual(2, len(replay.calls))
        self.assertGreaterEqual(adapter.metrics.before_llm_calls, 2)
        self.assertGreaterEqual(adapter.metrics.after_llm_calls, 2)
        self.assertEqual(1, adapter.metrics.after_tool_calls)
        self.assertEqual(0, adapter.metrics.active_pending_view_count)


if __name__ == "__main__":
    unittest.main()
