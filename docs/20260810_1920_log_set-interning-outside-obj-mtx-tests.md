# Set-interning outside-`obj_mtx` regression-test log

Rules reviewed: BR-003, BR-004, BR-018, BR-019, and BR-026.

## Change

Registered three set-interning/test-hook VTCs in `src/Makefile.am`:

- `cachetag_pm00031.vtc` covers a deliberately small table, bounded migration, lookups while old and active tables coexist, purge during migration, and worker convergence.
- `cachetag_pm00032.vtc` covers initial bucket-array allocation failure versus later growth allocation failure and verifies that the latter preserves attach availability.
- `cachetag_pm00033.vtc` uses two synchronized backend responses carrying the same canonical multi-fold set and checks one publication, one hit, two references, and outside-lock losing-candidate cleanup.

All compilation and VTC verification was performed through `scripts/test-with-vinyl-cache.sh ../vinyl-cache`; no host build was used.

## Planned-interface assumptions

The tests retain `test_fail_next_intern_alloc()` for the existing miss-only candidate-failure contract. They additionally use `test_intern_initial_buckets(INT)`, `test_fail_next_intern_table_alloc()`, `test_intern_worker_hold(BOOL)`, `test_intern_migrate_buckets(INT)`, `test_intern_active_buckets()`, `test_intern_old_buckets()`, and the existing `test_resize_worker_drain(INT)`. The migration test needs the hold and bounded-step seams to make old-table lookup and cleanup assertions deterministic; without them, worker scheduling can complete migration before the VTC observes the transition. Migration-active state is checked through the `volatile_interned_migration_active` VSC gauge.

The tests use debug gauges named `volatile_interned_old_table_bytes`, `volatile_interned_detached_table_bytes`, `volatile_interned_table_alloc_failures`, `volatile_interned_table_grow_failures`, and `volatile_interned_candidate_discards`. These expose old-table storage, outside-lock cleanup, and retained availability after a grow allocation failure without weakening the coverage.

## Review corrections

An implementation review added the planned `test_intern_migration_active()` hook, aligned the growth-failure VTC expectation with the implemented `volatile_interned_table_grow_failures` VSC name, and capped manual test-hook migration to the ordinary request-side budget so a test VCL call cannot take an unbounded `obj_mtx` hold.

The review also found that a detached intern table could be stranded if a later attach retry exited through an object or side-allocation failure. Every post-initialisation attach exit now consumes the prepared candidate and drains the caller-local cleanup handoff after releasing `obj_mtx`.

## Docker verification

- `CACHE_TAG_CHECK_TARGET=check CACHE_TAG_TESTS='vtc/cachetag_pm00030.vtc vtc/cachetag_pm00031.vtc vtc/cachetag_pm00032.vtc vtc/cachetag_pm00033.vtc' scripts/test-with-vinyl-cache.sh ../vinyl-cache`: 4/4 passed after correcting VTC fixture counts and the three-party publication-race barrier.
- First full enabled check: 57/58 passed; `cachetag_pm00027_interned.vtc` failed. Per BR-018 this run remains recorded as test evidence.
- Isolated rerun of `cachetag_pm00027_interned.vtc`: 1/1 passed without a code or test change, identifying the full-run failure as non-reproducible timing-sensitive lifecycle evidence rather than a deterministic count/accounting regression.
- `CACHE_TAG_CONFIGURE_ARGS='' CACHE_TAG_CHECK_TARGET=check scripts/test-with-vinyl-cache.sh ../vinyl-cache`: production surface with set interning disabled passed 38/38.
- Replacement full enabled check: 58/58 passed, including the publication-race VTC for a second successful Docker execution.
- One-run benchmark smoke arms completed through `scripts/benchmark-cachetag-vmod.sh ../vinyl-cache` with `BENCH_SET_INTERNING=1` and `BENCH_SET_INTERNING=0`, using separate build caches and result directories. Both runs completed successfully.
- `benchmarks/summarize_results.py` was exercised inside the Docker build image against both smoke artifacts. It accepted the extended VSC rows and rendered the outside-`obj_mtx` allocation, migration/accounting, allocation-failure, and candidate-discard fields for both configurations.
- `git diff --check`: passed.

## Scope and follow-up

The VTCs and benchmark smoke runs intentionally do not claim throughput or latency improvement. They check ownership and counter invariants, artifact generation, and summarizer compatibility only: no shared hit consumes the miss-only fault, initial allocation failure is fail-closed, a later grow failure keeps an existing table usable, old-table storage converges after the request stream stops, and a same-set publication race leaves one canonical registry node. A future benchmark decision round still requires the separately frozen A/A, uninstrumented rate, and instrumented lock evidence described by the plan.

## 2026-08-11 independent review follow-up

A Terra standards review found duplicated attach cleanup paths, duplicate test-hook declarations, and a generation field that was incremented but not used for stale-publication validation. A Luna spec review then identified three correctness and observability gaps: post-table-publication error exits could return without waking migration maintenance, the resize worker's bounded migration holds were omitted from the table-grow timer, and detach-all did not advance the generation to invalidate bucket arrays prepared before a cold/reset transition.

The attach exits now share one cleanup helper. Error cleanup checks the live migration state and wakes maintenance when required. Prepared arrays capture and validate the generation, detach-all advances it, worker batches update the bounded table-grow timer, and duplicate hook declarations were removed. The legacy inside-lock set-allocation timer descriptions now state that all three fields remain zero.

`cachetag_pm00032.vtc` now covers a miss-only failure immediately after successful growth-table publication and drains the migration to zero active/old state. The first version incorrectly expected the VTC background thread itself to converge without the deterministic drain hook and failed after five seconds; the corrected assertion uses the documented test drain seam. After the review fixes, the targeted Docker command for `cachetag_pm00030.vtc` through `cachetag_pm00033.vtc` passed 4/4.

The review also found acceptance-coverage gaps that remain explicit: no VTC forces an intern-table allocation retry to overlap object-segment or side-map mutation, and the migration VTC does not prove invalidation from known active and old buckets separately. The implementation review found no remaining concrete defect in those paths, but the existing tests must not be described as covering those two plan items.
