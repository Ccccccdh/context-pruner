# r19 real-capture cache-hit decomposition (zero API; citable)

source: per-attempt `full_attempt_capture` of `runs/stage5-crewai/crewai-r19-fullcapture-acquisition-api-01` (real `usage`; each row carries two copies, so the totals count ONE copy only)

## 1. totals (real, not mock)
- attempts 21; input **29513** tokens (hit **20224**, miss **9289**, hit share **68.53%**); output 1152 tokens
- off-peak attempts 21/21

## 2. split by new-content vs reused-prefix
- first attempt of each role (3 attempts): input 4469, hit 0 (0.0%), mean new messages 24.0
- later attempts (18): input 25044, hit 20224 (80.75%), mean reused prefix messages 18.67, mean new messages 2.33

| attempt index | attempts | input | hit | hit share | mean reused prefix | mean new messages |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 3 | 4469 | 0 | 0.0% | 0.0 | 24.0 |
| 1 | 3 | 4785 | 4224 | 88.28% | 24.0 | 2.0 |
| 2 | 3 | 5080 | 4608 | 90.71% | 26.0 | 2.0 |
| 3 | 3 | 5382 | 4736 | 88.0% | 28.0 | 2.0 |
| 4 | 3 | 5734 | 5120 | 89.29% | 30.0 | 2.0 |
| 5 | 3 | 1862 | 0 | 0.0% | 0.0 | 4.0 |
| 6 | 3 | 2201 | 1536 | 69.79% | 4.0 | 2.0 |

## 3. do the hits come from the shared prefix or from re-sends?
- attempts with ZERO new messages (a pure re-send of the same frame): 0 (0.0%); attempts that appended content: 21
- reading: the hit is driven by the REUSED PREFIX.  Every call carries the previous frame's leading messages unchanged, and only the appended observation is the miss part, so the hit share rises with the attempt index (table above).
- a guard refusal re-sends the same frame, which adds zero new messages and is therefore billed entirely at the hit price: re-sends are cheap, not expensive.  A host that forks long identical prefixes across arms should see an equal or higher hit share.

## 4. cost (official input tiers plus the stated output stand-in)
- measured peak-equivalent $0.001972; off-peak billed $0.000986
- all-miss upper bound: peak $0.004945 / off-peak $0.002473 = about 2.51x the measured cost
- all-hit lower bound: peak $0.000607

## 5. citation limits
- one host, one arm, 21 attempts, 3 tasks: a DIRECTION, not a transferable share; do not move the percentage to another host.
- this batch is not a saving or quality claim: citable_as_saving=false and citable_as_quality_equivalence=false.
- the mock run's 21 simulated requests and 21,017 simulated tokens are NOT provider usage; every number in this section comes from real `usage`.
