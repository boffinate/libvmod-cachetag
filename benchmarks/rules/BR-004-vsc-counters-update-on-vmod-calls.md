# BR-004: `CACHETAG.*` VSC counters are published on a cadence, not on every call

**Rule:** `CACHETAG.*` counters are published by a per-namespace background
thread once per `vsc_publish_interval` (default 0.1 s), and synchronously by the
read probes `objects()`, `pending()`, `edges()` and `compact()`, by VCL warm, and
by VCL cold. Mutating calls (`stale()`, `add()`, `purge()`, insert and expiry) do
not publish. Do not assert an exact counter value after a mutating call without
either a read probe or the synchronous test policy
(`CACHE_TAG_TEST_VSC_PUBLISH_SYNC=1`, set for the diagnostic suite). Never assert
a transient (`sweep_last_*`, `sweep_remaining`, `reclaim_pending`,
`publication_phase`) without a synchronous flush; the thread may sample either
side of the window.

**Why:** VSC values used to update only on VMOD calls. The `pm00018` VTC waited on
`sweep_remaining==2` mid-pass and failed because the counter is not externally
observable during a synchronous compact
(`devdocs/docs/archived/20260713_0816_diagnostic_phase4_sweep_latency.md`); a
`vinyl -expect` after `delay` read a stale snapshot during Phase 4/6 work.
Related: point-in-time gauges (e.g. `reclaim_pending`) can legitimately go
non-zero between the flush probe and the `vinylstat` read — judge teardown from
reader counts, acquire/release balance, and exact-retirement gates, not a lone
gauge (`devdocs/docs/archived/20260714_1355_note_phase5-held-publication.md`).

This rule was rewritten on 2026-08-20 when the publish policy changed from
publishing on every VMOD call to the background-thread-plus-read-probe policy
described above; the incidents above are the history that motivated it and still
apply.

**Comply by:** a read probe before every single-shot `vinylstat -1` read;
`vinyl -expect` may rely on its retry loop only for eventually-stable values.

**Tripwire:** the generator emits a flush client before every stats capture
(stage 0); hand-written VTCs remain a review item.
