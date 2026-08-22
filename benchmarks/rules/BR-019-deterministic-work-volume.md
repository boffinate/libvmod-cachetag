# BR-019: Attribution requires a deterministic work volume

**Rule:** Do not attribute a CPU/RSS delta to a code change unless the benchmark
performs the same work every run: generation-scoped URLs, all-miss loads by
construction, a quiescence barrier, a deterministic compact path, and a
fail-loud `backendObjects == objects` assertion. Never mix deterministic-lane
figures with legacy noise-lane figures (`rotating-tag-churn` is a soak lane
only).

**Why:** Before the 2026-07-04 determinism rework, same-code variance was
±14–18% CPU and an instrumentation-only change "measured" −18.6% CPU / −21.5%
RSS — larger than the −4%/+31% deltas actually under judgment. TTL races and the
expiry-thread-dependent compact path meant each run did different work.

**Comply by:** Using the deterministic churn lanes for attribution; checking the
per-cycle backend-object assertions passed; quoting legacy-lane figures only as
history, never against new-lane numbers.

**Tripwire:** Implemented — deterministic lanes assert per-cycle
`backendObjects == objects` and fail loud on drift.
