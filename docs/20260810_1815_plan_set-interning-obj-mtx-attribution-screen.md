# Set-interning `obj_mtx` attribution screen

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (no build-inclusive peak claim), BR-002 (paired baseline/enabled rows), BR-003 (final post snapshot for retained counters), BR-004 (generated workload flushes VSC before snapshots), BR-008 (one sequential remote session; the owner explicitly requested delegated execution), BR-009 (generated VTCs start Vinyl with `debug=none`), BR-011 (under-saturated rows are not throughput or CPU evidence), BR-013 (whole-tree CPU is not VMOD attribution), BR-014 (one existing host cohort), BR-016 (fresh build on every configuration switch), BR-017 (the timing instrumentation creates a new harness identity), BR-018 (no automatic retry of a failed workload), BR-019 (deterministic work volume), BR-020 (the named 8 GiB Default-storage envelope and attach-only residency scope), BR-023 (no latency-tail claim), BR-024 (same-code diagnostic only), BR-025 (record governor and boost state), and BR-026 (frozen staged budget).

**Campaign class:** Instrumented development screen.

**Status:** Completed exactly B24, P24, B48, P48. Every row exited successfully and its fetched archive passed a manual SHA-256 comparison. The evidence and decision are recorded in `20260810_1845_report_set-interning-obj-mtx-attribution-screen.md`.

**Host and harness:** Reuse `ubuntu@51.159.202.218` and `cachetag-bench-set-interning-20260810`. The first row synchronizes the current working tree; its source-content hash, not the earlier `a12a849` remote screen, identifies this campaign. The local Docker harness has already passed both configure arms with the instrumentation enabled.

## Claim

Determine whether the part of set interning executed while `obj_mtx` is held is long enough, or contended enough, that it needs a design change before production use. This screen does not establish a production throughput improvement or regression: the timers are opt-in diagnostic overhead, and neither current cold-attach calibration point saturates the server.

The enabled arm records cumulative VSC counters for the complete intern acquisition, the table allocation/rehash, and membership-node allocation. It stores those counters in `obj_mtx`-protected index state, so it does not take `counter_mtx` while an attach holds `obj_mtx`. The existing request-attach wait counters remain the contention signal. Sorting and hashing happen before `obj_mtx`, so the acquisition timer intentionally excludes them.

## Prior concurrency calibration

The earlier, pre-instrumentation cached enabled build ran the one-million-object no-index control at c24 and c48. Both were valid but under-saturated; c24 achieved 89,565.59 load RPS with 66.39% average maximum CPU (83.79% run maximum), while c48 achieved 76,891.16 RPS with 63.87% average maximum CPU (84.19% run maximum). This selects c24 as the higher achieved-rate cold-attach setting and c48 as a higher-concurrency diagnostic setting only. It is not a comparison with this source identity and does not support a capacity or VMOD-CPU claim.

## Frozen evidence map and budget

Every row runs `local-cost-attach-1m` with one deterministic repetition of `cutover-mostly-shared,cutover-mostly-unique`, five tags/object, default storage, xkey disabled, and no no-index control. `CACHE_TAG_INSTRUMENT_OBJ_MTX=1` is fixed across all rows. Each arm switch performs a fresh build; `SKIP_BUILD=1` is prohibited.

| Row | Clients | Set interning | Purpose |
| --- | ---: | ---: | --- |
| B24 | 24 | `0` | Direct-vector attach-wait baseline at the best achieved-rate setting |
| P24 | 24 | `1` | Intern acquisition, allocation/rehash, and attach-wait diagnostics |
| B48 | 48 | `0` | Direct-vector attach-wait baseline at higher concurrency |
| P48 | 48 | `1` | Higher-concurrency interning and attach-wait diagnostics |

The maximum budget is four fresh-build rows and eight cachetag workload runs. Stop immediately after any setup, build, workload, or artifact-integrity failure; fetch the diagnostic artifact but do not retry, add c64, start `perf record`, or add uninstrumented rate rows without owner approval.

Interpret the enabled rows as follows:

- Small acquisition maxima with no threshold crossings, and no material B/P attach-wait increase, means allocator/table work under `obj_mtx` is not currently the primary design risk for these endpoint shapes.
- Acquisition or table-grow outliers that coincide with higher attach waits, especially at c48, identify the critical section as a redesign target. A first remediation would move table and node allocation out of `obj_mtx` with a publish/retry protocol.
- Whole-tree CPU, inherited hardware counters, and load RPS remain context only under BR-011 and BR-013. A later uninstrumented achieved-rate pair and targeted `perf record` would be required for performance acceptance or VMOD CPU attribution.

## Commands

Run in this exact order. The first row synchronizes the newly instrumented source; subsequent rows run against the frozen remote checkout. Do not set `CACHE_TAG_REMOTE_CLEAN_STALE=1`.

```sh
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=1 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/B24 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_INSTRUMENT_OBJ_MTX=1 CACHE_TAG_BENCH_CLIENTS=24 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_RUN_NOINDEX=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/P24 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_INSTRUMENT_OBJ_MTX=1 CACHE_TAG_BENCH_CLIENTS=24 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_RUN_NOINDEX=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/B48 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_INSTRUMENT_OBJ_MTX=1 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_RUN_NOINDEX=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/P48 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_INSTRUMENT_OBJ_MTX=1 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_RUN_NOINDEX=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
```
