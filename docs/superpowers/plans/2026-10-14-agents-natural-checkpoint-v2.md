# Agents Natural Checkpoint v2 Implementation Plan

> Execute inline with superpowers:executing-plans and test-driven-development; user selected steps1/2 directly.

**Goal:** 新SDK审计入口和有界同H自然模型对照。
**Spec:** docs/superpowers/specs/2026-10-14-agents-natural-checkpoint-v2-design.md
**Architecture:** 独立审计transport/ledger，checkpoint队列冻结与真实SDK runner，独立结果分析。
**Tech Stack:** installed Python3.13.11/openai-agents0.22.3/OpenAI SDK/httpx/unittest。

## Global constraints

历史和用户文件只读；新文件/ID；无Git提交。串行、72请求、$1费用保守预留、2048输出、24000bodybytes、SDK/client/transport隐藏重试关闭。失败留存，paid前冻结。

## Review focus

预算作用于底层请求；重试/并发/重复调用拒绝；原始usage未知不置0；源码/输入hash漂移停批；HTTP故障与本地验收异常完整保存；checkpoint相关性和端点范围明确；凭据不进输出或子进程。

### Task 1: Audited provider entry

Files: experiments/runners/agents_provider_audit_v2.py; tests/test_agents_provider_audit_v2.py.
Interfaces: RequestLedger.begin/finish; AuditedTransport.handle_async_request; request_scope; make_sdk_model.
- [ ] Missing module RED then budgets/HTTP/nullusage/sameSDKrequest GREEN.
- [ ] ActualSDK MockTransport proves request hashes and structured response conversion.

### Task 2: Checkpoint freeze and batch

Files: experiments/runners/run_agents_checkpoint_natural_v2.py; tests/test_agents_checkpoint_natural_v2.py.
Interfaces: select_checkpoints, build_plan, prepare_freeze, verify_freeze, run_batch.
- [ ] Rule selection/tamper/count/repair RED→GREEN.
- [ ] ZeroAPI plan/capturedSDKbody plus historical proof and all related regressions.
- [ ] Independent review; fix findings with RED/GREEN; freeze hashes and JSON protocol.
- [ ] Run paid queue using User-scope key without printing prefix; preserve all samples/failures and independent patch endpoint verdicts.
- [ ] Independent read-only aggregate and source drift checks; report limitations.
