"""Tests against the installed Microsoft Agent Framework runtime."""

from __future__ import annotations

import importlib.util
import unittest

from context_pruner.adapters import MicrosoftAgentFrameworkContextMiddleware
from context_pruner.plugin import ContextPluginConfig
from context_pruner.types import ContextBudget


MICROSOFT_AGENT_FRAMEWORK_INSTALLED = importlib.util.find_spec("agent_framework") is not None

if MICROSOFT_AGENT_FRAMEWORK_INSTALLED:
    from agent_framework import (
        Agent,
        AgentSession,
        ChatContext,
        ChatMiddlewareLayer,
        ChatResponse,
        Content,
        InMemoryHistoryProvider,
        Message,
    )


class _RawRecordingChatClient:
    additional_properties: dict = {}

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def get_response(self, messages, *, stream=False, **kwargs):
        if stream:
            raise AssertionError("streaming is outside this adapter validation")

        async def response():
            self.calls.append(list(messages))
            return ChatResponse(messages=[Message("assistant", ["native-ok"])])

        return response()


class RecordingChatClient(ChatMiddlewareLayer, _RawRecordingChatClient):
    """Real framework middleware layer over a deterministic leaf client."""

    def __init__(self) -> None:
        super().__init__()


@unittest.skipUnless(
    MICROSOFT_AGENT_FRAMEWORK_INSTALLED,
    "Microsoft Agent Framework optional dependency is not installed",
)
class MicrosoftAgentFrameworkContextMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def config(method: str = "pruner_v1") -> ContextPluginConfig:
        return ContextPluginConfig(
            method=method,
            budget=ContextBudget(
                soft_limit_tokens=180,
                hard_limit_tokens=240,
                target_tokens=140,
            ),
        )

    async def test_runs_inside_real_agent_pipeline(self) -> None:
        client = RecordingChatClient()
        middleware = MicrosoftAgentFrameworkContextMiddleware(self.config())
        agent = Agent(
            client=client,
            instructions="Keep the answer short.",
            context_providers=[InMemoryHistoryProvider("history", load_messages=True)],
            middleware=[middleware],
        )
        session = agent.create_session(session_id="native-pipeline")

        response = await agent.run("hello", session=session)
        second_response = await agent.run("again", session=session)

        self.assertEqual("native-ok", response.text)
        self.assertEqual("native-ok", second_response.text)
        self.assertEqual(2, len(client.calls))
        self.assertTrue(all(isinstance(item, Message) for item in client.calls[0]))
        self.assertIn("context_pruner", session.state)
        metrics = middleware.metrics_dict(session)
        self.assertEqual(2, metrics["microsoft_agent_framework_chat_calls"])
        self.assertEqual(2, metrics["microsoft_agent_framework_compressed_calls"])
        self.assertEqual(2, metrics["microsoft_agent_framework_observed_model_output_count"])
        self.assertEqual(0, metrics["resync_count"])

    async def test_compresses_model_view_and_restores_tool_group_by_identity(self) -> None:
        call = Message(
            "assistant",
            [Content.from_function_call("call-1", "lookup", arguments={"id": 7})],
        )
        result = Message(
            "tool",
            [Content.from_function_result("call-1", result={"status": "ready"})],
        )
        latest = Message("user", ["CURRENT_DECISION=GO; preserve this exact fact"])
        history = [Message("system", ["Follow all constraints."])]
        for index in range(12):
            history.extend(
                [
                    Message("user", [f"old request {index} " + "x" * 180]),
                    Message("assistant", [f"old answer {index} " + "y" * 180]),
                ]
            )
        history.extend([call, result, latest])
        session = AgentSession(session_id="tool-roundtrip")
        client = RecordingChatClient()
        context = ChatContext(client, history, None, session=session)
        captured: list[Message] = []

        async def call_next() -> None:
            captured.extend(context.messages)
            context.result = ChatResponse(messages=[Message("assistant", ["done"])])

        middleware = MicrosoftAgentFrameworkContextMiddleware(self.config())
        await middleware.process(context, call_next)

        self.assertTrue(all(isinstance(item, Message) for item in captured))
        self.assertIn(call, captured)
        self.assertIn(result, captured)
        self.assertIs(call, captured[captured.index(call)])
        self.assertIs(result, captured[captured.index(result)])
        self.assertIn("CURRENT_DECISION=GO", "\n".join(item.text for item in captured))
        self.assertLess(
            sum(len(item.text) for item in captured),
            sum(len(item.text) for item in history),
        )
        metrics = middleware.metrics_dict(session)
        self.assertGreater(metrics["compression_count"], 0)
        self.assertEqual(1, metrics["microsoft_agent_framework_protected_group_count"])
        self.assertEqual(2, metrics["microsoft_agent_framework_protected_message_count"])
        self.assertEqual(0, metrics["microsoft_agent_framework_unmatched_call_count"])
        self.assertEqual(0, metrics["microsoft_agent_framework_group_restore_failure_count"])

    async def test_session_state_isolated_and_serializable(self) -> None:
        middleware = MicrosoftAgentFrameworkContextMiddleware(self.config(method="none"))
        client = RecordingChatClient()
        first = AgentSession(session_id="first")
        second = AgentSession(session_id="second")

        async def invoke(session: AgentSession, text: str) -> None:
            context = ChatContext(client, [Message("user", [text])], None, session=session)

            async def call_next() -> None:
                context.result = ChatResponse(messages=[Message("assistant", ["ok"])])

            await middleware.process(context, call_next)

        await invoke(first, "first-only")
        await invoke(first, "first-next")
        await invoke(second, "second-only")

        self.assertEqual(
            2,
            middleware.metrics_dict(first)["microsoft_agent_framework_chat_calls"],
        )
        self.assertEqual(
            1,
            middleware.metrics_dict(second)["microsoft_agent_framework_chat_calls"],
        )

        restored_session = AgentSession.from_dict(first.to_dict())
        restored_adapter = MicrosoftAgentFrameworkContextMiddleware(
            self.config(method="none")
        )
        restored_metrics = restored_adapter.metrics_dict(restored_session)
        self.assertEqual(2, restored_metrics["microsoft_agent_framework_chat_calls"])
        self.assertEqual(2, restored_metrics["microsoft_agent_framework_observed_model_output_count"])

    async def test_errors_are_recorded_and_reraised(self) -> None:
        middleware = MicrosoftAgentFrameworkContextMiddleware(self.config())
        session = AgentSession(session_id="error-session")
        client = RecordingChatClient()
        context = ChatContext(
            client,
            [Message("user", ["recover me"])],
            None,
            session=session,
        )

        async def fail() -> None:
            raise RuntimeError("provider unavailable")

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await middleware.process(context, fail)

        metrics = middleware.metrics_dict(session)
        self.assertEqual(1, metrics["microsoft_agent_framework_error_count"])
        self.assertGreaterEqual(metrics["recovery_count"], 0)


if __name__ == "__main__":
    unittest.main()
