# `cachetag` VMOD Benchmarks

The benchmark harness builds Vinyl Cache and this repository as a standalone
VMOD in the configured Docker image, generates VTC workloads, and records
process metrics plus VMOD/xkey VSC counters. It does not copy files into the
Vinyl source tree.

Set up a local or remote benchmark host with [INSTALL.md](INSTALL.md) before
running the harness.

Generated benchmark VTCs explicitly start Vinyl with `-p debug=none` because
`vinyltest` otherwise enables `debug=+vtc_mode`. That mode is useful for
regression tests but throttles backend fetch throughput enough to dominate
benchmark results ([BR-009](rules/BR-009-debug-none-vtc-mode-throttle.md)).

## Interpretation rules

Rules for designing benchmarks and interpreting their results — each born from
a real misinterpretation incident — live in [`rules/`](rules/INDEX.md), one rule
per file with its tripwire status. Read `rules/INDEX.md` before designing a
campaign or judging results, and open every campaign note/report with a
`Rules reviewed: ...` line citing the applicable BR rules. This README documents
how to run the harness; the rules directory documents how not to be fooled by it.

## Quick Run

From this repository:

```sh
OBJECTS=1000 TAGS_PER_OBJECT=4 RUNS=3 RUN_XKEY=1 PERF_MODE=auto \
  scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

Results are written to `benchmarks/results/<timestamp>/`.

If the local `vinyl-cache-ubuntu-build` image is missing or stale, rebuild it
from the checked-in benchmark Dockerfile:

```sh
scripts/build-benchmark-image.sh
```

Use `DOCKER_BUILD_NO_CACHE=1 scripts/build-benchmark-image.sh` when you want to
force every package layer to rebuild.

The current Docker/OrbStack setup on macOS blocks hardware counters, so runs
record:

- `perf_event_status=unavailable`
- `perf_event_error=1:Operation not permitted`

Wall time and RSS are still captured, but the most reliable local signal is the
VMOD/xkey VSC memory counters.

## Profiles

Select a profile with `BENCH_PROFILE`. `all` generates every implemented
catalogue profile plus one no-index baseline.

- `uniform-tags`: uniform tag distribution over `BENCH_TAG_UNIVERSE`.
- `zipfian-tags`: one hot key plus deterministic skewed tail tags.
- `cms-entity-list`: Drupal/Symfony-style entity, route, list, tenant, and site tags.
- `extreme-high-fanout`: `site`, tenant, and frontpage high-fanout tags.
- `low-fanout-unique`: per-object URL tags plus low-fanout groups.
- `explicit-purge`: long-TTL fanout-shaped load followed by explicit purge.
- `short-ttl-high-churn`: short-TTL reload cycles to exercise expiry/death cleanup.
- `bulk-purge-bursts`: repeated multi-key purge-header requests.
- `concurrent`: overlapping reads, inserts, and purge traffic.
- `purge-storm`: sustained purge stream concurrent with warm read traffic; use `BENCH_PURGE_STORM_*` knobs to vary rate, distinct key vocabulary, unknown-key share, and hard/soft mix.
- `purged-cold-residency`: load, hard-purge a high-fanout key, sample cachetag object residency while read traffic runs, then validate the post-purge freshness window.
- `populated-map-warm`: pre-seed unknown-tag purge state before the timed warm-hit phase, exercising warm reads with a populated purge-history map once the purgemap backend exists.
- `phase4-sweep-latency`: load and warm a long-TTL inventory, run a pre-sweep hit window, trigger one accepted hard purge followed by an explicit certified compaction with `sweep_interval=0s` while read traffic continues, run a post-sweep hit window, validate freshness, and emit pre/sweep/post latency samples plus sweep VSC counters.
- `phase5-held-*`: cachetag-only held-publication lanes for short, multi-wakeup, cap, and cold/discard shutdown coverage; the shutdown shape discards the old VCL while its publication token is still held.
- `phase6-fill-drain`: cachetag-only repeated fill/drain stability lane with full and partial hard drains, threshold churn, soft purge/expiry, TTL expiry, and deliberate storage-pressure/LRU cycles. It requires at least ten cycles and emits per-cycle VSC and vinyld memory snapshots.
- `eviction`: indexed load intended for deliberately undersized storage.

```sh
BENCH_PROFILE=all BENCH_BUCKETS=64 CHURN_CYCLES=3 OBJECTS=10000 RUNS=3 \
  RUN_XKEY=1 scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

## Important Environment Variables

- `OBJECTS`: objects loaded per run.
- `TAGS_PER_OBJECT`: tags attached per object.
- `BENCH_PROFILE`: one of the profiles above, `all`, or a comma-separated profile list.
- `BENCH_SET_INTERNING`: `0` for the direct-vector baseline or `1` for `--enable-set-interning`; default `0`. It applies to the Default/Buddy volatile index representation; Fellow uses its separate on-disk index path. The resolved selection is recorded in build provenance and result metadata, and a `SKIP_BUILD=1` reuse rejects a mismatch.
- `BENCH_BUCKETS`: bucket cardinality for shared bucket tags.
- `BENCH_CLIENTS`: concurrent HTTP clients for the Go driver, default `1`.
- `BENCH_WARM_SECONDS`: timed post-load warm-hit phase duration for long-TTL load profiles, default `5`; set `0` to disable.
- `BENCH_WARM_VALIDATE_HIT`: fail the warm phase if any warm request misses, default `1`.
- `BENCH_RESIDENCY_VALIDATE_OBJECTS`: maximum post-load residency probes, default `0` for full validation.
- `BENCH_HTTP_TIMEOUT`: per-request driver timeout in seconds, default `30`.
- `BENCH_TAG_UNIVERSE`: tag universe for uniform and Zipfian profiles, default `10000`.
- `BENCH_PURGE_REQUESTS`: requests in the bulk purge burst, default `100`.
- `BENCH_PURGE_KEYS_PER_REQUEST`: keys per bulk purge request, default `10`.
- `BENCH_PURGE_VALIDATE_OBJECTS`: object probes after an accepted cachetag purge publication, default `1000`.
- `BENCH_PURGE_SETTLE_MS`: optional delay before post-publication sanity probes, default `1000`.
- `BENCH_PURGE_VALIDATION_DELAY_MS`: legacy alias for `BENCH_PURGE_SETTLE_MS` when `BENCH_PURGE_SETTLE_MS` is unset.
- `BENCH_CONCURRENT_SECONDS`: concurrent profile duration, default `30`.
- `BENCH_CONCURRENT_READERS`: concurrent profile reader goroutines, default `BENCH_CLIENTS`.
- `BENCH_CONCURRENT_WRITERS`: concurrent profile writer goroutines, default derived from `BENCH_CLIENTS` and `BENCH_CONCURRENT_INSERT_EVERY`; set `0` for read-only pressure.
- `BENCH_CONCURRENT_PURGERS`: concurrent profile purger goroutines, default `1`; set `0` to disable purge traffic.
- `BENCH_CONCURRENT_TARGET_RPS`: aggregate read/write offered RPS, default `0` for unbounded.
- `BENCH_CONCURRENT_PURGE_RATE`: concurrent profile purge requests/second, default `5`.
- `BENCH_CONCURRENT_INSERT_EVERY`: legacy insert ratio used only to derive the default writer count, default `5`.
- `BENCH_PURGE_STORM_RATE`: purge-storm purge requests/second, defaulting to `BENCH_CONCURRENT_PURGE_RATE`.
- `BENCH_PURGE_STORM_DISTINCT`: distinct purge key vocabulary for purge-storm, default `100000`.
- `BENCH_PURGE_STORM_UNKNOWN_PERCENT`: percent of purge-storm keys absent from loaded objects, default `100`.
- `BENCH_PURGE_STORM_SOFT_PERCENT`: percent of purge-storm requests sent as soft purges, default `0`.
- `BENCH_POPULATED_MAP_ENTRIES`: unknown-tag purges preseeded before populated-map-warm, default `1000`; large gates should set `100000` or higher.
- `BENCH_RESIDENCY_SWEEP_SECONDS`: cold-residency object-count sampling duration, defaulting to `BENCH_CONCURRENT_SECONDS`.
- `BENCH_RESIDENCY_SAMPLE_MS`: cold-residency object-count sampling interval, default `1000`.
- `BENCH_PHASE4_PRE_SECONDS`: pre-sweep read-hit window for `phase4-sweep-latency`, default `5`.
- `BENCH_PHASE4_SWEEP_SECONDS`: read-hit window overlapping the accepted purge and certified sweep for `phase4-sweep-latency`, default `5`.
- `BENCH_PHASE4_POST_SECONDS`: post-sweep read-hit window for `phase4-sweep-latency`, default `5`.
- `BENCH_PHASE6_PRESSURE_BODY_BYTES`: synthetic body size used by the Phase 6 storage-pressure cycle, default `4096`.
- `BENCH_PHASE6_QUIET_SECONDS`: quiet observation period after each Phase 6 cycle, default `6` and minimum `5`.
- `BENCH_COLD_RESIDENCY_STORAGE`: storage size for purged-cold-residency, defaulting to `BENCH_STORAGE`; remote M0 uses a smaller value than the other M0 profiles to exercise storage pressure.
- `BENCH_COLD_RESIDENCY_BODY_BYTES`: origin response body size for purged-cold-residency, default `0` to reuse `BENCH_BACKEND_BODY_BYTES`.
- `BENCH_VALIDATE_RESIDENCY`: validate post-load residency with hit probes, default `1`.
- `BENCH_EVICTION_STORAGE`: generated storage size for the eviction profile, default `1m`.
- `BENCH_EVICTION_VALIDATE_OBJECTS`: eviction validation probe count, default `1000`.
- `BENCH_SYSTEM_SAMPLE_INTERVAL`: seconds between host utilisation samples in `.time` files, default `1.0`; set `0` to disable. Samples include CPU, memory, process hot spots, and aggregate non-loop block-device IO rates/await/flush/utilization.
- `BENCH_DETAILED_MEMORY_INTERVAL`: seconds between timeout-isolated cache-process `smaps_rollup`, `maps`, and `cmdline` reads, default `1.0`. Freeze this interval for a campaign because a slower interval can reduce observed PSS maxima.
- `BENCH_DETAILED_MEMORY_TIMEOUT`: seconds before a blocked detail helper is killed and reaped, default `0.5`.
- `BENCH_STORAGE`: generated Vinyl storage size, default `256m`.
- `BENCH_STORAGE_KIND`: `default` for the normal benchmark storage path, `fellow` for a patched Slash/Fellow storage lane, or `buddy` for a patched Slash/Buddy storage lane.
- `BENCH_FELLOW_SIZE`: Fellow storage file size for `BENCH_STORAGE_KIND=fellow`, defaulting to `BENCH_STORAGE`.
- `BENCH_FELLOW_SEGMENT_SIZE`: Fellow segment size for `BENCH_STORAGE_KIND=fellow`, default `1MB`.
- `BENCH_FELLOW_BLOCK_SIZE`: Fellow block size for `BENCH_STORAGE_KIND=fellow`, default `64KB`.
- `BENCH_BUDDY_SIZE`: Buddy storage size for `BENCH_STORAGE_KIND=buddy`, defaulting to `BENCH_STORAGE`.
- `BENCH_BUDDY_RESERVE_CHUNKS`: Buddy `reserve_chunks` tune value, default `0`, so the configured Buddy size is the usable benchmark allowance unless a run deliberately reserves memory for LRU latency behavior.
- `BENCH_CACHE_TAG_PERSIST`: `auto`, `0`, or `1`; `auto` enables persistent cachetag metadata for Fellow lanes and disables it for default lanes.
- `BENCH_CACHE_TAG_WAL_FSYNC`: persistent cachetag WAL fsync policy, `strict` or `grouped`, default `strict`.
- `BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS`, `BENCH_CACHE_TAG_SWEEP_BATCH_HOLD`, `BENCH_CACHE_TAG_SWEEP_BATCH_YIELD`: override the cachetag namespace sweep batch count, `obj_mtx` hold budget, and inter-batch handoff duration for bounded-sweep experiments.
- `BENCH_INSTRUMENT_OBJ_MTX`: enable opt-in request-lock timing and set-interning critical-section counters for the matched observability-overhead control; default `0`.
- `BENCH_VINYL_THREADS`: generated Vinyl worker thread cap, default `16`.
- `CHURN_CYCLES`: load/sleep cycles for short-TTL profiles; Phase 6 requires at least `10`.
- `RUNS`: repetitions per workload.
- `RUN_XKEY`: `1`, `0`, or `auto`.
- `RUN_NOINDEX`: `1` to generate the no-index load baseline, or `0` to skip it; Fellow remote matrices set this to `0` because Workstream 1 measures cachetag/Fellow behavior and the no-index Fellow baseline currently trips a Vinyl teardown panic unrelated to cachetag.
- Cachetag generation always emits the one purge-map implementation under stable `cachetag_*` workload names. Archived paired-backend result bundles remain readable through the summarizer’s historical-artifact parser.
- `PERF_MODE`: `auto`, `off`, or `required`.
- `VTC_LOG_BYTES`: internal `vinyltest` log buffer, default `20M`.
- `RESULTS_DIR`: explicit output directory.
- `VINYL_DOCKER_IMAGE`: build/test image.

Use `SKIP_BUILD=1` after the first build when only rerunning generated
benchmarks against an already-built tree. Reuse is provenance-checked: the
build records source-content hashes in `build-provenance.env` (also copied into
each result dir), and a `SKIP_BUILD=1` run fails if the mounted sources no
longer match the cached build ([BR-016](rules/BR-016-build-cache-contamination.md));
set `CACHE_TAG_ALLOW_STALE_BUILD=1` only for a deliberate, labelled stale reuse.
Build caches created before provenance recording fail their first
`SKIP_BUILD=1` run — rebuild once to mint the provenance file.

Treat Docker cgroup `memory.peak` as a benchmark-runtime signal only when the measured container did not also build Vinyl, Slash, or this VMOD; a build-inclusive peak is a lifetime high-water mark dominated by compiler/linker activity. Full rule and comply-by list: [BR-001](rules/BR-001-build-inclusive-memory-peak.md). Never `SKIP_BUILD=1` across an arm or storage-kind change: [BR-016](rules/BR-016-build-cache-contamination.md).

The Go driver is required. It generates deterministic `X-Cache-Tags` values,
reuses HTTP connections, supports bounded load/concurrent workers via
`BENCH_CLIENTS`, validates every response, requires cachetag’s `-1` accepted-publication result, and validates post-publication misses with sampled object probes after `BENCH_PURGE_SETTLE_MS`. It also validates post-load cache residency for long-TTL profiles and emits `.driver` metrics including request count, errors, wall time, throughput, residency probes, latency samples where relevant, and client CPU time. Long-TTL load phases report both cold insertion throughput (`driver_load_requests_per_second`) and timed warm-hit throughput (`driver_warm_requests_per_second`) after residency validation. Bulk-purge keys are de-duplicated across each burst; the driver samples affected objects to prove they miss after the accepted publication. The `concurrent` profile uses explicit reader, writer, and purger goroutine counts plus optional aggregate offered RPS. The phased purge profiles are useful for load cost, warm-hit cost, purge-map memory, publication latency, post-publication sanity, and VMOD counter deltas; detailed correctness belongs in the VMOD test suite. The `short-ttl-high-churn` profile records hit/miss probes instead of requiring all-hit residency, because 1-second TTL objects may legitimately expire during validation. The `eviction` profile uses `BENCH_EVICTION_STORAGE` plus larger synthetic bodies and fails unless cache-miss probes and `n_lru_nuked` prove eviction occurred.

The `phase6-fill-drain` profile is cachetag-only and uses explicit compaction
between cycle operations. Its VTC writes a `vinylstat` snapshot and a `vinyld`
`smaps_rollup` snapshot after every cycle, synchronized through
`BENCH_PHASE_MARKER_DIR`; the driver writes matching cycle kinds, live-object
counts, purge/compact results, and timing metrics to the `.driver` artifact.

The `phase4-sweep-latency` profile writes raw latency sample files beside the `.driver` file as `<workload>.run-<n>.driver_phase4_pre.latency_samples.tsv`, `<workload>.run-<n>.driver_phase4_sweep.latency_samples.tsv`, and `<workload>.run-<n>.driver_phase4_post.latency_samples.tsv`. Use those raw samples, not only precomputed percentiles, when judging an accepted pause/tail budget ([BR-023](rules/BR-023-tail-budget-judgment.md)). If no owner-approved budget is documented, treat Phase 4 rows as evidence collection only.

Every `run_with_metrics.py` wrapped VTC run now writes a raw sampler stream beside its `.time` file as `<workload>.run-<n>.time.samples.jsonl`. Each JSON line is one time-aligned lightweight sample containing host `meminfo`/`vmstat`, cgroup `memory.current`, `memory.peak`, `memory.stat`, `memory.events`, and tracked-process identity/status RSS fields. Process matching uses `comm` plus executable identity; `cmdline`, `maps`, and `smaps_rollup` are read only by a timeout-controlled helper process on the slower detailed-memory interval. The `.time` file records cadence ratio, a boundary-inclusive longest gap, detail attempts/successes/timeouts, helper state, PID/start-time identity, PSS maxima, and explicit sampler/memory validity. Use the JSONL stream for phase-aligned lightweight attribution, the provenanced `cache_process` detailed aggregate for worker PSS gates, and do not infer jemalloc active/retained values from libc RSS ([BR-006](rules/BR-006-allocator-decay-purge-signature.md)).

Run the sampler liveness regression only through Docker:

```sh
scripts/test-benchmark-sampler-liveness.sh
```

The test injects a deterministically blocked detailed-memory read and proves that lightweight cadence and wrapper shutdown continue, the helper times out without leaking, PID/start-time mismatches are rejected, and the summarizer rejects sparse sampling while retaining independent workload/raw-artifact scope fields.

Each `.time` file records per-run kernel memory deltas from `/proc/vmstat` and `/proc/meminfo`, including `vmstat_pswpin_delta`, `vmstat_pswpout_delta`, `vmstat_pgmajfault_delta`, `meminfo_swapfree_kb_delta`, and `swap_activity`. `swap_activity=1` means the kernel swapped pages in or out during that measured VTC run ([BR-011](rules/BR-011-run-validity-flags.md)). It also samples host-level utilisation while the VTC runs: `system_cpu_busy_avg_percent`, `system_cpu_busy_max_percent`, `system_cpu_any_core_busy_max_percent`, `system_cpu_iowait_max_percent`, `system_load1_per_cpu_max`, `system_procs_running_max`, `system_memavailable_min_kb`, `system_memavailable_min_percent`, `system_memavailable_drop_max_kb`, `system_memavailable_drop_max_percent`, `system_swap_used_max_kb`, cgroup memory current/peak fields when Docker exposes them, and aggregate disk deltas. Use these fields to identify runs that were not stressing the server, runs bottlenecked on one hot core, and runs distorted by IO wait or memory pressure. The harness also writes a `SWAP_DETECTED` marker and a `summary.txt` warning when any measured run swaps; treat those timings as suspect for performance claims.

## Large Runs

For 10k and 100k object runs, use a real Linux host when possible:

- hardware counters are much more likely to work with `PERF_MODE=auto` or
  `PERF_MODE=required`;
- wall-clock timing avoids macOS virtualization and OrbStack filesystem noise;
- RSS and allocator behavior are closer to production;
- xkey's large-cache behavior should be easier to see when object-key relation
  counts become large.

Recommended Linux sequence:

```sh
# First run builds Vinyl and xkey.
BENCH_PROFILE=extreme-high-fanout BENCH_BUCKETS=64 OBJECTS=100000 RUNS=3 \
  RUN_XKEY=1 PERF_MODE=required scripts/benchmark-cachetag-vmod.sh ../vinyl-cache

# Reuse the build for the full profile set.
BENCH_PROFILE=all BENCH_BUCKETS=64 CHURN_CYCLES=3 OBJECTS=10000 RUNS=3 \
  RUN_XKEY=1 SKIP_BUILD=1 PERF_MODE=required \
  scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

If `PERF_MODE=required` fails, rerun with `PERF_MODE=auto` and keep the emitted
`perf_event_*` fields in the result note.

`PERF_MODE` records inherited hardware counters in `.time` files; it does not produce call-stack attribution. For VMOD hot-path attribution, use the opt-in `perf record` wrapper on a narrow run:

```sh
BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 RUNS=1 RUN_XKEY=0 \
  BENCH_WORKLOAD_FILTER=cachetag_low_fanout_unique \
  BENCH_PERF_RECORD=on BENCH_PERF_RECORD_WORKLOAD=cachetag_low_fanout_unique \
  BENCH_PERF_RECORD_RUNS=1 BENCH_PERF_RECORD_SCOPE=command \
  PERF_MODE=off SKIP_BUILD=1 scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

The default `BENCH_PERF_RECORD_PHASE=command` records the whole `vinyltest` command, including load, validation, and warm traffic. To profile only resident warm hits, use `BENCH_PERF_RECORD_PHASE=warm`; this waits for the Go driver to enter its warm phase, attaches `perf record` to the live `vinyld` process by default, and stops when the driver leaves the warm phase:

```sh
BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 RUNS=1 RUN_XKEY=0 \
  BENCH_WORKLOAD_FILTER=cachetag_low_fanout_unique \
  BENCH_PERF_RECORD=on BENCH_PERF_RECORD_WORKLOAD=cachetag_low_fanout_unique \
  BENCH_PERF_RECORD_RUNS=1 BENCH_PERF_RECORD_PHASE=warm \
  BENCH_PERF_RECORD_SCOPE=command BENCH_PERF_RECORD_TARGET=vinyld \
  PERF_MODE=off SKIP_BUILD=1 scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

Use `BENCH_PERF_RECORD_TARGET=descendants` to profile the whole `vinyltest` process tree during the warm phase. Use `BENCH_PERF_RECORD_SCOPE=system` only when system-wide attribution is needed; in that mode the target setting is ignored.

The profiling wrapper writes `*.perf.data`, `*.perf-report.txt`, `*.perf-report-children.txt`, `*.perf-buildids.txt`, and a bounded `*.perf-script.txt` beside the normal run artifacts. It also copies the cachetag VMOD, installed Vinyl binaries, installed VMODs, and xkey VMOD when present into `symbols/` with `symbols/manifest.txt`, so downloaded artifacts retain the symbol material needed to diagnose unresolved `perf` frames.

## Remote Matrices

Use `scripts/remote-benchmark.sh` for rented Debian/Ubuntu servers. The SSH target is the normal `user@host` argument or an entry from `~/.ssh/config`. The default remote workspace is `cachetag-bench` under the SSH user's home. Root SSH works, and cloud-image users such as `ubuntu` work when they have passwordless sudo; the remote runner defaults `REMOTE_DOCKER=auto`, which tries plain `docker` first and then `sudo -n docker`. It caps remote driver clients at 8 by default, sets `BENCH_VINYL_THREADS` to the logical CPU count clamped to at least the selected client count, and passes `--cap-add PERFMON` to Docker for hardware perf counters.

By default, downloaded remote artifacts are stored under `benchmarks/remote-results/YYYYMMDD_host`, for example `benchmarks/remote-results/20260622_51.159.110.61`. Override this with `CACHE_TAG_FETCH_DIR` or the explicit `LOCAL_DIR` argument when a run needs a different layout.

For before/after benchmark campaigns, run rows in an interleaved order such as `baseline,patched,baseline,patched` or reset host state between rows ([BR-002](rules/BR-002-interleave-campaign-rows.md)); sequential arms manufacture position-correlated regressions in whichever arm runs last. Comparisons are only valid within one hardware cohort and host session ([BR-014](rules/BR-014-hardware-cohorts.md)).

When judging acceptance from a campaign, read retained-index-floor and low-water convergence claims (`resize_active_bytes`, `index_memory_bytes`, `resize_low_water_rearms`) from `summarize_results.py`'s `Phase 4 resize VSC` and `resize events` lines, which use the final `_post.run-N.stats` teardown snapshot — never from `phase4_post_*` keys, which can report pre-convergence sizes ([BR-003](rules/BR-003-final-post-snapshot-for-convergence.md)).

```sh
scripts/remote-benchmark.sh setup ubuntu@host
scripts/remote-benchmark.sh run ubuntu@host smoke
scripts/remote-benchmark.sh run ubuntu@host full ./benchmarks/remote-results/host-a/full
```

For one exact replacement after a proven measurement-capture failure, set `CACHE_TAG_RUNS_OVERRIDE=1` on the same named matrix. Keep the rejected artifact, preserve the frozen code/harness/host/power and BR-002 ordering constraints, and map the replacement in the campaign report. `remote-run.env` records `runs_override=1`, while `metadata.env` records the effective `runs=1`. Do not use this mechanism to erase a workload crash, nonzero exit, wrong work volume, or hit/error failure ([BR-018](rules/BR-018-measurement-voids-fail-loud.md)).

Named matrices are tiered. The **regression set** is the gate: run it (as the
`regression` group, or lane by lane) before and after any performance-relevant
change, against recorded baselines and the rules in [`rules/INDEX.md`](rules/INDEX.md).

- `regression`: runs, in order, `sanity-smoke`, `local-cost-attach-1m`, `local-cost-warm-1m`, `local-cost-pressure-1m`, `churn-deterministic-incremental-100k`, `phase4-sweep-default-1m`, `phase6-fill-drain-default-1m`, and `phase6-fill-drain-buddy-1m`. Each lane covers a distinct regression surface: correctness smoke, attach cost, warm-hit cost, purge pressure, deterministic churn attribution, sweep/compact pause latency, and fill/drain stability plus memory convergence on both storage arms.
- `sanity-smoke`: 1k objects, all profiles, one run, full validation.
- `local-cost-attach-1m`, `local-cost-warm-1m`, `local-cost-pressure-1m`: 1M attach-only, warm-hit, and read-purge pressure lanes with xkey/no-index controls where useful.
- `churn-deterministic-incremental-100k`: deterministic rotating churn incremental lane (the attribution lane; see BR-019).
- `phase4-sweep-default-1m`: sweep/compact pause-latency lane with attributable-latency artifacts, judged against the ratified 15ms VMOD-attributed budget (BR-023).
- `phase6-fill-drain-default-1m`, `phase6-fill-drain-buddy-1m`: ten-cycle fill/drain stability lanes; the summarizer prints their per-cycle table and BR-005/BR-006 warnings.

Scale and pre-release lanes, run when a release or a scale-sensitive change warrants them:

- `sanity-10k`: 10k objects, all profiles, three runs, full benchmark sanity validation.
- `pressure-100k`, `pressure-1m`, `pressure-5m`: pressure subset with sampled validation and hardware-derived reader/writer/purger counts.
- `local-cost-100k`, `local-cost-1m`: attach/warm/pressure child-matrix groups; 100k leaf lanes are also individually addressable.
- `local-cost-pressure-paired-100k`, `local-cost-pressure-paired-1m`: pressure lanes with no-index, xkey, and cachetag arms.
- `lowfanout-10m`: 10M `low-fanout-unique` with an xkey comparison on Default for high-cardinality memory slope. Buddy runs cachetag only with 32 GiB storage; Fellow runs persistent cachetag only with the proven 128 GiB/4 KiB full-residency envelope.
- `purgemap-fanout-attach-10m`: 10M high-fanout attach-only gate.
- `fanout-100k` through `fanout-30m`: high-fanout scale ladder with xkey comparison.
- `eviction-100k`: eviction-specific lane scaled around storage pressure; cost measurement only (BR-020).
- `phase4-sweep-buddy-1m`: Buddy-storage sweep-latency lane.
- `phase6-fill-drain-noindex-default-1m`: plain-Vinyl generation-ban ownership control for allocator-tail attribution (the BR-006 falsification lane).
- `churn-deterministic-full-100k`, `churn-deterministic-incremental-1m`, `churn-deterministic-incremental-5m`: deterministic churn variants.
- `buddy-smoke`, `buddy-local-cost-100k` (group) and its attach/warm/pressure/pressure-paired 100k leaves: Buddy-arm lanes.
- `backend-local-cost-100k`: Default, Buddy, and Fellow 100k local-cost lanes on one host.
- `full`: maintenance group; runs `regression` plus `pressure-100k`, `pressure-1m`, `pressure-5m`, `eviction-100k`, and `fellow-restart-idle-memory`.

Fellow backlog lanes, run when Fellow persistence work resumes: `fellow-smoke`, `fellow-local-cost-100k`, `fellow-local-cost-1m`, `fellow-shutdown-5m`, `fellow-shutdown-10m`, the `fellow-storage/volatile/persistent-attach` lanes at 100k/1m/5m, the `fellow-fanout-*-attach-100k` lanes, the `fellow-memory-paired-*` groups, and the `fellow-restart-*` lanes (idle-memory, idle-memory-1m, first-touch, cold-purge, hot-purge).

Retired campaign matrices — the Proposal 8 M0/M3 gates, the `purgemap-cutover-*` groups, the Phase 4 sweep scale ladder (100k/5m/10m), the Phase 5 held-publication lanes, `selected-10m/20m/30m`, `eviction-1m`, `local-cost-resize-pressure-260k`, and the `purgemap-*` duplicates of the plain lanes — were removed from the matrix after their decisions closed (Phase 10, 2026-07-15). Their archived artifacts record the harness commit that produced them; reproduce by checking out that commit. Lane lifecycle policy: a lane added for a campaign is promoted into the regression set or removed when its campaign closes (see `rules/INDEX.md`).

Remote matrix defaults can be tuned with environment variables:

- `CACHE_TAG_BENCH_CLIENTS`: remote load/validation client count; empty means hardware-derived and capped at 8.
- `CACHE_TAG_VINYL_THREADS`: Vinyl worker thread cap; empty means logical CPUs, clamped to at least `CACHE_TAG_BENCH_CLIENTS`.
- `CACHE_TAG_PRESSURE_READERS`: pressure matrix reader goroutines; empty means auto.
- `CACHE_TAG_PRESSURE_WRITERS`: pressure matrix writer goroutines; empty means auto.
- `CACHE_TAG_PRESSURE_PURGERS`: pressure matrix purger goroutines; empty means auto.
- `CACHE_TAG_PRESSURE_TARGET_RPS`: pressure matrix aggregate read/write offered RPS; empty means auto and `0` means unbounded.
- `CACHE_TAG_PRESSURE_PURGE_RATE`: pressure matrix purge requests/second; empty means auto.
- `CACHE_TAG_BENCH_PERF_RECORD`: pass `BENCH_PERF_RECORD` through to the Docker benchmark wrapper for opt-in `perf record` profiling.
- `CACHE_TAG_BENCH_PERF_RECORD_SCOPE`: `command` or `system`; `system` adds `perf record -a`.
- `CACHE_TAG_BENCH_PERF_RECORD_PHASE`: `command` for the whole `vinyltest` command or `warm` for driver warm-phase-only capture.
- `CACHE_TAG_BENCH_PERF_RECORD_TARGET`: `vinyld` or `descendants` for phase profiles.
- `CACHE_TAG_BENCH_PERF_RECORD_RUNS`: number of runs per workload to record, or `all`.
- `CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD`: optional workload basename to profile while other generated workloads run normally.
- `CACHE_TAG_BENCH_VALIDATE_RESIDENCY`: override `BENCH_VALIDATE_RESIDENCY`.
- `CACHE_TAG_BENCH_WARM_SECONDS`: override `BENCH_WARM_SECONDS`.
- `CACHE_TAG_BENCH_SKIP_PURGE`: override `BENCH_SKIP_PURGE` for load-only probes.
- `CACHE_TAG_BENCH_RESTART_TAG_PROFILE`: override restart/demand-load tag profile, for example `low-fanout-unique` or `extreme-high-fanout`.
- `CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT`: override restart first-touch percentage, from `1` to `100`.
- `CACHE_TAG_BENCH_STORAGE_KIND`: override `BENCH_STORAGE_KIND` for the selected matrix.
- `CACHE_TAG_BUDDY_SIZE`: override `BENCH_BUDDY_SIZE` for the selected matrix.
- `CACHE_TAG_BUDDY_RESERVE_CHUNKS`: override `BENCH_BUDDY_RESERVE_CHUNKS` for the selected matrix.
- `CACHE_TAG_FELLOW_SIZE`: override `BENCH_FELLOW_SIZE` for the selected matrix.
- `CACHE_TAG_FELLOW_SEGMENT_SIZE`: override `BENCH_FELLOW_SEGMENT_SIZE`.
- `CACHE_TAG_FELLOW_BLOCK_SIZE`: override `BENCH_FELLOW_BLOCK_SIZE`.
- `CACHE_TAG_CACHE_TAG_PERSIST`: override `BENCH_CACHE_TAG_PERSIST`.
- `CACHE_TAG_WAL_FSYNC`: override `BENCH_CACHE_TAG_WAL_FSYNC`.
- `CACHE_TAG_SWEEP_BATCH_OBJECTS`, `CACHE_TAG_SWEEP_BATCH_HOLD`, `CACHE_TAG_SWEEP_BATCH_YIELD`: override the matching `BENCH_CACHE_TAG_*` bounded-sweep knobs.
- `CACHE_TAG_BENCH_PERF_FREQ`: `perf record` frequency.

Slash-backed storage benchmarks are deliberately isolated from the default in-memory matrices. Use `BENCH_STORAGE_KIND=fellow` or the dedicated `fellow-*` remote matrices when the question is Fellow-backed persistent storage behavior; these lanes build patched Slash/Fellow inside the benchmark container and run cachetag with a persistent namespace by default. Use `BENCH_STORAGE_KIND=buddy` or the dedicated `buddy-*` remote matrices when the question is Buddy-backed volatile storage behavior; Buddy lanes build patched Slash/Buddy inside the benchmark container and keep cachetag persistence disabled by default.

`run` fetches the completed matrix result tarball and checksum automatically. For `full`, each child matrix is fetched into a child-named local directory immediately after it completes, including failed runs once a result directory has been created. The remote wrapper labels benchmark containers with matrix, result id, and branch. If an old labelled container exists, the wrapper refuses to start by default; set `CACHE_TAG_REMOTE_CLEAN_STALE=1` to remove stale labelled benchmark containers before starting the next run. Downloaded artifacts are uniquely named from the remote result id.

Summarize downloaded tarballs or extracted result directories with:

```sh
benchmarks/summarize_results.py benchmarks/remote-results/host-a/full
benchmarks/summarize_results.py benchmarks/remote-results/host-a/full/*/*.tgz
benchmarks/summarize_results.py benchmarks/remote-results/20260622_51.159.110.61/local-cost-*/*.tgz
```

The summarizer reports pass/fail counts, driver errors, wall-time distribution, CPU saturation, busiest single core, memory headroom, cgroup peak memory, swap activity, disk IO deltas, an inferred limiting factor, and hardware fingerprint groups. Treat summaries that warn about low CPU and memory pressure as correctness or harness-overhead results, not throughput limits.

The summarizer’s `cgroup_peak_bytes` field has the same lifetime-high-water caveat as the raw `.time` metric ([BR-001](rules/BR-001-build-inclusive-memory-peak.md)). For Phase 6 rows the summarizer also prints a per-cycle p99/max/RSS table and emits `[BR-005]`/`[BR-006]` interpretation warnings for non-production allocator configs and allocator decay-purge tail signatures.

## Origin Epochs

Ordinary object requests carry `X-Bench-Origin-Epoch`. The driver starts at epoch 1 and advances it when an accepted purge publication begins a freshness-validation transition. Accepted purges outside those transitions do not advance it merely because they succeeded. Phase 4 request snapshots hold a shared epoch lease for the request lifetime, so the transition waits for already-started old-epoch requests without relabelling them. The stateless backend echoes that value as the cacheable `X-Origin-Generation` header. Freshness validation requires equality with the epoch captured when the request started; older and newer mismatches are reported separately. The epoch is not included in the URL, cache key, or generated `vcl_hash`, and generated cachetag, xkey, and no-index VCL do not rewrite the response header in `vcl_deliver`. A missing request epoch falls back to 1; malformed, zero, or negative values receive HTTP 400.

## Phase 4 attributable latency

`phase4-sweep-latency` writes a versioned `*.phase4_requests.tsv` artifact and a matching `*.phase4_boundaries.tsv` artifact. Every request row contains a unique sequence, object ID, phase hint, scheduled/start/end monotonic offsets in integer nanoseconds, duration, scheduling lag, skipped pacing slots, cache state, requested/returned origin epochs, error classification, and epoch-boundary classification. The boundary artifact uses the same monotonic origin for the reader window, initial purge request/response, accepted epoch transition, seal purge, compact request/response, stable pre/post windows, and configured guard. `BENCH_PHASE4_ATTRIBUTION_GUARD_MS` defaults to `10`.

The summarizer validates schema, unique IDs, timestamp arithmetic, epoch-boundary classification, and zero dropped samples before emitting these distributions:

- `phase4_pre`: the stable pre-purge window.
- `phase4_compact_overlap`: requests overlapping the driver-observed synchronous compact call with zero guard.
- `phase4_compact_guarded`: requests overlapping compact plus the explicit guard on both sides.
- `phase4_refill`: requests which start after the guarded interval and before the measurement window ends.
- `phase4_post`: the stable post window.

Each distribution reports p50/p95/p99, p99.9 and p99.99 when enough samples exist, raw maximum, threshold counts, interval-local offered/achieved rates and scheduled/started/completed counts, skipped/late pacing evidence, cache state, errors, stale responses, and epoch mismatches. The Phase 4 pacer skips slots missed during a pause and records them; it does not issue a catch-up burst. Historical duration-only artifacts remain readable, but attributable fields are never synthesized for them.

After the accepted epoch transition has drained pre-transition request leases, both Phase 4 arms publish the same key once more without advancing the epoch. This seal publication covers an old-epoch fetch which began before the transition but attached after the initial purge; retaining the original request epoch remains useful evidence, while the seal prevents that benchmark-boundary artifact from surviving as a current-epoch hit. Seal timing and return value are explicit artifacts, not part of the compact interval.

`phase4-refill-control` runs the same load, warmup, readers, target RPS, initial and seal purge publications, epoch transition, and refill duration with `sweep_interval=0s` and no `.compact()` call. Its boundary artifact explicitly records `compact_present=0`; it deliberately leaves certified reclamation incomplete and is not a reclamation result.

Set `BENCH_INSTRUMENT_OBJ_MTX=1` for diagnostic rows. This enables monotonic acquisition-wait timing for request probe, attach, and invalidation categories, plus set-interning acquisition, table-grow, and set-allocation timings captured while `obj_mtx` is held. The set-interning timers write to mutex-protected index state directly, rather than taking `counter_mtx` per attach. Resize, rehash, zero-container free, and whole `cachetag_record_shrink()` timings are always counted. Generated Phase 4 workloads capture cumulative VSC snapshots at pre-start, pre-end, compact/control boundary, refill end, and post end so the summarizer can produce phase deltas. Use matched uninstrumented rows for final latency unless the 100k overhead comparison shows the instrumentation cost is negligible.

Phase 4 container-resize telemetry separates compact scheduling cost from deferred-resize work: `record_shrink_*` measures compact scheduling call lock cost, while physical object segment publication/detach, side-table publication/migration, low-water observation, cancellation, rollback, and resize-batch `obj_mtx` timing are reported by the `object_segment_*`, `side_*`, `resize_low_water_*`, and `resize_batch_*` fields. Capacity/state fields such as buckets, bytes, cursor, remaining work, state, reason, target capacity, and elapsed observation time are phase snapshots, not cumulative deltas. The byte reconciliation fields use `resize_active_bytes + resize_retiring_bytes + resize_detached_bytes == resize_reconciled_bytes`; `index_memory_bytes` includes the same active, retiring, and still-detached container bytes plus non-container VMOD state such as fold vectors and purge-map storage.

## Phase 5 held publication

`phase5-held-short`, `phase5-held-multi`, and `phase5-held-cap` are cachetag-only held-publication profiles. The generated VTC declares a pair of `vtc` barriers, the driver starts a special `/__bench_phase5_hold` fetch, and generated VCL calls `tags.add("phase5:held")` before waiting on the hold barrier. Once the VTC observes the hold, the driver publishes and compacts a purge, then runs hit and purge traffic while the old publication token remains held. `phase5-nohold-short`, `phase5-nohold-multi`, and `phase5-nohold-cap` are matched control profiles: they use the same load duration, purge cadence, cap settings, snapshots, and final validation, but skip the held backend fetch and barriers.

The VTC captures VSC snapshots at hold-fetch start, hold active, held-load start/end, pre-release, release, and final post. The short lane defaults to a 500ms window, the multi-wakeup lane to 3s, and the cap lane to 6s with `purge_history_max_entries=32` and distinct cap-pressure purge keys. The summarizer reports whether a publication was held, read/purge counts, p99/p99.9 latency, publication acquire/release counters, reader gauges, deferral timing, purge-map entries/bytes, cap prune counters, sweep counters, and the phase snapshots.
