# Set-interning saturation and attach-cost screen

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (only `SKIP_BUILD=1` calibration rows may use runtime cgroup peak, although no peak claim is planned), BR-002 (interleave comparison arms), BR-003 (final post snapshot for retained counters), BR-004 (generated workload flushes VSC before snapshots), BR-008 (one sequential remote session; the owner explicitly requested delegated execution), BR-009 (generated VTCs start Vinyl with `debug=none`), BR-011 (quote rate and CPU only from valid, non-under-saturated comparison rows), BR-013 (whole-tree CPU is not VMOD attribution), BR-014 (one existing host cohort), BR-016 (fresh build on every configuration switch), BR-017 (one harness commit), BR-018 (no automatic retry of a failed workload), BR-019 (deterministic work volume), BR-020 (the named 8 GiB Default-storage envelope and attach-only residency scope), BR-023 (no latency-tail claim), BR-024 (interleaved B/P pairs with three repetitions), BR-025 (record governor and boost state), and BR-026 (frozen staged budget).

**Campaign class:** Development screen.

**Status:** The c24 and c48 no-index calibrations completed as planned. Both were valid but under-saturated; c24 achieved the higher cold-attach rate, so no c48 comparison was started. The current source adds `obj_mtx` critical-section diagnostics, which changes harness identity under BR-017. The remaining comparison is superseded by `20260810_1815_plan_set-interning-obj-mtx-attribution-screen.md`.

**Host and harness:** Reuse the already prepared `ubuntu@51.159.202.218` cohort and `cachetag-bench-set-interning-20260810` remote directory. The deployed source remains commit `a12a849 Add set-interning benchmark arms`; no source or harness change is permitted during this screen.

## Claim

Determine whether sorting, hashing, copying, and retaining interned complete membership sets changes achieved attach rate or process/tree CPU at a driver setting that is materially busier than the prior eight-client screen. Compare the one-shared-set and all-unique-set endpoints only. This does not determine CMS-weighted intermediate-set behavior, release acceptance, tail latency, or a universal server-capacity figure.

## Saturation calibration

The historical AMD EPYC 4345P calibration saw a bounded achieved-rate peak at 24 clients but first reached a clean CPU-limited warm point at 48; 64 was unstable. The host fingerprint matches that 16-logical-CPU class, so run one no-index `local-cost-attach-1m` calibration at 24 and 48 clients. It uses the cached enabled build (`SKIP_BUILD=1`), the `noindex_load` workload only, one repetition, and a deterministic 1m/t5 URL shape. No arm comparison is made in this stage.

If c48 is process-pass, zero-error, zero-swap, and no worse than a harness failure, use c48 for the attach comparison even if the summarizer retains the conservative `harness/under-saturated` label. If c48 fails or is invalid, stop and report; do not try c64 or add a replacement without owner approval.

## Attach comparison

At c48, run `local-cost-attach-1m` with `cutover-mostly-shared,cutover-mostly-unique`, five tags per object, xkey disabled, and the wrapper's no-index control retained. Every comparison invocation has three repetitions. Execute B1 (interning disabled), P1 (enabled), B2 (disabled), P2 (enabled), rebuilding on every configuration switch. This maps the shared endpoint to the potential memory/locality benefit and the unique endpoint to the sort/hash/table overhead.

The maximum budget is two cached calibration rows plus four fresh-build comparison rows, for 24 cachetag workload repetitions and 14 no-index control repetitions. Expected elapsed time is no more than two hours. Stop after the second calibration if it fails, or after any comparison row fails; fetch the diagnostic artifact but do not retry, profile, add warm-hit rows, or raise concurrency beyond c48 without owner approval.

No VSC or rate conclusion is valid unless the fetched archive records the expected set-interning build provenance, exact object/tag shape, zero errors, and no swap. Rate and CPU discussion must retain the summarizer classification. Whole-tree CPU and inherited hardware counters are directional only under BR-013; a later narrow `perf record` pair would be needed to attribute a material delta to VMOD symbols.

## Commands

Calibration, in this order:

```sh
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/c24-calibration CACHE_TAG_SKIP_BUILD=1 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_CLIENTS=24 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_BENCH_WORKLOAD_FILTER=noindex_load scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/c48-calibration CACHE_TAG_SKIP_BUILD=1 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_RUNS_OVERRIDE=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 CACHE_TAG_BENCH_WORKLOAD_FILTER=noindex_load scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
```

If and only if c48 completes, execute this interleaved order:

```sh
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/B1 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/P1 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/B2 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-saturation/P2 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_CLIENTS=48 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-1m
```
