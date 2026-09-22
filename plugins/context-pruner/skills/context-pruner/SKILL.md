---
name: context-pruner
description: Compress, resume, or audit exported Agent message histories with the local Context-Pruner lifecycle engine. Use when the user asks to reduce an Agent context JSON file, inspect compression metrics or lifecycle state, prepare a reproducible before-and-after context experiment, or integrate Context-Pruner into a Python Agent. This skill operates on explicit files and integration code; it does not replace Codex's internal context compaction.
---

# Context-Pruner

Use the repository's local Python package and keep all context data on the user's machine.

## Compress an exported history

1. Confirm the source is a JSON array of `{role, content}` messages or an object containing a `messages` array.
2. Pick budgets from the target model's usable context. Reserve instructions, tool schemas, and response tokens separately.
3. Run the bundled script, resolving its path relative to this skill:

```powershell
python ..\..\scripts\compress_context.py compress --input history.json --output compressed.json --soft 6000 --hard 8000 --target 5000 --reserved-tokens 1200
```

4. Report the full/served token estimates, gross savings, budget violations, compression count, archive count, and output paths.
5. Preserve `lifecycle_state` or use `--state-out` when later calls must resume the same lifecycle.

Do not send history to a network service unless the user explicitly asks. Do not overwrite the source history unless the user explicitly requests it.

## Integrate a Python Agent

- For a custom loop, use `ContextPrunerMiddleware.before_model`, `after_model`, `after_tool`, `on_error`, and `finalize`.
- For LangGraph, use `LangGraphContextAdapter`.
- For the OpenAI Agents SDK, use `create_call_model_input_filter` with `RunConfig.call_model_input_filter`.
- For AutoGen AgentChat, use `create_autogen_model_context` as the Agent's `model_context`.
- For an AutoGen team, use one `AutoGenTeamContextCoordinator`, register each Agent, and route information through `publish_private`, `publish_shared`, or `publish_handoff`.
- For any new Agent surface, run `context-pruner assess` first and select native, bridge, explicit, or unsupported integration from the reported capabilities.
- The OpenAI Agents adapter compresses ordinary messages while protecting Responses reasoning, function-call, function-call-output and following assistant items as lossless ordered groups. Check the unmatched-call and restore-failure metrics before accepting an integration result.

Always retain the application's authoritative full history. Send only the middleware's returned message view to the model.

## Interpret scope correctly

This Codex skill provides an explicit local workflow for exported histories and integration code. It cannot intercept or replace the host Codex application's private internal compaction pipeline.
