# Move set-interning allocation and growth out of `obj_mtx`

Rules reviewed: BR-001 through BR-026; applicable: BR-003 (snapshot counters must continue to describe physical retained state), BR-004 (new VSC values must be flushed before a benchmark snapshot), BR-011 and BR-012 (the existing timing screen is lock/counter evidence, not a throughput result), BR-013 (do not infer VMOD CPU from whole-tree CPU), BR-016 (separate configure-arm builds), BR-017 (this change creates a new benchmark identity), BR-018 (failures are test evidence and must not be hidden), BR-019 (deterministic workload shapes), BR-023 (no latency acceptance claim without a budget and raw samples), BR-024 (establish an A/A noise floor before judging a future rate delta), and BR-026 (freeze the post-change verification scope before a new benchmark host is used).

**Status:** Revised after design review and ready for implementation. The former remote benchmark host has been deleted; this plan contains no remote execution step.

## Decision requested

Implement bounded incremental table migration, reuse the existing outside-lock multi-fold storage allocation as the unpublished intern candidate, and use the existing resize worker to guarantee migration convergence. Do not attempt a broad lock-free registry or an all-at-once copy-on-write rehash in this change.

The result must remove all allocator calls and all growth-specific unbounded table walks from `obj_mtx`. Ordinary lookup still traverses its selected hash chain, as it does today. Migration may retain a fixed, small number of pointer relinks under `obj_mtx` per registry operation; that is the mechanism that preserves correctness while eliminating the observed multi-millisecond growth pause.

## Evidence

The current implementation does the following while holding `obj_mtx`:

- `cachetag_intern_maybe_grow_locked()` calls `calloc()`, walks every old bucket and relinks every set, then calls `free()`.
- `cachetag_intern_acquire_locked()` calls `malloc()` and copies the canonical fold vector before publishing the candidate.
- `cachetag_intern_release_locked()` calls `free(set)` while holding the same mutex.

The validated one-million-object screen used five-tag exact-set endpoints, Default storage, xkey/no-index disabled, and `BENCH_INSTRUMENT_OBJ_MTX=1`. All four artifacts were valid, no-swap, and under-saturated; they are therefore lock evidence only, not rate or CPU evidence.

| Shape and clients | Average acquire under `obj_mtx` | Largest acquire | Table growth | Candidate allocation | Largest attach wait |
| --- | ---: | ---: | --- | --- | ---: |
| Mostly shared, c24 | 0.070 us | 59 us | 1 growth, below timer resolution | 1 allocation, below timer resolution | 1.222 ms |
| Mostly unique, c24 | 0.448 us | 10.617 ms | 15 growths, 21.637 ms total, 10.616 ms max | 1M allocations, 51.644 ms total, 3.047 ms max | 10.635 ms |
| Mostly shared, c48 | 0.068 us | 108 us | 1 growth, below timer resolution | 1 allocation, below timer resolution | 2.192 ms |
| Mostly unique, c48 | 0.478 us | 10.822 ms | 15 growths, 22.480 ms total, 10.821 ms max | 1M allocations, 54.032 ms total, 135 us max | 5.558 ms |

The normal lookup/insert path is not the concern: even the unique endpoint sums to under half a second across one million acquisitions. The outliers are. In particular, the c24 unique row's 10.635 ms maximum attach wait is consistent with a contender arriving during the 10.616 ms largest table growth. The full provenance and counter extract are in `20260810_1845_report_set-interning-obj-mtx-attribution-screen.md`.

The c24 unique row's nested maxima also attribute the episode tightly: the complete intern acquire was 10.617 ms and the table-grow portion inside it was 10.616 ms. The screen cannot distinguish rehash CPU from a deschedule or page-zeroing delay inside that timed region, but all of those possibilities mean the thread retained `obj_mtx` for the observed wall time. This is sufficient design evidence for removing the monolithic locked operation, while remaining insufficient for a throughput or post-change latency claim.

## Target design

### Invariants

- `obj_mtx` remains the sole authority for membership lookup, reference count changes, registry insertion/removal, table publication, and migration state transitions.
- In the interning build, the existing multi-fold storage allocation is laid out as an unpublished `struct cachetag_interned_set` candidate. Allocation, fold population, sorting, and hashing all happen before `obj_mtx`; the candidate is not reachable from the registry until a short locked publish step succeeds.
- A candidate that loses a same-set race is freed only after dropping `obj_mtx`.
- The old and active tables may coexist during migration. Lookups search active first and old second; an interned set is linked in exactly one table at a time.
- Every locked migration step has a fixed work budget. It must never scan an arbitrary number of empty buckets or relink an arbitrary chain.
- No dense object entry is initialised from a candidate until the final locked attach transaction has revalidated the objcore, object capacity, side-map insertion capacity, intern generation, and migration state.
- Removed sets and retired table arrays are detached while locked into a caller-owned cleanup handoff and freed after unlock. There is no namespace-global retired-set or retired-table list and no retirement-list allocation under `obj_mtx`.
- The resize worker is woken when an intern migration starts and advances it in bounded batches even when the purge sweep interval is zero. Small request-side steps reduce runway pressure, but correctness and memory convergence do not depend on later attaches arriving.
- Live set/reference gauges and cumulative hit/miss counters retain their current meanings. Table-byte and index-memory gauges include active plus old table storage for the whole migration interval. `index_memory_bytes` also includes detached set and table bytes until the outside-lock owner frees them; expose both categories through distinct debug gauges rather than silently losing physical-accounting accuracy.

### Representation

Keep `intern_buckets` and `intern_nbuckets` as the active table, then add an old-table migration state under `CACHE_TAG_SET_INTERNING`:

```c
struct cachetag_interned_set **intern_old_buckets;
size_t intern_old_nbuckets;
size_t intern_migrate_cursor;
uint64_t intern_generation;
unsigned intern_migration_active;
size_t intern_detached_set_bytes;
size_t intern_detached_table_bytes;
```

Use a caller-local, fixed-shape cleanup accumulator for unpublished candidates, zero-reference sets, and detached tables. It may reuse `struct cachetag_interned_set::next` after a set is unlinked and hold fixed slots for the active and old arrays needed by teardown. Locked helpers add pointers and byte counts to this accumulator without allocating; the top-level attach, invalidation, sweep, worker, detach-all, or delete caller frees its contents after unlocking.

Use `TAG_INTERN_MIGRATE_STEPS = 4` as the initial fixed budget. One step either examines one old bucket or moves one set from that bucket's head into the active table. Counting empty-bucket examination against the same budget prevents a sparse old table from creating a new linear scan. Four steps complete a grow before the next capacity threshold during a unique-set load while keeping each critical section constant-time.

The four-step runway argument is a performance expectation, not a correctness precondition. If a migration is still active when the active table reaches its nominal threshold, keep inserting into the active table and allow chains to grow until the worker completes the migration. Never start a third live table or perform an emergency full rehash under `obj_mtx`.

### Candidate preparation and publish/retry

Do not allocate a second set node after an intern miss. `cachetag_attach()` already allocates multi-fold scratch storage outside `obj_mtx`. In the interning build, change that storage layout so it is the complete unpublished candidate: its header precedes the fold vector, its initial reference count is zero, and `cachetag_fold_storage_values()` returns its flexible-array folds. This replaces the current scratch allocation followed by a second `malloc()` and copy with one allocation.

Make the ownership contract explicit: after `cachetag_record_attach_purgemap_take()` is called in an interning build, it consumes every multi-fold candidate on every return path. A successful miss transfers the candidate to the registry, while a hit, validation failure, retry cancellation, or attach rollback queues it in the caller-local cleanup accumulator. `cachetag_attach()` frees a candidate itself only when preparation fails before the take call.

Refactor the current monolithic `cachetag_intern_acquire_locked()` into three locked roles plus outside-lock cleanup:

1. `cachetag_intern_lookup_locked()` searches active then old tables and increments references/hits on a match.
2. `cachetag_intern_publish_locked()` validates that a miss is still live, links the prepared candidate into the active table, sets its first reference, and records one miss.
3. `cachetag_intern_migrate_locked()` performs no more than the supplied step budget and returns any completed old table through the caller-local cleanup accumulator.

The attach path already has a publish/retry pattern for object segments and side-table maps. Extend that same transaction for intern-table allocation rather than introducing a separate candidate-allocation retry:

1. Allocate and populate the candidate folds, then sort and hash them before taking `obj_mtx`.
2. Enter the existing `again` transaction and revalidate the objcore, dense object capacity, and side-map insertion capacity before attempting intern publication. Preserve the candidate across any existing unlock/allocate/relock retry.
3. Run the small request-side migration budget and look up the canonical set. A hit increments the canonical reference and queues the unpublished candidate for outside-lock cleanup.
4. On a miss, consume `test_fail_next_intern_alloc` at the same logical seam as today. The hook remains armed across shared hits; when consumed on a miss it queues the already-allocated candidate for cleanup and fails the attachment closed without changing registry counters or object attachment.
5. If the initial or next bucket array is required and no matching prepared array is available, record the current generation and capacity, unlock, allocate the zeroed array, relock, and return to the start of `again`. Do not initialise the dense object entry before this complete revalidation.
6. After revalidation, discard a stale prepared array after unlock or publish a still-required array, increment the generation, and start migration. Rerun the lookup after any table publication so a competing insertion becomes a hit.
7. On a live miss, publish the candidate and continue the existing dense-object and side-map commit. An unexpected side-map insertion failure releases the just-acquired canonical reference through the same cleanup accumulator.
8. After the final unlock, free every unpublished candidate, stale or detached bucket array, and zero-reference set owned by this call, then wake the resize worker if intern migration remains active.

Actual candidate-allocation failure occurs before `obj_mtx` and fails the attach closed. The existing test hook continues to model the current miss-only allocation-failure contract even though the physical allocation is now earlier; this preserves `cachetag_pm00030.vtc` and prevents a shared hit from consuming the one-shot failure.

### Table growth and migration

Do not build a complete replacement table under the lock. A truly outside-lock whole-table rebuild would require either immutable table links plus a mutation log or an RCU-style lifetime scheme; both are disproportionate to this experimental VMOD and would widen the correctness surface.

Instead:

1. Compute the next power-of-two capacity under the lock and allocate the empty bucket array outside it.
2. Under the lock, validate that the active table still requires that capacity and that no migration is already active. Publish the prepared array as active and retain the former active array as old. This is pointer assignment and state initialisation only.
3. New candidates insert into active. Lookup and zero-reference removal search active first and old second. Each acquire/release/invalidation may perform at most `TAG_INTERN_MIGRATE_STEPS` request-side steps.
4. Mark the migration as maintenance work and wake the existing resize worker. Extend `cachetag_resize_needs_work_locked()` and `cachetag_resize_maintenance()` so the worker advances an independently bounded intern batch, yields through the existing maintenance loop, and keeps running when `sweep_interval = 0s`.
5. When the cursor reaches the old-table end, detach the now-empty old array under the lock into the current request or worker's local cleanup accumulator and free it after unlock. A new growth cannot start until the old table has been detached, bounding registry-owned tables to active plus one old table.
6. If next-table allocation fails while an active table exists, preserve current behaviour: keep using the active table and permit chains to grow. Only initial-table allocation failure fails the attach closed.

This moves `calloc()` and `free()` out of `obj_mtx`, removes the redundant candidate allocation and copy, replaces the current O(number of sets) rehash pause with bounded pointer work per request or worker batch, and preserves a simple mutex-based ownership model. During a migration, physical bucket storage is temporarily old plus active; after detachment, its bytes remain tracked until the outside-lock free completes.

### Removal, sweep, and teardown

Refactor `cachetag_intern_release_locked()` to unlink a zero-reference set from whichever table currently contains it, decrement live gauges, and append it to the current caller's cleanup accumulator. The helper does not free memory and does not publish the node to namespace-global retirement state.

Update every caller that can release an interned set:

- attachment rollback after a side-table insertion failure;
- invalidation;
- sweep disposal; and
- namespace detach-all and delete.

The sweep accumulates zero-reference nodes only for its current bounded locked batch and frees them in the existing unlocked gap before yielding or reacquiring `obj_mtx`.

For the interning build, detach-all and delete must not release every object reference or scan every intern bucket while holding `obj_mtx`. After the object-event subscription and resize worker have stopped, detach the dense object segments, side tables, and active/old intern arrays under the mutex, zero the live gauges and registry state, then traverse the detached intern arrays and free each canonical set exactly once after unlocking. Because each set is linked in exactly one table, the two detached arrays are a complete and duplicate-free ownership inventory.

For ordinary zero-reference removal, add the known node size to detached-set accounting before unlocking. For teardown, capture the existing aggregate `intern_bytes` value and the active/old array capacities under the mutex; do not scan nodes merely to calculate their bytes. Include those counts in `index_memory_bytes` and dedicated debug gauges, free the detached memory, then decrement the detached-byte accounting. Follow the existing `resize_detached_bytes` lock and snapshot discipline so a concurrent VSC snapshot never reports the allocation as gone before the physical free completes. Unpublished candidates are transient request allocations that never become namespace-owned and are not included in the retained-index gauges.

## Implementation sequence

1. Change the interning build's multi-fold storage layout into an unpublished intern candidate and define the take/cleanup ownership contract. Preserve the direct-vector build and its existing storage contract.
2. Add the active/old-table, cursor, generation, migration, and detached-byte fields plus a caller-local cleanup accumulator. Add helpers for lookup/publish, bounded migration, table-byte accounting, locked cleanup handoff, and outside-lock cleanup.
3. Thread the prepared candidate and optional prepared bucket array through `cachetag_record_attach_purgemap_take()`'s existing full `again` transaction. Audit every exit and retry for exactly-one candidate/table disposition and no partially published dense object entry.
4. Implement two-table migration, integrate it with the existing resize worker, and update release, invalidation, sweep, detach-all, and delete to reap outside the mutex without an unbounded teardown scan under `obj_mtx`.
5. Extend VSC/summarizer diagnostics: retain the existing inside-lock acquire timer, add outside-lock candidate and table allocation timing, and add migration state, active-plus-old table bytes, detached-set bytes, and detached-table bytes. The legacy inside-lock set-allocation timer becomes zero because that operation no longer exists; the new outside-lock candidate timer counts every prepared multi-fold candidate, including candidates discarded on a canonical hit. The post-change inside-lock table-grow maximum should describe only bounded publication/migration work, not allocation or a full rehash.
6. Update the interning design documentation to state the temporary two-table memory cost, worker convergence, ownership contract, and allocation-failure fallback behaviour.

## Test and acceptance plan

All verification is through the documented Docker harness; do not compile VMOD or benchmark helpers on the host.

1. Run the existing set-interning regression, `cachetag_pm00030.vtc`, in the enabled diagnostic/test-hook build. It must retain exact set/ref/hit/miss values and preserve the one-shot miss-only allocation-failure semantics. Add a case proving that a shared hit does not consume the hook and the next distinct miss fails closed.
2. Add a deterministic grow-transition VTC using a deliberately small test-only initial interning table. Cover: shared hit, distinct miss, migration-active lookup in both old and active tables, candidate race/retry, invalidation of sets from each table, active-plus-old table-byte accounting, and post-migration convergence.
3. Add a test-only next-table-allocation fault. Initial table allocation must fail closed; a later growth allocation failure must retain availability by inserting into the existing table and increment an explicit failed-grow diagnostic counter.
4. Add a concurrency VTC that sends the same multi-tag membership concurrently. It must produce one miss and the expected references, with every losing preallocated candidate freed outside the registry. Run this test repeatedly in Docker to exercise publish/retry interleavings.
5. Force an intern-table allocation retry to overlap object-segment or side-map state changes. Assert that the complete attach transaction restarts, the objcore is rechecked, no dense entry becomes visible early, and every candidate and prepared table has exactly one owner.
6. Stop the request stream immediately after one insertion triggers a grow, including with `sweep_interval = 0s`. The resize worker must finish migration, free the old table outside `obj_mtx`, and converge active/old/detached byte gauges without another attach or invalidation.
7. Exercise purge sweep, explicit compact, detach-all, and namespace delete while a migration is active. Assert no leaked live set/ref/active/old/detached gauges after cleanup and no stale membership is served.
8. Run `CACHE_TAG_CHECK_TARGET=check scripts/test-with-vinyl-cache.sh ../vinyl-cache` in the normal enabled diagnostic build, then a production-surface Docker check with set interning disabled. Run the small Docker benchmark smoke for both arms to verify VSC generation and summarizer fields.
9. On a future comparable Linux host, freeze a new decision-round plan before benchmarking. It must include a same-code A/A pair, an uninstrumented B/P achieved-rate pair, and a separately instrumented lock screen. Do not compare it with the deleted-host result or reuse its rates.

## Review focus

- Is bounded two-table migration the right complexity/performance trade-off versus a larger immutable-table/RCU redesign?
- Do the request and worker migration budgets keep critical sections short while guaranteeing prompt convergence after a workload stops? Both constants are deliberately isolated for later measurement, not exposed as runtime tuning knobs.
- Does the preallocated-candidate take contract give every candidate, canonical reference, prepared table, and detached allocation exactly one owner across every retry and rollback?
- Should a future production memory envelope be a namespace-wide resident-index limit rather than a special limit for temporary intern tables? No new interning-only limit is required while this remains opt-in experimental functionality, provided the physical gauges and allocation-failure fallback are correct.

## Regression-test contract

The interning regression VTCs use the existing `test_fail_next_intern_alloc()` seam and the following test-only surface. `test_intern_initial_buckets(INT)` sets a deliberately small active table before the first attach. `test_fail_next_intern_table_alloc()` fails one prepared bucket-array allocation. `test_intern_worker_hold(BOOL)` pauses the maintenance worker so a VTC can inspect the old-table lookup path without a scheduling race. `test_intern_migrate_buckets(INT)` advances at most the requested bounded migration steps. `test_intern_active_buckets()` and `test_intern_old_buckets()` expose the table capacities for deterministic state assertions. The existing `test_resize_worker_drain(INT)` then drives the worker to convergence without requiring another request. The migration-active VSC gauge is asserted directly rather than through a separate VCL method.

The diagnostic VSC names used by the new VTCs are `volatile_interned_old_table_bytes`, `volatile_interned_detached_table_bytes`, `volatile_interned_table_alloc_failures`, `volatile_interned_table_grow_failures`, and `volatile_interned_candidate_discards`. They are deliberately separate from the existing live-set, reference, hit, miss, and aggregate table-byte gauges. Old-table storage remains visible during migration, detached bytes remain visible until the outside-lock free, a failed grow does not fail an attach when an active table exists, and losing same-set candidates are counted as outside-lock cleanup.

The grow-transition VTC loads five distinct multi-fold sets into a four-bucket table so the fifth set deterministically starts migration, exercises a shared hit and an old/active lookup while migration is held, purges a set during migration, and drains the worker to zero old and detached bytes. The allocation-fault VTC fills an eight-bucket table with eight sets, then arms the ninth insertion so the later growth allocation fails while the active table remains usable. The publication-race VTC uses two backend barriers and distinct object URLs carrying one canonical set; it requires one miss, one hit, two references, and at least one discarded losing candidate.

These tests are specification-level coverage for the planned hooks and counters. They are not host-build verification; run them only through `scripts/test-with-vinyl-cache.sh ../vinyl-cache` in the diagnostic/set-interning/test-hook arm.
