# BR-004: `CACHETAG.*` VSC counters are not background-published

**Rule:** Do not read a `CACHETAG.*` counter with `vinyl -expect` (or a stats
snapshot) after a VTC `delay` without first calling a VMOD entry point; the
snapshot is stale until a VMOD call flushes it.

**Why:** VSC values update only on VMOD calls. The `pm00018` VTC waited on
`sweep_remaining==2` mid-pass and failed because the counter is not externally
observable during a synchronous compact
(`devdocs/docs/archived/20260713_0816_diagnostic_phase4_sweep_latency.md`); a
`vinyl -expect` after `delay` read a stale snapshot during Phase 4/6 work.
Related: point-in-time gauges (e.g. `reclaim_pending`) can legitimately go
non-zero between the flush probe and the `vinylstat` read — judge teardown from
reader counts, acquire/release balance, and exact-retirement gates, not a lone
gauge (`devdocs/docs/archived/20260714_1355_note_phase5-held-publication.md`).

**Comply by:** Calling a cheap VMOD entry point (for example
`namespace.objects()`) between the `delay` and the expectation; never asserting
on a mid-synchronous-call counter value.

**Tripwire:** Partial — generated Phase 5/6 workloads issue a flush probe before
post snapshots; hand-written VTCs remain a review item.
