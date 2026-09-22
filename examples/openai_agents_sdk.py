"""OpenAI Agents SDK integration using the official pre-model input filter."""

from agents import Agent, RunConfig, Runner

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import create_call_model_input_filter


context_filter = create_call_model_input_filter(
    ContextPluginConfig(budget=ContextBudget(6000, 8000, 5000)),
    task_state="回答用户问题",
    fixed_reserved_tokens=1200,
)
agent = Agent(name="Assistant", instructions="Answer concisely.")
result = Runner.run_sync(
    agent,
    [{"role": "user", "content": "Explain context lifecycle management."}],
    run_config=RunConfig(call_model_input_filter=context_filter),
)

print(result.final_output)
print(context_filter.metrics_dict())
