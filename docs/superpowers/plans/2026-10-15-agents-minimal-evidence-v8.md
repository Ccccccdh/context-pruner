# V8 Minimal Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved three-arm deterministic record projection and conditionally run one bounded development cohort.

**Architecture:** Preserve frozen V7/V6 files. New V8 projection consumes verified whole-source evidence; a new SDK tool returns full or selected records under identical lifecycle configuration. Separate audit reconstructs all evidence and wire/accounting.

**Tech Stack:** Python3.13, existing OpenAI Agents SDK/httpx/unittest.

**Spec:** ../specs/2026-10-15-agents-minimal-evidence-v8-design.md

## Global Constraints

- Only new V8 files; no commits/pushes, no change to prior manifests or claims.
- B0/B1/B2 identical schema/prompt, lifecycle enabled, autonomous recovery disabled. B0 full/source; B1 selected/source; B2 selected/archive-first.
- source_version=whole sourceSHA; payload_sha256=returned textSHA; new kind=registry_record_evidence.
- Seeds401/409/419/421,12samples,16HTTP/sample,192total,24000wirebytes,USD2 all-miss reservation,one exclusive claim,no retries/replacements.
- Offline ≥50% response-text bytes and ≥15% aggregate final-wire reduction B1/B0; B1/B2 body equality.
- Screen: complete/no unknown usage; B1/B2 quality4/4; geometric mean B1/B0 total-token and both price ratios≤.90; at least3/4 instances lower in both token/cost. No automatic confirmation run.

## Review Focus

- Projected text mistaken for whole-source evidence: core test distinguishes both hashes and refuses forged whole source.
- Cache corruption/stale/namespace mismatch: SDK test validates fallback through frozen V7 resolver.
- Natural choice of full tool or no record tool: retain all samples and ordinary reads; analysis includes no-use outcomes.
- Prepared hit without successful provider input: injected presend rejection and transport failure must preserve counters/unknown usage.
- Changed freezes used for repeat budgets: fixed operation claim, explicit cap/schema/task/source manifest checks.

### Task 1: Pure projection

Files: create context_pruner/evidence_projection_v8.py, tests/test_evidence_projection_v8.py.
Consumes verified full evidence dict and explicit entity/codes/revision/closed/full arguments. Produces project_records(payload,entity,codes,revision,closed,full=False)->dict and validate_request(...).
- [ ] Write tests for all24code values/request ordering,3-code sufficiency,metadata/hash semantics,unknown/duplicate/empty/type errors,bad source,phase/revision errors,deepcopy.
- [ ] Run unittest module: expect import RED.
- [ ] Implement deterministic validation/projection without work_order/reference access.
- [ ] Run module: expect all GREEN; ledger results.

### Task 2: SDK facade and independent audit

Files: create experiments/runners/run_agents_minimal_evidence_v8.py, experiments/audits/agents_minimal_evidence_v8_audit.py, tests/test_agents_minimal_evidence_v8.py, docs/AGENTS_V8_PROTOCOL_20261015.md.
Consumes project_records and frozen V7 resolver. Produces prepare/verify/run_batch/audit_output CLI with scoped base runner patches.
- [ ] Test12mock samples/common schema and initialbody; all qualityPASS; source/cache equal selected payloads; whole finalbody B1/B2 equal; bytes gates; no lifecycle recovery.
- [ ] Test invalid requests,corrupt archivefallback/source failure,immutable source,exclusive capclaim,presend rejection,unknownusage transport error and tampered proof/projection.
- [ ] Run SDK tests: expect missing module RED.
- [ ] Implement facade with host call_id/proof/counter persistence; every group uses lifecycle armA1 internally with externalB labels. Capture common instruction addition before frozen parity.
- [ ] Implement audit reconstructing requests/journal/settings/costs/manifest/source/evidence/quality and independently recount validation calls.
- [ ] Run both modules GREEN; repair causes without relaxing approved gates.

### Task 3: Review and final offline admission

- [ ] One fresh independent final review using most capable reviewer; parent fixes Important findings RED→GREEN in one pass; minors ledger.
- [ ] Related previous108 tests plus V8 modules pass; preserve all predecessor hashes.
- [ ] Fresh final freeze and SDK mock pass all byte/equality/proof gates. Inspect synthetic egress and existing key availability, never print key.
- [ ] If a gate fails, archive failure and stop before paid; do not modify a consumed freeze.

### Task 4: One paid development screen and archive

Files: create experiments/audits/analyze_agents_minimal_evidence_v8.py and result/status/manifest under new V8 root.
- [ ] Recheck official prices against frozen rates. Execute one authorized foreground queue within statedUSD2/12sample caps,stop on infra/integrity/budget.
- [ ] Fresh audit; recompute per-instance B1/B0 total tokens/costs,quality,host reads and selection sufficiency. All providers/unknown denominators retained.
- [ ] Save result,screen gate and hash manifest; verify predecessor manifests unchanged.
- [ ] If screen passes, prepare a concrete confirmation planning note with uncertainty/sample-size limitations and proposed separate budget for review. If fails, document cause and stop without new paid candidate.

Plan self-review: all approved spec sections covered; signatures consistent; prior full-source resolver never receives projected evidence; natural flow and controlled flow separated. Native execution follows user's “已确认，直接开始”; plan is saved for review while authorized reversible offline work proceeds. Existing checkout preference preserved because frozen prerequisites/untracked files bind absolute paths; only new files, no branch changes. Future confirmation/cross-task execution remains outside this plan.
