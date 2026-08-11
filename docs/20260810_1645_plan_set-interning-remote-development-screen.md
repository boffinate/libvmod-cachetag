# Set-interning remote development screen

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (runtime memory comes only from cached-build rows), BR-002 (interleaved arms), BR-003 (final post snapshot for retained-state readings), BR-004 (VSC flush semantics), BR-008 (one benchmark session on the host; the owner specifically requested delegated execution), BR-009 (generated VTCs set `debug=none`), BR-011 (invalid rows are correctness/counter evidence only), BR-014 (one host and uninterrupted cohort), BR-016 (rebuild on each configuration switch), BR-017 (one harness commit), BR-018 (fail loud; no automatic behavioral reruns), BR-019 (deterministic object and tag shape), BR-020 (storage envelope), BR-023 (no tail acceptance claim), BR-024 (same-code noise observation), BR-025 (record host power metadata), and BR-026 (frozen scope and budget).

**Campaign class:** Development screen.

**Host:** `ubuntu@51.159.202.218`.

**Harness commit:** `a12a849 Add set-interning benchmark arms` on `feature/set-interning`.

## Claims and exclusions

This screen establishes that the remote Docker harness can build and run both configured membership representations, that the enabled build reports the expected intern-table geometry for a shared exact membership set while the disabled build reports zeroes, and that the two existing exact-set extremes provide a first direction-of-travel comparison on one hardware cohort.

It does not establish release acceptance, a production CPU/latency benefit, an optimal table geometry, Fellow behavior, or a result for CMS-weighted intermediate exact-set reuse. The existing `cutover-mostly-shared` and `cutover-mostly-unique` profiles are intentionally the two endpoints; a later decision round needs stable-set-regeneration and listing-set-mutation profiles.

## Frozen evidence map and budget

Each runtime row is `local-cost-attach-100k` overridden to run only `cutover-mostly-shared,cutover-mostly-unique`, with five tags per object, xkey disabled, and the matrix's three repetitions. The wrapper's default no-index control remains present, but does not support an interning claim. Default storage, `PERF_MODE=required`, and the matrix's 2 GiB storage envelope are retained.

| Row | Set interning | Purpose |
| --- | --- | --- |
| B1 | `0` | Direct-vector baseline and first same-code sample |
| P1 | `1` | Enabled representation |
| B2 | `0` | Interleaved direct-vector sample |
| P2 | `1` | Interleaved enabled sample |

The maximum is four runtime rows (24 cachetag workload repetitions total) plus one deployment/setup operation. Every arm switch is a fresh build; `SKIP_BUILD=1` is prohibited for this screen. Expected wall time is at most two hours, including four Docker builds. Stop after all four artifacts are fetched, or immediately after any setup/build/workload failure. A failed row is diagnostic only; do not rerun or add a lane without owner approval.

The intended execution order is B1, P1, B2, P2. This is the BR-002 interleave. The two baseline rows provide an A/A observation; all results remain a development screen because they cover only the endpoint tag shapes.

## Commands

First deploy and prepare the host with the documented wrapper:

```sh
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 scripts/remote-benchmark.sh setup ubuntu@51.159.202.218
```

Run the rows in the table order. Each command uses `CACHE_TAG_REMOTE_SYNC=0` because setup deployed the frozen harness; it uses a fresh build rather than a cached one and fetches its own artifact directory.

```sh
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/B1 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-100k
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/P1 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-100k
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/B2 CACHE_TAG_BENCH_SET_INTERNING=0 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-100k
CACHE_TAG_REMOTE_DIR=cachetag-bench-set-interning-20260810 CACHE_TAG_REMOTE_SYNC=0 CACHE_TAG_FETCH_DIR=benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/P2 CACHE_TAG_BENCH_SET_INTERNING=1 CACHE_TAG_BENCH_PROFILE=cutover-mostly-shared,cutover-mostly-unique CACHE_TAG_TAGS_PER_OBJECT=5 CACHE_TAG_RUN_XKEY=0 scripts/remote-benchmark.sh run ubuntu@51.159.202.218 local-cost-attach-100k
```

Do not set `CACHE_TAG_REMOTE_CLEAN_STALE=1`: a stale labelled container must fail loud rather than be removed automatically. Record setup/runtime failures and actual elapsed time before any later decision.
