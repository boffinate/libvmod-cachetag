# Set-interning `obj_mtx` instrumentation log

Rules reviewed: BR-001 through BR-026; applicable: BR-003, BR-004, BR-011, BR-013, BR-016, BR-017, and BR-019.

## Change

Added opt-in VSC timing counters for the set-interning portion of `obj_mtx`:

- complete intern acquire (lookup, grow if needed, node creation/copy, and publication);
- intern-table allocation and rehash; and
- interned-set node `malloc`.

The timer state lives on `struct cachetag_index`, which is already protected by `obj_mtx`, and is copied to VSC only during a snapshot. This deliberately avoids taking `counter_mtx` inside each instrumented intern acquisition. The existing request attach lock-wait VSC remains the contention measurement. Sorting and XXH3 hashing remain outside `obj_mtx` and outside the new acquisition timer.

The counters are available in both configure arms. They are zero when set interning is disabled or when `BENCH_INSTRUMENT_OBJ_MTX=0`.

## Local Docker verification

Ran the documented Docker benchmark harness with one 100-object repetition of `cutover-mostly-shared,cutover-mostly-unique`, five tags/object, xkey and no-index disabled, no warm phase, and `BENCH_INSTRUMENT_OBJ_MTX=1`.

- Enabled arm: valid 2/2. Shared: 100 acquires, one table grow, one node allocation, one live set, 100 references, 99 hits, one miss. Unique: 100 acquires, two table grows, 100 node allocations, 100 live sets, 100 misses. The low-volume local timing maxima were 2 microseconds shared and 5 microseconds unique; this is smoke evidence only, not a performance result.
- Disabled arm: valid 2/2. Every existing and new interning VSC counter was zero for both profiles.
- The Docker summarizer exposed the new counters in both human-readable output and CSV aggregation. Local result paths are `/private/tmp/libvmod-cachetag-set-interning-lock-enabled` and `/private/tmp/libvmod-cachetag-set-interning-lock-disabled`.

The local host was IO/single-core limited and is not used for any throughput or CPU conclusion. The remote instrumented screen is specified separately in `20260810_1815_plan_set-interning-obj-mtx-attribution-screen.md`.

## Remote diagnostic outcome

The remote B24/P24/B48/P48 screen completed with valid, no-swap artifacts. The enabled shared endpoint averaged 0.07 microseconds per intern acquisition; the enabled unique endpoint averaged 0.45–0.48 microseconds. The unique endpoint also showed 15 intern-table growths, with a 10.6–10.8 ms largest growth while holding `obj_mtx`, and one 3.0 ms set-node allocation at c24. This supports keeping ordinary interning as-is for now while treating allocation/growth under `obj_mtx` as the hardening target if attach pause safety is required. See `20260810_1845_report_set-interning-obj-mtx-attribution-screen.md` for provenance, full counters, and limitations.
