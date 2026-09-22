"""Minimal private/shared/handoff context routing for two AutoGen Agents."""

import asyncio

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import AutoGenTeamContextCoordinator


async def main() -> None:
    coordinator = AutoGenTeamContextCoordinator(
        ContextPluginConfig(budget=ContextBudget(6000, 8000, 5000)),
        team_id="release-team",
        task_state="Validate and execute the release",
        fixed_reserved_tokens=1200,
    )
    planner_context = coordinator.register_agent("planner")
    executor_context = coordinator.register_agent("executor")

    await coordinator.publish_private(
        "planner",
        "Candidate comparison notes; keep inside the planner context.",
    )
    await coordinator.publish_shared(
        "planner",
        "Release constraint: production requires approval ticket REL-42.",
    )
    await coordinator.publish_handoff(
        "planner",
        "executor",
        "Execute only after REL-42 is approved; use deployment plan blue-green.",
    )

    # Pass planner_context and executor_context to the corresponding
    # AssistantAgent(model_context=...) instances.
    print("planner messages:", len(await planner_context.get_messages()))
    print("executor messages:", len(await executor_context.get_messages()))
    print("team metrics:", coordinator.metrics_dict())


if __name__ == "__main__":
    asyncio.run(main())
