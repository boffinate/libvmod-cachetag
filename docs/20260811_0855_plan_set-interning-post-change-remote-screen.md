# Set-interning post-change remote screen

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (fresh-build peaks excluded), BR-002 (B1, P1, B2, P2 interleave), BR-003 and BR-004 (final flushed snapshots for retained counters), BR-008 (one sequential benchmark session on the host), BR-009 (generated VTCs use `debug=none`), BR-011 (invalid or under-saturated rows are correctness evidence only), BR-013 (whole-tree CPU is directional), BR-014 (all comparisons rerun on the rebuilt host cohort), BR-016 (fresh provenance-checked build for every source switch), BR-017 (fixed source and harness identities per arm), BR-018 (fail loud; no automatic replacements), BR-019 (deterministic object/tag volumes), BR-020 (8 GiB Default-storage envelope and attach-only scope), BR-023 (raw maxima reported without a release tail claim), BR-024 (two repetitions of each source arm provide the same-code noise observation), BR-025 (record governor and boost state), and BR-026 (frozen six-row budget and stop condition).

**Campaign class:** Decision round for the narrow outside-`obj_mtx` design decision. It is not release acceptance.

**Host:** `ubuntu@51.159.202.218`, rebuilt on 2026-08-11 and therefore a new hardware cohort. No rate or latency value from the deleted 2026-08-10 host instance will be compared with this campaign.

**Baseline source:** `a12a849 Add set-interning benchmark arms`, the implementation measured before allocation/migration hardening.

**Patched source:** `1680f6f Move set interning allocation outside object lock`.

## Claims and exclusions

The uninstrumented rows test whether the hardening changes achieved cold-attach rate or whole-tree CPU materially at the one-shared-set and all-unique-set endpoints. The instrumented rows test the narrower mechanism claim: the unique-set endpoint should no longer contain the old implementation's monolithic full-table rehash or allocator calls inside `obj_mtx`; table-grow timing should instead consist of bounded publication and migration holds.

The campaign does not establish CMS-weighted intermediate-set behaviour, a production latency budget, Fellow behaviour, or release acceptance. Rate and CPU are judged only if the summarizer accepts the row as valid and not under-saturated. Instrumented timing is diagnostic and is not compared directly with uninstrumented throughput.

## Frozen evidence map and budget

All rows use `local-cost-attach-1m`, Default storage, 24 clients, five tags per object, xkey and no-index disabled, and fresh builds. Uninstrumented rows run `cutover-mostly-shared,cutover-mostly-unique` with three repetitions per profile. Instrumented rows run only `cutover-mostly-unique` with one repetition.

| Row | Source | Instrumentation | Purpose |
| --- | --- | --- | --- |
| B1 | `a12a849` | off | Baseline rate/counter sample and first same-code observation |
| P1 | `1680f6f` | off | Patched rate/counter sample |
| B2 | `a12a849` | off | Interleaved baseline and baseline noise observation |
| P2 | `1680f6f` | off | Interleaved patched and patched noise observation |
| B-lock | `a12a849` | on | Old unique-set acquire/growth hold attribution |
| P-lock | `1680f6f` | on | Bounded post-change unique-set hold attribution |

Maximum evidence is six remote runtime rows: 24 uninstrumented workload repetitions and two instrumented repetitions. Each source switch rebuilds; `CACHE_TAG_SKIP_BUILD` is prohibited. Expected wall time is at most two hours after setup. Stop immediately after a setup, build, workload, provenance, checksum, swap, or overall-validity failure. Do not rerun, add concurrency, profile, or increase clients without owner approval.

The remote host must run only one row at a time. Local review or artifact reading may be delegated, but no subagent may start a second remote benchmark session.

## Execution

Create a detached baseline worktree with sibling Vinyl access, then invoke the wrapper from the appropriate source tree. Every row sets `CACHE_TAG_REMOTE_SYNC=1`, so the source-content hash in the fetched artifact is authoritative.

```sh
git worktree add --detach /private/tmp/cachetag-set-interning-before-workspace/libvmod-cachetag a12a849
ln -s /Users/peter/projects/open-source/vinyl-cache/vinyl-cache /private/tmp/cachetag-set-interning-before-workspace/vinyl-cache
```

Use remote directory `cachetag-bench-set-interning-20260811` and fetch under `benchmarks/remote-results/20260811_51.159.202.218/set-interning-post-change/`. The common row settings are `CACHE_TAG_BENCH_SET_INTERNING=1`, `CACHE_TAG_BENCH_CLIENTS=24`, `CACHE_TAG_TAGS_PER_OBJECT=5`, `CACHE_TAG_RUN_NOINDEX=0`, and `CACHE_TAG_RUN_XKEY=0`.

Run B1, P1, B2, P2 in that order with `CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique` and the matrix's three repetitions. Then run B-lock and P-lock with `CACHE_TAG_BENCH_PROFILE=cutover-mostly-unique`, `CACHE_TAG_RUNS_OVERRIDE=1`, and `CACHE_TAG_INSTRUMENT_OBJ_MTX=1`.

Fetch each row into its named directory and retain its archive and SHA-256 file. Summarize all artifacts inside `vinyl-cache-ubuntu-build`; do not compile or run benchmark helpers on the host.

## Interpretation and stop decision

First verify exact source hashes, enabled interning, object/tag counts, zero errors, zero swap, and expected repetition counts. Use B1/B2 and P1/P2 spreads as the observed noise floor; do not claim an uninstrumented rate change smaller than that spread. If under-saturation persists, report rates and CPU as excluded and limit the performance decision to valid counter and lock-attribution evidence.

For B-lock versus P-lock, report acquire and table-grow calls, totals, maxima, and threshold counts. The mechanism succeeds if the patched table-grow observations contain only bounded migration/publication work and no old-style all-table rehash signature. Without an owner-supplied tail budget, even a lower maximum is diagnostic evidence rather than latency acceptance.
