# Fellow lifecycle findings, 2026-06-24

## Remote validation

The authoritative validation target was `ssh ubuntu@51.159.110.61` through `scripts/remote-benchmark.sh`.

The current durable Fellow patch stack includes `patches/fellow/0003-fix-fellow-shutdown-drain-races.patch`. It keeps the Fellow stevedore attached until `fellow_cache_obj_wait_written()` completes during shutdown drain, then mutates the object to the expired fallback stevedore without clearing `oc->stobj`.

The grouped-fsync validation run was `remote-20260624T102020Z-fellow-local-cost-100k` under `benchmarks/remote-results/20260624_51.159.110.61/fellow-local-cost-100k-grouped-lifecycle-objectfix-only-final-1run/`.

The close-path fix validation run was `remote-20260624T103306Z-fellow-local-cost-100k` under `benchmarks/remote-results/20260624_51.159.110.61/fellow-local-cost-100k-grouped-close-logcache-drain-1run/`.

## Results

- `wal_fsync=grouped` was applied in the generated VTC and metadata. The generated `vinyld` command included `-p timeout_idle=1 -p backend_idle_timeout=1`, and the VTC added `delay 2` before reset.
- The load driver completed cleanly: `driver_load_wall_seconds=9.532513044`, `driver_errors=0`.
- The purge driver completed cleanly: `driver_purge_wall_seconds=0.000294680`, `driver_errors=0`.
- The earlier shutdown-drain object lifecycle assertions did not recur. Specifically, this run did not hit `stvfe_oc_log_submitted: Assertion '((stvfe)) != 0' failed` or `ObjGetXID` with `oc->stobj->stevedore == NULL`.
- Reset still fails in Fellow log close. The fresh fatal path is `fellow_logcache_take -> fellow_logcache_get -> fellow_logs_iter -> fellow_logs_rewrite -> fellow_logs_close -> fellow_log_close`, reported as `Wrong turn at /work/vinyl-src-copy/bin/vinyld/cache/cache_main.c:388`.
- With `patches/fellow/0004-fix-fellow-close-logcache-drain.patch`, the same grouped 100k workload passed: `TEST /results/workloads/cachetag_low_fanout_unique.vtc passed (21.538)`, `driver_load_wall_seconds=9.349459458`, and load/purge `driver_errors=0`.
- The successful grouped run recorded `system_disk_flush_ios_delta=55`, `system_disk_flush_await_ms_avg=0.550324675`, `system_disk_flush_await_ms_max=2.000000000`, and `system_disk_flush_ios_per_second_max=8`.

## Fsync finding

The grouped-fsync change is applied and materially changes runtime. Prior 100k remote measurements showed strict WAL fsync load around `493.6s` with about `100150` disk flushes, while grouped WAL fsync loaded in about `9.25s` to `9.53s` with about `54` disk flushes. The remaining long tail is not the 100k object load path; it is reset-time Fellow log close/rewrite.

## Open bug

The remaining lifecycle bug was in Fellow log close/rewrite. Close-time `fellow_logs_close()` rewrites logs after the workload and purge have succeeded, and the failure moved from object handoff assertions to log-cache/mempool behavior during `fellow_logs_iter()`.

The fix in `0004` addresses two close-time resource pressure problems:

- `fellow_logcache_init()` now caps log-cache metadata entries to `logblk_mempool_space()` so metadata capacity cannot outrun backing 4KB scratch-block capacity.
- `fellow_logcache_take()` now reuses a non-current used entry before blocking on `logblk_mempool_get()` when the pool is empty.
- `FP_FINI` log iteration now drains accumulated `tofree` regions incrementally, which avoids keeping every object/data extent in memory until the end of close.

Speculative probes that should not be treated as fixes:

- Forcing log memfail around close-time rewrite converted the hang into an immediate `logblk_mempool_get()` assertion, which confirms the path still expects log-block allocation during close.
- Reusing the global log-block pool and reducing prefetch did not eliminate the close-time failure.
- Allocating close scratch memory outside membuddy moved the failure to `fellow_logcache_steal()`, exposing assumptions around reusable cached log entries.

The next fix should isolate a smaller Fellow log-close reproducer before changing log-cache allocation semantics. The benchmark now has enough knobs to keep the cachetag workload out of the way while exercising the reset path.

## Benchmark controls added

- `BENCH_SHUTDOWN_DRAIN_SECONDS` inserts a final VTC `delay`, defaulting to `2` for Fellow and `0` otherwise.
- `BENCH_TIMEOUT_IDLE` and `BENCH_BACKEND_IDLE_TIMEOUT` pass through to generated `vinyld` parameters, defaulting to `1` for Fellow.
- `BENCH_HTTP_DISABLE_KEEPALIVES` lets the Go driver disable HTTP keep-alives and records `driver_disable_keepalives` in driver output.

Failure-path IO capture still needs hardening. Successful grouped/strict comparisons produced useful flush metrics, and the successful close-path validation captured IO in `cachetag_low_fanout_unique.run-1.time`, but reset-failure tarballs did not always include system IO sample outputs. Post-processing should preserve those artifacts even when `vinyltest` exits non-zero.
