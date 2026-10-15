# Agents natural lifecycle v3 implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. Steps use checkbox syntax. User requested direct execution, so design/plan review is incorporated into ongoing work without repeated authorization questions.

**Goal:** Complete roadmap stage3 and decide stage4 admission from unselected natural results.
**Architecture:** Frozen synthetic workspace tasks + trusted SDK function tools + existing lifecycle adapter and durable HTTP audit.
**Tech Stack:** Python unittest,Agents SDK0.22.3,httpx,DeepSeek Chat Completions.
**Spec:** docs/superpowers/specs/2026-10-14-agents-natural-lifecycle-v3-design.md

## Global constraints
Six samples;24 requests/sample;144/batch;USD2;32000 wire bytes;2048 output;TTL2. No old source changes, no hidden answers in prompts, no forced missing or fixed natural schedule, no automatic paid retry batches. Existing working-tree preference retained.

## Tasks

1. Task contracts/independent admission: create experiments/runners/agents_lifecycle_tasks_v3.py and tests/test_agents_lifecycle_tasks_v3.py. RED tests current reference pass,empty/wrong-version fail,third deterministic candidate; GREEN pure evaluator and controlled workspace tools. Outputs feed task2, same manifest and artifact schema.
2. Lifecycle runner and freeze: create experiments/runners/run_agents_lifecycle_natural_v3.py and tests/test_agents_lifecycle_natural_v3.py. RED freeze drift,budget guards,input parity,negative/wrong-version restoration,real SDK mock tool loop; GREEN immutable freeze,per-boundary evidence and finalized failure ledger. Run related90 + new tests. Task1 host receipts must exactly match adapter allowlist/schema.
3. Independent review and one fix pass: request fresh reviewer focused on answer leakage,source/version and body binding,tool atomicity,real autonomous vs scripted mock,request caps and evidence on failure. Fix Important/Critical with RED→GREEN. Freeze only after review/regressions.
4. Execute six frozen natural samples via approved network,save summary/raw traces and descriptive analysis. Infrastructure/accounting violations stop; never add tasks. Report price range,token/quality/recovery paths and limitations. Evaluate stage4 conditional admission; do not call stage5 formal confirmation without stable baseline/candidate design.

## Review focus
Model-controlled artifacts cannot change verifier/source; historical evidence must not satisfy current dependency; archived full history stays authoritative across SDK calls; HTTP requests bind visible input including tools; missing provenance does not expose expected rows; sample limit and budget cannot reset on followup.
