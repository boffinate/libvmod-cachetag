# BR-001: Docker cgroup `memory.peak` is a lifetime high-water mark

**Rule:** Do not cite cgroup `memory.peak` (or the summarizer's `cgroup_peak_bytes`)
as runtime cache memory evidence when the measured container also built Vinyl,
Slash, or this VMOD.

**Why:** A benchmark invocation without `SKIP_BUILD=1` uses one container for the
build and the VTC run, so the peak is usually compiler/linker activity, not the
cache workload. A 2026-07-12 Buddy attribution wrongly suggested a storage-engine
regression until the peak was traced to the build phase.

**Comply by:** Using same-host `SKIP_BUILD=1` rows for RAM comparisons; judging
runtime memory from `memory.current`, tracked `vinyld` RSS/PSS, `smaps_rollup`,
VMOD counters, and `memory.stat` in the JSONL sampler. Build-inclusive peaks may
be cited only as total workspace/capacity checks, labelled build-inclusive.

**Tripwire:** Partial — the summarizer prints `cgroup_peak_bytes` with a caveat in
its docs but does not yet label build-inclusive rows automatically.
