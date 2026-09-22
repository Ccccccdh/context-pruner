"""Minimal framework-neutral integration example."""

from context_pruner import ContextBudget, ContextPluginConfig, ContextPrunerMiddleware


middleware = ContextPrunerMiddleware(
    ContextPluginConfig(budget=ContextBudget(6000, 8000, 5000)),
    task_state="完成用户当前任务",
)

history = [{"role": "user", "content": "请分析这个任务。"}]
prepared = middleware.before_model(
    history,
    reserved_tokens=1200,  # instructions, tool schemas and response reserve
)

# Send prepared.messages to your model client, then record the result:
model_output = {"role": "assistant", "content": "分析完成。"}
middleware.after_model(model_output)
history.append(model_output)

print(prepared.metrics)
