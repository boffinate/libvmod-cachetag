# Set-interning `obj_mtx` attribution screen report

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (build-inclusive cgroup peaks excluded), BR-002 (paired baseline/enabled rows), BR-003 (final post snapshots retained), BR-004 (VSC flush semantics), BR-008 (one sequential remote session delegated at the owner's request), BR-009 (generated VTCs used `debug=none`), BR-011 (all rows are under-saturated and therefore not rate or CPU evidence), BR-013 (whole-tree CPU is not VMOD attribution), BR-014 (one host cohort), BR-016 (fresh build per arm), BR-017 (new source identity), BR-018 (no retries), BR-019 (one-million-object deterministic inputs), BR-020 (8 GiB Default storage and attach-only scope), BR-023 (no tail acceptance claim), BR-024 (single diagnostic repetition, not a noise estimate), BR-025 (host power metadata captured), and BR-026 (completed frozen four-row budget).

## Decision

Do not redesign the ordinary interning hit/miss path solely to shorten its average `obj_mtx` critical section. It is 0.07 microseconds per acquire for one shared five-tag set and 0.45–0.48 microseconds for one million unique five-tag sets.

Do treat table growth under `obj_mtx` as a design risk before presenting the option as pause-safe production behaviour. The unique-set endpoint performed 15 growths and one took 10.6–10.8 ms while the lock was held. One c24 node allocation also took 3.0 ms while holding that lock. These are rare rather than steady-state costs, but they are long enough to stall a concurrent attach. A later hardening change should allocate/rebuild the new bucket array, and preferably allocate the candidate set node, outside `obj_mtx`, then publish with a revalidate/retry protocol.

## Validity and provenance

All four fetched artifacts passed their SHA-256 check and summarizer validity gates: 2/2 workload runs valid in each artifact, zero driver errors, and no swap. Each used the same cachetag input hash `cd02a693d00fa503422d64304a1fa7c4db67373e9706227dda2f83e9398a1105`, the same Vinyl input hash `eb02e251a2c79341d13ad7e99190b3f50571c7b514eb7184468d02beaa5c8a06`, five tags/object, default storage, xkey/no-index disabled, and `BENCH_INSTRUMENT_OBJ_MTX=1`. B24/P24 used 24 clients and B48/P48 used 48. The baseline arms had `build_set_interning=0`; the enabled arms had `build_set_interning=1`.

The AMD EPYC 4345P host classified every row as `harness/under-saturated` (average maximum CPU 63.14–65.26%). Therefore this report makes no throughput, capacity, latency, or VMOD CPU attribution claim. The timing counters themselves are opt-in diagnostic overhead.

## Lock evidence

The figures below are the final pre-purge VSC snapshots after one million successful cachetag attaches. `B -> P wait` compares the direct-vector baseline's mean request-attach mutex wait with the enabled arm's mean wait at that client setting. It is an informative contention signal, not a statistically sufficient performance comparison.

| Clients | Exact-set shape | Intern acquire while holding `obj_mtx` | Allocation/growth while holding `obj_mtx` | B -> P attach wait |
| ---: | --- | --- | --- | --- |
| 24 | Mostly shared | 0.070 us average; 59 us max; 2/1,000,000 above 50 us | 1 table growth and 1 node allocation; both below timer resolution | 0.791 -> 1.361 us average; 642 -> 1,222 us max |
| 24 | Mostly unique | 0.448 us average; 10.617 ms max; 54/7/5/1 above 50/250 us/1/10 ms | 15 table growths: 21.637 ms total, 10.616 ms max; 1,000,000 node allocations: 51.644 ms total, 3.047 ms max | 1.054 -> 2.143 us average; 1.168 -> 10.635 ms max |
| 48 | Mostly shared | 0.068 us average; 108 us max; 4/1,000,000 above 50 us | 1 table growth and 1 node allocation; both below timer resolution | 1.764 -> 2.180 us average; 3.297 -> 2.192 ms max |
| 48 | Mostly unique | 0.478 us average; 10.822 ms max; 125/7/4/1 above 50/250 us/1/10 ms | 15 table growths: 22.480 ms total, 10.821 ms max; 1,000,000 node allocations: 54.032 ms total, 135 us max | 1.767 -> 3.330 us average; 2.783 -> 5.558 ms max |

The normal unique-set acquisition sum is under half a second across one million objects. The 15 table-growth calls account for roughly 22 ms in total, but one growth dominates each run. The c24 unique enabled row's 10.635 ms maximum attach wait is consistent with another attach arriving during its 10.616 ms largest table growth. The c48 run still has one 10 ms acquisition/growth event, although no waiter spanned all of it.

The enabled unique rows also add 1.09 microseconds (c24) and 1.56 microseconds (c48) to the observed mean attach wait relative to their one-run direct baselines; shared rows add 0.57 and 0.42 microseconds. Those differences are directionally consistent with more work while the mutex is held, but cannot be apportioned cleanly between set interning and the opt-in timing code, or converted into a throughput conclusion, from one under-saturated instrumented sample per arm.

## Interpretation

The main caveat is narrowed substantially:

- Sorting and hashing are correctly outside `obj_mtx`; they are not implicated by the lock timers.
- Reusing a shared set is inexpensive enough that there is no evidence for a broad lock-free or per-set-lock redesign.
- Unique-set steady-state node creation is also inexpensive on average, but allocator outliers can be millisecond-scale.
- Intern-table grow is an unambiguous `obj_mtx` pause source. It has a bounded frequency for a finite load but an unbounded operational concern as a table continues to grow, and it can materially delay a contending attach.

The practical next decision is whether pause safety matters for the intended opt-in deployments. If it does, design growth/allocation outside the mutex before calling set interning production-ready. If the current option remains explicitly experimental and its target is memory-constrained, batch-style workloads without a strict attach-pause budget, the present implementation is acceptable for further uninstrumented achieved-rate and memory benchmarking; record the rare growth-pause caveat in its documentation.

## Artifacts

- `benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/B24/cachetag-benchmark-results-remote-20260810T171029Z-local-cost-attach-1m.tgz`
- `benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/P24/cachetag-benchmark-results-remote-20260810T171339Z-local-cost-attach-1m.tgz`
- `benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/B48/cachetag-benchmark-results-remote-20260810T171437Z-local-cost-attach-1m.tgz`
- `benchmarks/remote-results/20260810_51.159.202.218/set-interning-obj-mtx/P48/cachetag-benchmark-results-remote-20260810T171542Z-local-cost-attach-1m.tgz`
