# BR-005: vinyltest children run a non-production jemalloc config

**Rule:** Do not extrapolate absolute latency or RSS figures from vinyltest-run
benchmarks to production without noting that vinyltest sets
`MALLOC_CONF=abort:true,junk:true` for child processes; any production allocator
recommendation must be sanity-checked with a non-junk launch.

**Why:** `junk:true` memsets every freed allocation (debug feature), inflating
mass-free costs and dirtying pages in every benchmark arm. Discovered during the
2026-07-15 allocator probes: the effective config recorded on the no-purge row
was `abort:true,junk:true,dirty_decay_ms:-1,muzzy_decay_ms:-1`. Comparisons
*between* arms remain valid because the base is uniform.

**Comply by:** Treating cross-arm deltas as the primary signal; labelling absolute
numbers as vinyltest-config figures; rerunning outside vinyltest before shipping
an allocator tuning recommendation.

**Tripwire:** Implemented — `summarize_results.py` warns `[BR-005]` when a Phase 6
row's captured allocator environment contains `junk:true`, and notes the inferred
default when the capture is empty.
