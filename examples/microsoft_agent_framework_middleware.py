"""Offline Microsoft Agent Framework middleware smoke example.

Replace ``RecordingChatClient`` with an official provider client in a real
application.  Official clients already include ``ChatMiddlewareLayer``; custom
clients must include that layer for chat middleware to execute.
"""

from __future__ import annotations

import asyncio

from agent_framework import (
    Agent,
    ChatMiddlewareLayer,
    ChatResponse,
    InMemoryHistoryProvider,
    Message,
)

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import create_microsoft_agent_framework_middleware


class _OfflineLeafClient:
    additional_properties: dict = {}

    def get_response(self, messages, *, stream=False, **kwargs):
        if stream:
            raise RuntimeError("this offline example uses non-streaming responses")

        async def response():
            visible = " | ".join(message.text for message in messages)
            return ChatResponse(
                messages=[Message("assistant", [f"model-view: {visible[-160:]}"])]
            )

        return response()


class RecordingChatClient(ChatMiddlewareLayer, _OfflineLeafClient):
    def __init__(self) -> None:
        super().__init__()


async def main() -> None:
    context_pruner = create_microsoft_agent_framework_middleware(
        ContextPluginConfig(
            budget=ContextBudget(
                soft_limit_tokens=220,
                hard_limit_tokens=300,
                target_tokens=170,
            )
        ),
        task_state="Preserve CURRENT_DECISION=GO",
        fixed_reserved_tokens=40,
    )
    agent = Agent(
        client=RecordingChatClient(),
        instructions="Keep CURRENT_DECISION exactly.",
        context_providers=[InMemoryHistoryProvider("history", load_messages=True)],
        middleware=[context_pruner],
    )
    session = agent.create_session(session_id="context-pruner-example")

    for index in range(8):
        await agent.run(
            f"Historical turn {index}: " + "background " * 20,
            session=session,
        )
    response = await agent.run(
        "CURRENT_DECISION=GO; return the current decision.",
        session=session,
    )

    print(response.text)
    print(context_pruner.metrics_dict(session))
    print("state persisted:", "context_pruner" in session.state)


if __name__ == "__main__":
    asyncio.run(main())
