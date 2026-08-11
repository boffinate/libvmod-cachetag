# Set-interning remote development screen report

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (all cgroup peaks are build-inclusive and excluded from runtime-memory evidence), BR-002 (executed B1, P1, B2, P2), BR-003 (retained-state counters read from final post snapshots through the summarizer), BR-004 (generated workload flushes before snapshots), BR-008 (one sequential remote session; execution was delegated at the owner's explicit request), BR-009 (generated VTCs), BR-011 (all rows are under-saturated and therefore counter/memory evidence only), BR-014 (one AMD EPYC 4345P host and kernel cohort), BR-016 (fresh build at every arm switch), BR-017 (one harness commit), BR-018 (no failed or replaced row), BR-019 (validated 100k-object deterministic tag shapes), BR-020 (2 GiB Default storage; no residency claim was required for this attach-only matrix), BR-023 (no tail claim), BR-024 (two interleaved direct-vector samples), BR-025 (governor and boost state captured), and BR-026 (completed within the four-row development-screen budget).

**Campaign class:** Development screen.

**Host:** `ubuntu@51.159.202.218`, AMD EPYC 4345P 8-Core Processor, 16 logical CPUs, 60.4 GiB RAM, Linux 7.0.0-15-generic.

**Harness commit:** `a12a849 Add set-interning benchmark arms` on `feature/set-interning`.

**Scope:** Default storage only; `local-cost-attach-100k`, `cutover-mostly-shared,cutover-mostly-unique`, five tags per object, three repetitions per workload, xkey disabled, and the wrapper's no-index control present. The remote setup and all four frozen rows succeeded: B1 (disabled), P1 (enabled), B2 (disabled), P2 (enabled). Each result has nine valid processes and zero failures: three repetitions for each cachetag profile plus three no-index control repetitions.

## Result

Set interning behaves as intended at the two exact-set endpoints.

- `cutover-mostly-shared`: the enabled arm has one interned set, 100,000 set references, 99,999 hits, one miss, 72 B set storage, and 512 B table storage. The direct-vector arm has zero-valued interning counters. The final tracked index value is 5.13 MiB enabled versus 8.94 MiB disabled, an approximately 3.81 MiB (43%) reduction at this 100k-object shape.
- `cutover-mostly-unique`: the enabled arm has 100,000 interned sets and misses, zero hits, 6.87 MiB set storage, and 1.00 MiB table storage. The direct-vector arm again has zero-valued interning counters. The final tracked index value is 12.99 MiB enabled versus 8.94 MiB disabled, an approximately 4.05 MiB (45%) increase.
- B1 and B2 agree at the displayed 8.94 MiB tracked index value for both profiles; P1 and P2 agree at 5.13 MiB for the shared endpoint and 12.99 MiB for the unique endpoint. This is a stable counter result across the interleaved samples.

Every row is classified `harness/under-saturated` (average maximum CPU about 18.5–18.7%, maximum single core about 60%). Under BR-011, load rates, wall time, and CPU values are excluded from any performance conclusion. The cgroup `memory.peak` values are also excluded because every row included a fresh Docker build (BR-001). The VSC-derived tracked index and interning counters are the valid evidence for this screen.

The host metadata recorded `powersave` as the CPU governor and `cpufreq:1` boost state for every row. Current frequency varied during the session; that does not change the counter result, and no CPU-cost claim is made.

## Artifacts

- [B1 archive](../benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/B1/cachetag-benchmark-results-remote-20260810T153858Z-local-cost-attach-100k.tgz)
- [P1 archive](../benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/P1/cachetag-benchmark-results-remote-20260810T154024Z-local-cost-attach-100k.tgz)
- [B2 archive](../benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/B2/cachetag-benchmark-results-remote-20260810T154142Z-local-cost-attach-100k.tgz)
- [P2 archive](../benchmarks/remote-results/20260810_51.159.202.218/set-interning-screen/P2/cachetag-benchmark-results-remote-20260810T154301Z-local-cost-attach-100k.tgz)

All four downloaded SHA-256 digests were checked locally before reading the artifacts. `benchmarks/summarize_results.py` was run inside `vinyl-cache-ubuntu-build` against all four archives.

## Decision and next step

The implementation is worth developing further for repetitive complete membership sets: its memory saving is direct and large at the shared endpoint. It is not a default-on candidate for unique-set workloads: the interned header and table add a material memory cost exactly as expected.

Do not use this screen to decide CPU, latency, or general workload benefit. The next decision round should add CMS-weighted intermediate exact-set-reuse shapes such as stable-set regeneration and listing-set mutation, then run a saturated remote comparison with a deliberately chosen pacing model and the same interleaved A/A protocol.
