# Agents Evidence and Lifecycle Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline with test-driven-development. User has explicitly selected direct execution.

**Goal:** 完成 Agents 证据勘误、同历史输入对照及真实 SDK 零 API 生命周期闭环。

**Architecture:** 独立 evidence review runner；独立 opt-in SDK evidence lifecycle + hooks；本地 SDK 实验入口。历史模块只读。

**Tech Stack:** 本机 Python 3.13.11、unittest、installed openai-agents 0.22.3、既有 Memory/SQLite archive。

**Spec:** docs/superpowers/specs/2026-10-14-agents-evidence-lifecycle-design.md

## Global Constraints

- 新文件、新实验 ID；旧源代码、用户修改和历史产物哈希不变。
- 当前工作区原地执行，不自动提交或清理其他会话文件。
- 不调用付费模型；真实 SDK + 固定 Model 的结果仅为工程证据。
- 恢复需要宿主工具回执、当前版本、原始完整工具组；TTL 按过滤调用计数。
- 独立输出目录拒绝覆盖；失败和无法重建的检查点保留。

## Review Focus

1. 伪造工具输出/重复 call_id 不能变成可信阶段或恢复事件。
2. 改版本、改内容、跨 namespace checkpoint 不能恢复错误来源。
3. 并行工具、reasoning、未配对调用、pin 和最近组不得破坏原子协议。
4. TTL 边界、重启、重复缺失信号不能无限扩上下文。
5. 归档脱敏、恢复预算不足、无命中必须诚实拒绝且保留权威历史。

### Task 1: Evidence review and matched checkpoint replay

**Files:** experiments/runners/agents_evidence_review_v1.py; tests/test_agents_evidence_review_v1.py; integrations/openai_agents/AGENTS_EVIDENCE_AMENDMENT_20261014.md.

**Interfaces:** summarize_batch(payload) -> dict; compare_checkpoint(messages) -> dict; reconstruct_checkpoints(root,payload) -> list[dict].

- [ ] Write tests for metadata mismatch, changed/saving coverage and candidate no-growth gate.
- [ ] Run unittest and observe missing module RED.
- [ ] Implement audit, read-only source reconstruction with digest gate and output SHA manifest.
- [ ] Run unit tests and real step5b replay; retain mismatch rows, never silently approximate.

### Task 2: SDK versioned lifecycle adapter

**Files:** context_pruner/adapters/openai_agents_lifecycle_v1.py; tests/test_openai_agents_lifecycle_v1.py.

**Interfaces:** observe_tool_result(call_id,tool_name,result); filter_items(items,instructions); export_state/from_state; sdk_hooks().

- [ ] Write RED tests for stage archive, exact group restore, missing signal, TTL, versions, pin, budget, orphan preservation, dedup and persistence.
- [ ] Implement trusted receipt registry, atomic group projection, existing archive integration, bounded recovery, state export and SDK ModelInputData callable/RunHooks bridge.
- [ ] Verify all tests and unchanged old adapter/middleware/archive tests.

### Task 3: Real SDK Runner controlled closure

**Files:** experiments/runners/run_agents_lifecycle_v1.py; tests/test_agents_lifecycle_sdk_v1.py; docs/AGENTS_LIFECYCLE_V1_RESULT_20261014.md.

**Interfaces:** run_local_trial(arm,backend,out_dir) -> dict; consumes Task 2 SDK filter/hooks.

- [ ] Write failing real SDK test comparing A0/A1/A2 with actual model inputs and file verification.
- [ ] Implement local Model and trusted function_tool flow, model-driven tool execution and TTL follow-up; network/tracing disabled.
- [ ] Run full Memory/SQLite trial matrix; output model inputs, event receipts, independent dependency verdict and zero-provider ledger.
- [ ] Independent code review, fix Important findings with RED/GREEN; finalize combined regression and old SHA verification.
