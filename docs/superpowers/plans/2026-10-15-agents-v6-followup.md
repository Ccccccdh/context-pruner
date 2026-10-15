# V6 follow-up execution plan

Goal: complete an independent six-sample development cohort, assess the unchanged candidate and design a separate memory-first read facade if rereading persists.
Spec: docs/AGENTS_V6_FOLLOWUP_PROTOCOL_20261015.md and the existing V6 receipt design.
Method: inline executing-plans and TDD; one read-only independent entry-point review before paid calls. Preserve existing workspace and all old source/data; only add follow-up files, no commits.

1. Test new scoped cohort entry (RED), implement wrapper (GREEN): fixed seeds227/229, exclusive new claim, sameV6 behavior, original global config restored on exceptions, old evidence verified and bound. Files: tests/test_agents_receipt_manifest_v6_followup.py; experiments/runners/run_agents_receipt_manifest_v6_followup.py.
2. Review and local gates: focused regression plus genuine SDK mock6/66HTTP, source/schema/runtime/final-wire checks, synthetic-only egress, current key available without disclosure. Freeze before paid.
3. Run the full independent cohort once within96HTTP/USD1. On failure stop with intact denominator and consumed claim. Audit all rawusage, artifacts, bounds and manifests.
4. Assess per-task costs/quality/rereads; keep unknowns explicit and old failures separate. If rereads persist, document 2–3 read-entry approaches and choose host archive-first facade with version checking, ordinary read fallback, isolated attribution and testable error behavior. No new mechanism implementation or formal run in this scope.

Review focus: cross-freeze budget reset, scope restoration, predecessor drift, accidental original claim reuse, frozen-source modification, mock proof admission.
