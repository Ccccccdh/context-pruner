"""AutoGen AgentChat integration through a drop-in model context."""

import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import create_autogen_model_context


async def main() -> None:
    model_client = OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_MODEL", "deepseek-v4-flash"),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": False,
            "family": "unknown",
            "structured_output": False,
            "multiple_system_messages": False,
        },
    )
    model_context = create_autogen_model_context(
        ContextPluginConfig(budget=ContextBudget(6000, 8000, 5000)),
        task_state="回答当前用户任务",
        fixed_reserved_tokens=1200,
    )
    agent = AssistantAgent(
        "assistant",
        model_client,
        model_context=model_context,
        system_message="Answer accurately and concisely.",
    )
    result = await agent.run(task="Explain context lifecycle management.")
    print(result.messages[-1].content)
    print(model_context.metrics_dict())
    await model_client.close()


if __name__ == "__main__":
    asyncio.run(main())
