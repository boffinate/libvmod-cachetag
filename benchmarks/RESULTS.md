# Cachetag Benchmark Results

Last updated: 2026-07-17

We compared Default cachetag, in-memory `xkey`, Buddy cachetag, and persistent Fellow cachetag on 10 million objects with four low-fanout tags per object. Default cachetag used 833.18 MiB of tracked index memory versus `xkey`’s 4.55 GiB—an 82.13% reduction—and its median load rate was 14.83% higher.

Every reported run completed the workload with 50,000/50,000 residency hits, no warm misses or errors, no LRU nukes, and no swap. The in-memory figures are achieved rates at the 24-client calibration peak, not saturated throughput or capacity; Fellow was IO-limited.

| Lane | Observed load RPS median (min–max) | Observed warm RPS median (min–max) | Membership evidence |
| --- | ---: | ---: | --- |
| Default cachetag | 80,175.81 (79,937.80–80,450.17) | 188,030.94 (187,790.55–188,791.28) | 833.18 MiB tracked, 87.37 bytes/object |
| Default in-memory `xkey` | 69,819.34 (69,555.03–69,963.03) | 191,756.85 (190,336.09–192,582.72) | 4.55 GiB tracked, 488.96 bytes/object |
| Buddy cachetag | 78,330.06 (78,055.06–78,737.78) | 189,878.34 (188,282.09–191,917.46) | 833.18 MiB tracked, 87.37 bytes/object |
| Persistent Fellow cachetag | 19,815.28 (19,630.68–19,914.72) | 82,464.79 (78,259.23–84,931.05) | 80 persistent attribute bytes/object; zero volatile cachetag objects/edges |

The tables below preserve earlier campaigns and workload shapes as historical context. Use the section above for the current README's 10M comparison.

## How To Read

`no-index` is the storage-only speed and memory baseline, with no tag index. `xkey` is the existing in-memory tag-index baseline. `cachetag` is the VMOD being tested. In the FDO-direct Fellow path, cachetag doesn’t rebuild an in-memory object-membership index after a restart or first touch. Higher RPS is better; lower memory use and latency are better. `n/a` means that the lane does not exist or was not run in that matrix.

## Load / Attach RPS

Cold load/attach path. These rows are clean unless the source cell carries an explicit caveat.

| Backend | Scale | Profile | `no-index` | `xkey` | `cachetag` | Source |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| Buddy | 100k | `extreme-high-fanout` | n/a | 73,001 | 73,107 | `buddy-local-cost-attach-100k` |
| Buddy | 100k | `low-fanout-unique` | n/a | 71,020 | 72,964 | `buddy-local-cost-attach-100k` |
| Default | 100k | `extreme-high-fanout` | n/a | 74,243 | 74,713 | `local-cost-attach-100k` |
| Default | 100k | `low-fanout-unique` | n/a | 71,942 | 74,694 | `local-cost-attach-100k` |
| Default | 1M | `extreme-high-fanout` | n/a | 74,891 | 74,796 | `local-cost-attach-1m` |
| Default | 1M | `low-fanout-unique` | n/a | 71,558 | 74,912 | `local-cost-attach-1m` |
| Default | 10M | `low-fanout-unique` | n/a | 66,117 | 71,025 | `lowfanout-10m` |
| Fellow persistent | 100k | `low-fanout-unique`, full-shape | n/a | n/a | 194 | `fellow-local-cost-100k`; anomalously slow |
| Fellow persistent | 1M | `low-fanout-unique`, full-shape | n/a | n/a | 18,928 | `fellow-local-cost-1m`; clean 2026-07-08 rerun, IO-limited |

## Warm-Hit RPS

Warm-hit path. Default/Buddy rows are separate warm matrices except 10M low-fanout, which includes warm in the low-fanout matrix. These rows are clean unless the source cell carries an explicit caveat.

| Backend | Scale | Profile | `no-index` | `xkey` | `cachetag` | Source |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| Buddy | 100k | `extreme-high-fanout` | n/a | 166,119 | 167,554 | `buddy-local-cost-warm-100k` |
| Buddy | 100k | `low-fanout-unique` | n/a | 166,482 | 168,744 | `buddy-local-cost-warm-100k` |
| Default | 100k | `extreme-high-fanout` | n/a | 165,167 | 166,791 | `local-cost-warm-100k` |
| Default | 100k | `low-fanout-unique` | n/a | 166,596 | 168,345 | `local-cost-warm-100k` |
| Default | 1M | `extreme-high-fanout` | n/a | 164,970 | 167,578 | `local-cost-warm-1m` |
| Default | 1M | `low-fanout-unique` | n/a | 165,903 | 168,107 | `local-cost-warm-1m` |
| Default | 10M | `low-fanout-unique` | n/a | 167,250 | 166,595 | `lowfanout-10m` |
| Fellow persistent | 100k | `low-fanout-unique`, full-shape | n/a | n/a | 37,629 | `fellow-local-cost-100k`; anomalous load row |
| Fellow persistent | 1M | `low-fanout-unique`, full-shape | n/a | n/a | 43,478 | `fellow-local-cost-1m`; clean 2026-07-08 rerun, IO-limited |

## Low-Fanout Memory

The tracked-memory column is `tracked_memory_bytes`; bytes per object is that value divided by the number of loaded objects. RSS is the highest `vinyld` resident set observed in the row. Cgroup peak covers the whole container, so it often reflects storage and object-body memory as well as the VMOD index.

| Backend | Scale | `cachetag` tracked | `cachetag` bytes/object | `xkey` tracked | `xkey` bytes/object | `cachetag` RSS | `xkey` RSS | Source |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Default | 100k | 11.57 MiB | 121 | 46.64 MiB | 489 | 132.80 MiB | 162.24 MiB | `local-cost-attach-100k` |
| Default | 1M | 99.75 MiB | 105 | 466.32 MiB | 489 | 1,013.40 MiB | 1.38 GiB | `local-cost-attach-1m` |
| Default | 10M | 1.01 GiB | 108 | 4.55 GiB | 489 | 9.61 GiB | 14.19 GiB | `lowfanout-10m` |
| Buddy | 100k | 11.57 MiB | 121 | 54.93 MiB | 576 | 150.00 MiB | 179.27 MiB | `buddy-local-cost-attach-100k` |

## Fanout Stress

Cachetag continues to use less tracked memory in the high-fanout rows. The 5M fanout stress row passed all nine runs. After relaxing the remote RAM-headroom guard, the 10M fanout/attach row also passed all nine runs.

| Matrix | Runs | Lane | Load RPS | Warm RPS | Tracked memory | RSS | Cgroup peak |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `fanout-100k` | 9/9 | `no-index` | 78,007 | 169,112 | n/a | 110.15 MiB | 243.65 MiB |
| `fanout-100k` | 9/9 | `xkey` | 73,881 | 166,592 | 28.23 MiB | 141.73 MiB | 249.46 MiB |
| `fanout-100k` | 9/9 | `cachetag` | 73,907 | 165,503 | 14.90 MiB | 135.50 MiB | 243.65 MiB |
| `fanout-1m` | 9/9 | `no-index` | 78,810 | 169,335 | n/a | 901.00 MiB | 1.41 GiB |
| `fanout-1m` | 9/9 | `xkey` | 74,243 | 166,523 | 282.29 MiB | 1.20 GiB | 1.58 GiB |
| `fanout-1m` | 9/9 | `cachetag` | 73,795 | 166,648 | 131.84 MiB | 1.02 GiB | 1.41 GiB |
| `fanout-5m` | 9/9 | `no-index` | 76,829 | 169,597 | n/a | 4.31 GiB | 6.95 GiB |
| `fanout-5m` | 9/9 | `xkey` | 72,564 | 164,977 | 1.38 GiB | 5.92 GiB | 7.75 GiB |
| `fanout-5m` | 9/9 | `cachetag` | 72,318 | 166,272 | 700.28 MiB | 5.07 GiB | 6.95 GiB |
| `fanout-attach-10m` | 9/9 | `cachetag` | 71,860 | n/a | 1.01 GiB | 9.61 GiB | 13.51 GiB |
| `fanout-attach-10m` | 9/9 | `xkey` | 70,910 | n/a | 2.76 GiB | 11.81 GiB | 15.28 GiB |

## Historical Fellow Attach Memory

These attach-only rows predate FDO-direct. They show the cost of the old persistent resident index and should not support current Fellow-direct claims.

| Matrix | Runs | Lane | Load RPS | Tracked memory | Bytes/object | RSS | Cgroup peak |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `fellow-storage-attach-100k` | 3/3 | storage only | 17,156 | n/a | n/a | 70.48 MiB | 174.14 MiB |
| `fellow-volatile-attach-100k` | 3/3 | volatile `cachetag` | 17,088 | 37.59 MiB | 394 | 130.23 MiB | 245.85 MiB |
| `fellow-persistent-attach-100k` | 3/3 | persistent `cachetag` | 15,881 | 37.59 MiB | 394 | 142.07 MiB | 288.09 MiB |
| `fellow-storage-attach-1m` | 3/3 | storage only | 16,786 | n/a | n/a | 442.08 MiB | 826.34 MiB |
| `fellow-volatile-attach-1m` | 3/3 | volatile `cachetag` | 16,609 | 352.26 MiB | 369 | 826.58 MiB | 1.19 GiB |
| `fellow-persistent-attach-1m` | 3/3 | persistent `cachetag` | 15,349 | 352.26 MiB | 369 | 903.50 MiB | 1.51 GiB |
| `fellow-storage-attach-5m` | 1/1 | storage only | 16,337 | n/a | n/a | 2.04 GiB | 3.64 GiB |
| `fellow-volatile-attach-5m` | 1/1 | volatile `cachetag` | 15,863 | 1.73 GiB | 372 | 3.84 GiB | 5.48 GiB |
| `fellow-persistent-attach-5m` | 1/1 | persistent `cachetag` | 15,048 | 1.73 GiB | 372 | 4.55 GiB | 7.37 GiB |
| `fellow-fanout-storage-attach-100k` | 3/3 | fanout storage only | 17,148 | n/a | n/a | 70.75 MiB | 172.42 MiB |
| `fellow-fanout-volatile-attach-100k` | 3/3 | fanout volatile `cachetag` | 17,110 | 14.90 MiB | 156 | 99.36 MiB | 210.68 MiB |
| `fellow-fanout-persistent-attach-100k` | 3/3 | fanout persistent `cachetag` | 15,928 | 14.90 MiB | 156 | 118.17 MiB | 255.23 MiB |

## Historical Fellow Full-Shape And Shutdown

The 2026-07-08 rerun separates benchmark results from VTC acceptance. `fellow-local-cost-1m` passed for the pre-FDO-direct path. The shutdown probes loaded every requested object and captured memory and load metrics without driver errors, but their VTCs failed during post-load teardown. Their numbers are useful samples, not clean benchmark passes. Use the 2026-07-10 B1 notes for current Fellow-direct claims.

| Matrix | Runs | Load RPS | Warm RPS | Tracked memory | Bytes/object | RSS | Cgroup peak | Caveat |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `fellow-local-cost-100k` | 3/3 | 194 | 37,629 | 37.59 MiB | 394 | n/a | n/a | anomalously slow load; use as diagnosis input |
| `fellow-local-cost-1m` | 3/3 | 18,928 | 43,478 | 352.26 MiB | 369 | 903.14 MiB | 1.52 GiB | clean 2026-07-08 rerun; IO-limited |
| `fellow-shutdown-5m` | 0/1 | 17,724 | n/a | 1.73 GiB | 372 | 4.36 GiB | 7.19 GiB | driver completed with zero errors; VTC failed after stats capture during teardown |
| `fellow-shutdown-10m` | 0/1 | 15,016 | n/a | 3.43 GiB | 369 | 8.64 GiB | 14.33 GiB | driver completed with zero errors; VTC failed after stats capture during teardown |

## Fellow FDO-Direct Restart

Use the `post_restart` and `post_first_touch` VMOD counters to assess the accepted 2026-07-10 FDO-direct restart results, rather than the old replay and hydration counters. On restart, cachetag doesn’t replay objects one at a time, read cachetag FDOs before traffic, or rebuild resident membership. On first touch, Fellow loads its usual disk-object metadata and cachetag reads the serialized envelope directly.

| Shape | Baseline post-restart obj/edges/rev | FDO-direct post-restart obj/edges/rev | FDO-direct after 10% touch obj/edges/rev | Object/index bytes before -> after |
| --- | ---: | ---: | ---: | --- |
| 100k-t5 | 100,000 / 500,000 / 4,000,000 | 0 / 0 / 0 | 0 / 0 / 0 | 2,300,792 + 9,830,400 -> 43,648 + 0 |
| 100k-t6 | 100,000 / 600,000 / 4,800,000 | 0 / 0 / 0 | 0 / 0 / 0 | 10,689,440 + 9,830,400 -> 43,648 + 0 |
| 100k-t20 | 100,000 / 2,000,000 / 16,000,000 | 0 / 0 / 0 | 0 / 0 / 0 | 10,689,440 + 9,830,400 -> 43,648 + 0 |
| 1m-t5 | 1,000,000 / 5,000,000 / 40,000,000 | 0 / 0 / 0 | 0 / 0 / 0 | 8,592,248 + 96,000,000 -> 43,648 + 0 |
| 1m-t6 | 1,000,000 / 6,000,000 / 48,000,000 | 0 / 0 / 0 | 0 / 0 / 0 | 75,701,152 + 96,000,000 -> 43,648 + 0 |
| 1m-t20 | 1,000,000 / 20,000,000 / 160,000,000 | 0 / 0 / 0 | 0 / 0 / 0 | 75,701,152 + 96,000,000 -> 43,648 + 0 |

The later 1% and 100% patched first-touch gates also passed for 100k and 1M t5/t6/t20. At both `post_restart` and `post_first_touch`, every row reported `mem_objects=0`, `mem_edges=0`, and `mem_reverse_bytes=0`. Attribute-read failures, invalid attributes, and store-invariant failures were all zero. See the `2026-07-10 FDO-direct B1 remote slice` baseline (`devdocs/benchmarks/baselines/2026-07-10-purgemap-fdo-direct-b1-remote-51.158.37.2.md`) for the full table.

## Eviction And Churn

The maintenance rows passed cleanly. Churn rows are useful memory-pressure signals because they create and retire many tag generations.

| Matrix | Runs | Lane | Load RPS | Tracked memory | RSS | Cgroup peak |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `eviction-100k` | 6/6 | `cachetag_eviction` | 59,904 | 17.38 MiB | 146.66 MiB | 258.95 MiB |
| `eviction-100k` | 6/6 | `noindex_load` | 78,984 | n/a | 109.86 MiB | 258.95 MiB |
| `eviction-1m` | 6/6 | `cachetag_eviction` | 59,656 | 143.58 MiB | 697.45 MiB | 1.13 GiB |
| `eviction-1m` | 6/6 | `noindex_load` | 79,445 | n/a | 901.00 MiB | 1.27 GiB |
| `churn-deterministic-full-100k` | 2/2 | `cachetag` | 69,942 | 63.05 MiB | 580.80 MiB | 804.11 MiB |
| `churn-deterministic-full-100k` | 2/2 | `xkey` | 66,370 | 171.68 MiB | 594.21 MiB | 821.30 MiB |
| `churn-deterministic-incremental-100k` | 2/2 | `cachetag` | 70,072 | 63.40 MiB | 362.62 MiB | 575.28 MiB |
| `churn-deterministic-incremental-100k` | 2/2 | `xkey` | 67,089 | 228.91 MiB | 755.30 MiB | 982.35 MiB |
| `churn-deterministic-incremental-100k` | 3/3 | `cachetag` | 71,452 | 25.08 MiB | 295.28 MiB | 588.60 MiB |
| `churn-deterministic-incremental-100k` | 3/3 | `xkey` | 67,688 | 228.91 MiB | 763.41 MiB | 989.40 MiB |

## Failed Or Partial Current Rows

These are the only failed or partial rows after the 2026-07-08 Buddy/Fellow rerun. Don’t replace them with older passing data without rerunning the benchmark.

| Matrix | Result | Likely limit | Failure signal / caveat |
| --- | --- | --- | --- |
| `sanity-10k` | 6/7 | IO limited | `cachetag_concurrent.run-1.time` failed; driver recorded zero HTTP errors; log only captured top-level VTC `FAILED exit=2` |
| `fellow-shutdown-5m` | 0/1 | IO limited | driver loaded all 5M objects with zero errors and stats were captured; top-level VTC failed after data capture during post-load teardown |
| `fellow-shutdown-10m` | 0/1 | IO limited | driver loaded all 10M objects with zero errors and stats were captured; top-level VTC failed after data capture during post-load teardown |
