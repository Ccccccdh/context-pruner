"""Offline CrewAI 1.x hook example using a deterministic local LLM."""

from __future__ import annotations

from crewai import Agent, BaseLLM
from crewai.tools import tool

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import create_crewai_context_adapter


class ReplayLLM(BaseLLM):
    """Two-response provider that makes the example network-free."""

    def __init__(self) -> None:
        super().__init__(model="context-pruner-replay", provider="custom")
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
        return self.responses.pop(0)


@tool("lookup_incident")
def lookup_incident(incident_id: str) -> str:
    """Return the deterministic incident health record."""
    return f"{incident_id} degraded; required decision is ROLLBACK"


def main() -> None:
    agent = Agent(
        role="Incident analyst",
        goal="Use evidence to select the safe incident decision.",
        backstory="You are a deterministic operations analyst.",
        llm=ReplayLLM(),
        tools=[lookup_incident],
        allow_delegation=False,
        max_iter=4,
        verbose=False,
    )
    context_pruner = create_crewai_context_adapter(
        ContextPluginConfig(
            budget=ContextBudget(
                soft_limit_tokens=800,
                hard_limit_tokens=1500,
                target_tokens=700,
            )
        ),
        task_state="Incident INC-2048 requires one evidence-backed decision.",
        agent_roles=["Incident analyst"],
    )

    with context_pruner.attached():
        result = agent.kickoff(
            "Inspect INC-2048 with the available tool and report the decision."
        )

    print(result)
    print(context_pruner.metrics_dict())


if __name__ == "__main__":
    main()
