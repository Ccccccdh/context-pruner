# V7 archive-first read implementation and expanded development plan

Spec: ../specs/2026-10-15-agents-unified-evidence-read-design.md. User now authorizes offline implementation/validation followed by expanded paid development if sound. Execute inline with TDD and one independent code review before paid dispatch.

Scope ruling: the earlier spec ended at design/no paid. Current direct instruction authorizes implementation and an expanded mechanism-validation cohort. Keep fullpayload/tool schema unchanged; input equivalence is expected and is not a model-dollar-savings claim.
Workspace: continue existing checkout; only new V7 files and outputs, preserve all frozen code/data and user changes, no commits.

1. RED→GREEN core resolver: context_pruner/evidence_reader_v7.py + tests/test_evidence_reader_v7.py. Validate namespace/group/store/receipt/content/current-version proof, extract one entity, deep copy and fallback; unknown entities rejected.
2. RED→GREEN host bridge: experiments/runners/run_agents_archive_first_v7.py + tests/test_agents_archive_first_v7.py. Same read_document schema via SDK ToolContext (call_id only host-side), V6 projection unchanged. Count postresearch read requests, prepared hits, actual next-model delivery and source-body construction separately; source verification retained. Mock source/cache variants should produce identical A1/A2 HTTP bodies under the same scripted schedule.
3. Independent review, meaningful related regression, fresh frozen4instances seeds307/311/313/317 ×3arms1repeat=12. ActualSDK scripted mock112HTTP0provider, fullpairing/usage and proof/equivalence checks, synthetic egress only. A0 uses retained sources;A1/A2 share the same reread script.
4. Conditional paid expanded development: 12samples,16/sample192totalHTTP,USD2 conservative reservation,24000wirebytes, one fixed exclusive claim. Ordinary failures retained; infrastructure/integrity/budget failures stop with full denominator and unknown costs. No retries/replacement/expansion or automatic formal confirmation.
5. Fresh audit and report: independent artifacts, rawusage, readproofs/deliveries, equivalence vs. uncontrolled natural behavior, latency not invented. Preserve predecessor manifests. Outcomes descriptive across4instances; no autonomous restoration or cost-saving attribution to hostcache.

Review focus: forged receipt/archive metadata; mixed atomics; stale and cross-namespace proof; loader fallback validation; ToolContext schema equivalence and actual call_id attribution; sent counters on failed dispatch; duplicated freezes bypassing operation budget.
