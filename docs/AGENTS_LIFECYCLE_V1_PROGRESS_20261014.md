# SDD ledger — plan: docs/superpowers/plans/2026-10-14-agents-evidence-lifecycle.md

User requested direct implementation; no repeated design approval. Implementer inline.

Pre-flight: Task 1 independent; Task 3 consumes Task 2 filter/hooks; archive backend lifecycle owned by trial. No conflicting interfaces.

Ruling: existing workspace contains untracked essential tooling/environments; add opt-in files in place, preserve main and all dirty files, no automatic Git commits. Skill scripts assume bash/git commits; Windows execution uses this persistent ledger and unittest equivalents.

Baseline: .venv has SDK 0.22.3 and no pytest. unittest old SDK adapter + middleware: 10 tests OK. pytest attempt failed only because package absent; no installation required.

Task 1: complete — missing-module RED, 2 unittest GREEN; real replay 78/78 exact, 15 changed calls; independent amendment saved. Old batch metadata mismatch retained.
Task 2: implementation and boundary tests complete — missing-module RED, initial7 then15 unittest GREEN; independent review pending.
Task 3: controlled SDK implementation complete — missing-module RED then6 arm/backend subcases GREEN; actual Runner matrix42 local model calls,36 tool calls,0 provider. Independent review/final regression pending.

Test-only correction: edge test insertion initially caused IndentationError; persistence block restored in place,15 tests OK. No production fix or historical test adjustment.

Independent review: agents_lifecycle_review, read-only;5 Important reproduced,0 Critical,0 Minor. No second review. Reviewer declined natural-model quality/cost, precise tokenizer, unsupported streaming/handoff, old CrewAI: these remain explicitly unclaimed.

Final fix pass:
- History integrity: active mutation/deletion RED→GREEN; append-only per-item SHA prefix persisted.
- Source consistency: checkpoint invalid pairs/rehashed group/source metadata and corrupt archive sources RED→GREEN; verified receipt-derived bindings, namespace/group ID/history membership.
- Unknown tool group: unfinished computer_call and mixed unknown parallel batch RED→GREEN; preserve all unknown groups.
- SDK remote history: missing guarded entry RED→GREEN for3 flags; run_sync rejects before Model, direct configuration caller contract documented.
- Budget: negative reservation bypass RED→GREEN; validate nonnegative integers before state mutation.
- Failure evidence: missing failure.json RED→GREEN; retain observed input/events, do not overwrite run directories.

Ruling: review recommended re-review; executing-plans directs one independent review thenTDD fix verification. We follow one review and final combined regression, disclose no second independent sign-off. Cost if wrong: remaining author blind spots before natural-model use.

Ruling: localSDKModel validates engineering only; real-provider causal and natural-code quality experiments are deferred to independently budgeted/frozen development. Cost if wrong: cannot yet claim net收益 or自主缺失发现.

Task 2: complete — 21 unittest cases; source and SDK wrapper fixes verified.
Task 3: complete — 3 SDK test cases (six arm/backend subcases, three flag rejects, failure retention), final SDK matrix 42/36/0. Final related regression 68/68; old 16 file SHA verification recorded in final_gate.json.
