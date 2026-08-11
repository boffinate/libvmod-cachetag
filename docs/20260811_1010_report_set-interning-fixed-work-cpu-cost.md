# Set-interning fixed-work CPU-cost report

Rules reviewed: BR-001 through BR-026; applicable: BR-002, BR-008, BR-011, BR-013, BR-014, BR-016 through BR-020, and BR-023 through BR-026.

## Answer

Complete five-tag set interning adds a small but resolved amount of cache-process compute during all-miss attachment:

- When every object shares one complete five-tag set, the two interleaved enabled-versus-disabled comparisons were **+0.370%** and **+0.363% retired instructions/object**, or about **1,264–1,290 additional instructions/object**.
- When every object has a unique complete five-tag set, the comparisons were **+0.603%** and **+0.622% retired instructions/object**, or about **2,108–2,173 additional instructions/object**.

The sign and magnitude reproduced. Same-code row-to-row median drift was at most 0.058% for the shared shape and 0.064% for the unique shape, making the BR-024 two-times floors 0.117% and 0.128%. Both comparisons clear those floors. They also exceed twice the largest within-row instruction spread, a more conservative secondary check. The enabled cost is therefore resolved on the primary algorithmic-compute metric; it is not merely “within noise”.

This measures the current complete-membership-set interning implementation in `1680f6f`, including tag sorting/hashing, registry lookup, refcounting and, for unique sets, candidate publication/table maintenance. It excludes retained-memory differences from the judgment.

## Primary evidence

Each value below is retired cache-process instructions per successfully published object during the acknowledged load phase. The repetitions are shown in run order.

| Row | Shape | Repetition 1 | Repetition 2 | Repetition 3 | Median | Spread |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| B1 disabled | shared | 348,030.91 | 348,587.40 | 348,534.75 | 348,534.75 | 0.160% |
| P1 enabled | shared | 349,757.40 | 349,829.86 | 349,824.77 | 349,824.77 | 0.021% |
| B2 disabled | shared | 347,963.65 | 348,469.02 | 348,356.83 | 348,356.83 | 0.145% |
| P2 enabled | shared | 349,401.42 | 349,621.54 | 349,620.33 | 349,620.33 | 0.063% |
| B1 disabled | unique | 349,942.18 | 349,597.99 | 349,334.58 | 349,597.99 | 0.174% |
| P1 enabled | unique | 351,705.71 | 351,615.20 | 351,771.10 | 351,705.71 | 0.044% |
| B2 disabled | unique | 349,494.33 | 349,373.92 | 348,706.95 | 349,373.92 | 0.226% |
| P2 enabled | unique | 351,547.40 | 351,282.58 | 351,628.15 | 351,547.40 | 0.098% |

The same-code median movements were -0.051% B1→B2 and -0.058% P1→P2 for shared sets, and -0.064% and -0.045% for unique sets. The largest within-row instruction spreads were 0.160% shared and 0.226% unique; all paired deltas also exceed twice those spreads.

## Secondary observations

For the shared shape, task-clock, cycles, reference cycles and driver wall time did not reproduce the same sign across both comparisons, so no overhead is claimed from them.

For unique sets, all secondary medians increased in both comparisons: task-clock +1.43%/+2.09%, cycles +1.44%/+2.34%, reference cycles +1.44%/+2.24%, and contextual driver wall time +0.89%/+1.51%. Their per-row variability was materially larger than for retired instructions, and the comparisons do not consistently clear twice that variability. They are directional support only, not accepted point estimates.

No throughput or saturation claim is made. The experiment deliberately measures identical completed work and process-scoped compute; whole-host saturation is not required for that question.

## Validity and provenance

The campaign ran sequential fresh-build rows B1, P1, B2 and P2 on `ubuntu@51.159.202.218`, with three repetitions of each shape per row: 24 fixed-work repetitions total. All 24 passed the existing Docker summarizer and the phase-stat summarizer; there were no invalid or failed processes and no swap activity.

Every repetition completed exactly 1,000,000 driver requests and backend objects, published 1,000,000 cachetag objects and 5,000,000 membership edges, and reported zero driver errors, attach failures, parse errors, limit rejections and LRU nukes. Enabled shared snapshots contained one live set and 1,000,000 references; enabled unique snapshots contained 1,000,000 live sets and references. No mutex instrumentation was enabled.

All four rows used cachetag build-input SHA-256 `aedf7f506ffd5c98570af1ea38f16d982606a2c9cd20b7c6acb2daf4e78b2dbb` and Vinyl build-input SHA-256 `eb02e251a2c79341d13ad7e99190b3f50571c7b514eb7184468d02beaa5c8a06`. The only intended build switch was set interning disabled for B1/B2 and enabled for P1/P2. Common configuration was Default storage, eight clients, five tags/object, xkey/no-index off, no warm phase, no residency sample, and `task-clock,instructions,cycles,ref-cycles` at 100% running. The hardware/power cohort remained an AMD EPYC 4345P with 8 cores/16 threads, `powersave` governor, boost enabled and 5.582301 GHz recorded maximum frequency.

The artifact revision fields are blank, so the build-input hashes—not embedded Git revisions—are the authoritative within-campaign source identity. The local checkout used implementation commit `1680f6f` and harness commit `83b773f`, but those commit IDs are checkout provenance rather than artifact-contained proof.

BR-026 caveat: the frozen plan specified the exact four-row, 24-repetition execution and stop conditions, but omitted an expected wall-clock duration and an explicit row-to-claim map. Execution stayed within the frozen matrix and no extra or replacement row was added.

Fetched archives and SHA-256 checksums are under `benchmarks/remote-results/20260811_51.159.202.218/set-interning-fixed-work-cost/`. The Docker/remote validation log is `docs/20260811_0958_log_set-interning-fixed-work-counter-seam.md`.
