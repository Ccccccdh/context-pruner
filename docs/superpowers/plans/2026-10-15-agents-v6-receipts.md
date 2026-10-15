# Agents V6 receipt manifest implementation plan

> Execute inline with superpowers:executing-plans, TDD, and one independent final code review before paid execution.

Goal: preserve observed current source identifiers after archival, then run a bounded six-sample development probe.
Spec: ../specs/2026-10-15-agents-v6-receipt-manifest-design.md (user authorized implementation and small experiment).

Constraints: frozen V1/V4/V5 sources unchanged; no commits or changes to existing user modifications; common metadata rule for A0/A1/A2; 2048-byte manifest, 24000-byte wire, 16 HTTP/sample, 96 total HTTP, USD 1 conservative cap. New seeds 211/223, one repeat, rotated arms. No formal hypothesis confirmation or expansion. New operation-level paid claim outside per-freeze directories prevents duplicate freezes resetting budget.

## Task 1: receipt adapter
- [ ] Create tests/test_openai_agents_receipts_v6.py; witness missing import RED.
- [ ] Create context_pruner/adapters/openai_agents_receipts_v6.py. Inherit lifecycle filtering; construct metadata only from fully validated current read_document receipts, after trusted research closure. Insert an independent data message at closure's atomic-span boundary. Preserve raw authority and log pre/post projection.
- [ ] Verify no-closure, forged/historical, namespace, mixed atomic group, version change, repeated boundaries, checkpoint, manifest and input limits. Expected unittest PASS.

## Task 2: bounded runner and SDK validation
- [ ] Create tests/test_agents_receipt_manifest_v6.py; witness missing runner RED.
- [ ] Create experiments/runners/run_agents_receipt_manifest_v6.py; reuse V5 workspace/prompt/tools, inject adapter through serial scoped configuration restored in finally. Independently freeze exact six-sample matrix and bounds, source/runtime/schema hashes. Bind one paid operation to fixed path, validate mock provenance before paid entry.
- [ ] Run real SDK with scripted MockTransport, zero provider. Assert final messages and hashes contain exactly one observed-only manifest after closure, pairings valid, metadata not counted as restoration. Test freeze/cap/plan tamper and duplicate operation rejection.

## Task 3: review, freeze, paid probe and audit
- [ ] Run focused and related regression suites; fresh independent review. Fix Important/Critical findings using RED→GREEN.
- [ ] Freeze final candidate; actual SDK mock six samples; verify raw converted inputs synthetic-only and all accounting bounds. Preserve final source hashes and historical file hashes.
- [ ] Start six-sample natural provider run once. On infrastructure/budget/accounting failure stop, preserve denominator and consumed claim; do not replace or expand.
- [ ] Reconcile every dispatched/returned request, independent artifacts, manifest visibility, cache/output cost, quality, rereads and recovery; report descriptive per-task outcomes and limitations.

Review focus: forged receipts and raw-output mismatch; mixed tool groups; source version invalidation; final-wire budget after injection; operation/mock bindings cannot admit paid work using summaries alone.
