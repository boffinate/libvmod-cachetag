# BR-006: jemalloc decay purging causes post-mass-free tails and RSS cliffs

**Rule:** Before attributing a wall-clock latency tail to VMOD or Vinyl code,
check for the allocator decay-purge signature: the tail cycle follows a mass
free, its maximum is far above p99 with p99 unaffected, worker RSS drops by
gigabytes in the same cycle, and there is no PSI, no VMOD counter movement, and
nothing on-CPU.

**Why:** A full day (2026-07-14) was spent chasing a reproducible ~37-44 ms
Default-storage cycle-6 tail as a suspected cachetag defect. It was jemalloc
dirty-page decay purging during the post-pressure mass free: disabling decay
removed it, a no-index control reproduced it without cachetag, and Buddy (no
per-object heap frees) never showed it
(`devdocs/docs/archived/20260715_0835_report_phase6-allocator-probes-remote.md`).

Related caveats: purging can spill past the tail cycle (the no-index control
showed a 15.5 ms cycle-7 max) because the ~10 s dirty decay outlasts the 6 s
quiet window, so cycle-endpoint RSS may be captured before purging completes;
`background_thread:true` alone does not remove the tail.

**Comply by:** Running the falsification probes before touching code: an
allocator-knob row (`MALLOC_CONF=dirty_decay_ms:-1,muzzy_decay_ms:-1`) and a
no-index ownership control. Do not infer jemalloc active/retained values from
libc RSS.

**Tripwire:** Implemented — `summarize_results.py` warns `[BR-006]` when a Phase 6
cycle's max is >=10x its p99, >=10 ms, and coincides with a >=1 GiB worker RSS
drop from the previous cycle endpoint.
