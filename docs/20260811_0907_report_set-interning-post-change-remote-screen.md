# Set-interning post-change remote screen report

Rules reviewed: BR-001 through BR-026; applied as frozen in `docs/20260811_0855_plan_set-interning-post-change-remote-screen.md`.

Date: 2026-08-11

Host: `ubuntu@51.159.202.218`

Baseline: `a12a849 Add set-interning benchmark arms`

Patched implementation: `1680f6f Move set interning allocation outside object lock`

## Decision

The campaign passes as a compatibility, correctness, and mechanism screen. It does not establish a throughput or latency improvement.

All 26 planned workload repetitions were accepted by the Docker summarizer, with no process failures, swap, or hardware-cohort split. Every row was classified `harness/under-saturated`, so BR-011 excludes the observed request rates and whole-tree CPU figures from a performance claim.

The patched diagnostic row does show the intended internal shape: one million unique-set attaches completed with candidate and table allocation outside `obj_mtx`, the legacy inside-lock set-allocation counters remained zero, migration converged, and the maximum bounded table publication/migration observation was 98 us. The baseline revision predates the new interning-specific VSC timers, so B-lock cannot supply a like-for-like old table-grow maximum. Its request-attach mutex-wait tail provides only indirect evidence: 10,711 us maximum and one wait above 10 ms, versus 1,354 us and none above 10 ms for P-lock. The single diagnostic repetition and lack of baseline hold attribution prevent turning that observation into latency acceptance.

## Execution and provenance

Remote setup was rerun in `/home/ubuntu/cachetag-bench-set-interning-20260811`. The six rows ran sequentially in the frozen order B1, P1, B2, P2, B-lock, P-lock. Every source switch used `CACHE_TAG_REMOTE_SYNC=1` and a fresh build with `SKIP_BUILD=0`.

The baseline rows all recorded cachetag build-input SHA-256 `1eea066407ab68d6bfb9eac626280d59a848e3c6876beb884e26ad8c2c0ca823`. The patched rows all recorded `aedf7f506ffd5c98570af1ea38f16d982606a2c9cd20b7c6acb2daf4e78b2dbb`. Every row recorded Vinyl build-input SHA-256 `eb02e251a2c79341d13ad7e99190b3f50571c7b514eb7184468d02beaa5c8a06`, Default storage, and set interning enabled. The source syncs were made from the detached `a12a849` worktree and the `1680f6f` implementation checkout respectively, but `metadata.env` leaves `cachetag_revision` empty; the artifacts bind each arm to a stable content hash rather than independently recording the Git commit-to-hash mapping.

All six downloaded archives matched their adjacent remote SHA-256 records. The artifacts were extracted only for inspection and summarized inside `vinyl-cache-ubuntu-build`; no benchmark helper or build was run on the host.

## Validity and environment

All rows belong to one hardware group: AMD EPYC 4345P, 8 physical cores and 16 logical CPUs, 63,372,032 KiB memory, kernel 7.0.0-15-generic. The host recorded the `powersave` governor with boost enabled throughout.

- B1, P1, B2, and P2: 6/6 valid repetitions each.
- B-lock and P-lock: 1/1 valid repetition each.
- Process results: 26 passed, 0 failed.
- Swap: none in every row.
- Minimum available memory: at least 95.74%.
- CPU: average maxima 64.36% to 65.24%; run maxima 82.67% to 83.15%.
- Limiting-factor classification: `harness/under-saturated` for every row.

P2's third mostly-shared repetition was structurally valid but performance-ineligible: it reached only 26.68% average whole-tree CPU and 296% maximum vinyld CPU while producing 55,531.91 requests/s over 18.01 s. The other P2 shared repetitions were 80,152.39 and 79,910.31 requests/s with roughly 675% vinyld CPU. The repetition remains in the retained artifact, but its rate is excluded and reinforces the decision not to interpret these rows as throughput-limited measurements.

## Uninstrumented observations

The values below are diagnostic only because the rows were under-saturated.

| Profile | B1 median requests/s | P1 median requests/s | B2 median requests/s | P2 median requests/s | Combined baseline median | Combined patched median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Mostly shared | 80,552.13 | 80,312.31 | 80,131.00 | 79,910.31 | 80,404.88 | 80,151.87 |
| Mostly unique | 79,593.77 | 79,015.86 | 79,348.81 | 79,117.30 | 79,443.75 | 79,066.58 |

The combined medians differ by about -0.31% for mostly shared and -0.47% for mostly unique. Those small changes are not accepted as regressions or improvements: the harness was under-saturated, the P2 outlier shows non-source noise, and no production latency distribution was measured.

Functional and memory outcomes matched across the arms:

- Mostly shared: one live set, 1,000,000 references, 999,999 hits, one miss, 72 B set storage, and 512 B table storage.
- Mostly unique: 1,000,000 live sets and references, zero hits, 1,000,000 misses at the judged snapshot, 68.66 MiB set storage, and 8.00 MiB table storage.
- Every workload published 1,000,000 of 1,000,000 objects.
- Final object-resize state was inactive with zero retiring and detached bytes.
- The patched mostly-shared rows recorded 999,999 unpublished candidate discards, matching canonical reuse.

## Lock-instrumented mechanism observations

The table uses the pre-purge snapshot for the one-million-object load; the final post-purge snapshots add one attach/acquire observation without changing any maximum or conclusion.

| Metric | B-lock | P-lock |
| --- | ---: | ---: |
| Request-attach `obj_mtx` calls | 1,000,000 | 1,000,000 |
| Total request-attach wait | 1,684,991 us | 2,565,426 us |
| Maximum request-attach wait | 10,711 us | 1,354 us |
| Waits above 1 ms | 7 | 1 |
| Waits above 10 ms | 1 | 0 |
| Intern acquire calls | unavailable | 1,000,000 |
| Intern acquire total / maximum | unavailable | 458,790 us / 142 us |
| Bounded table publication/migration calls | unavailable | 1,018,229 |
| Bounded table publication/migration total / maximum | unavailable | 125,004 us / 98 us |
| Inside-lock set-allocation calls / time | unavailable | 0 / 0 us |
| Outside-lock candidate allocation calls / total / maximum | unavailable | 1,000,000 / 109,954 us / 163 us |
| Outside-lock table allocation calls / total / maximum | unavailable | 24 / 199 us / 42 us |

P-lock finished with no active migration, no old-table bytes, no detached-set or detached-table bytes, no table-allocation or growth failures, and no unique-workload candidate discards. The 24 outside-lock table allocations are consistent with bounded growth to the final 8 MiB table.

The lower maximum request-attach wait is directionally consistent with removal of a monolithic in-lock rehash, while the higher total wait and higher counts above 50 us and 250 us show why a single diagnostic row must not be presented as a blanket contention improvement. A direct old/new table-grow hold comparison would require a baseline carrying equivalent interning-specific timers or another approved diagnostic method.

## Review outcome

Parallel standards and specification reviews found and corrected four substantive issues before this campaign: attach error paths now wake maintenance after publishing migration state; table publication revalidates the captured generation; worker migration contributes to table-grow timing; and detach/reset advances the generation so a stale prepared table cannot republish after cold teardown. Cleanup paths were consolidated, duplicate test-hook declarations removed, and the legacy always-zero VSC descriptions clarified.

The targeted Docker regression set `pm00030` through `pm00033` passed 4/4 after those changes. Two specification-level acceptance gaps remain: there is no deterministic VTC that overlaps an intern-table allocation retry with object-segment or side-map mutation, and the old/active-table lookup test does not prove invalidation from each known table separately. These are test-coverage gaps rather than observed failures in this campaign.

Unrelated untracked root READMEs, advisory material, `STRICTNESS-GUARANTEE.md`, `TESTING.md`, and `TODO.md` were excluded from the implementation and benchmark-plan commits.

## Artifacts

Fetched bundles and checksum files are retained under `benchmarks/remote-results/20260811_51.159.202.218/set-interning-post-change/`, grouped by the six frozen row names.
