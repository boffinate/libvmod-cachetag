# BR-024: Establish a same-code noise floor before judging any delta

**Rule:** Before attributing a performance delta to a code change, know the
same-code (A/A) variance of that benchmark shape on that host — from repeated
identical rows or an explicit A/A pair — and treat any delta smaller than
roughly twice that variance as noise, not a finding. Report spread (min/max or
percentiles across repetitions), never a bare median of few runs.

**Why:** General benchmarking practice, adopted after this project measured the
cost of ignoring it: pre-determinism churn rows showed ±14–18% same-code CPU
variance, and an instrumentation-only change "measured" −18.6% CPU — larger
than the real deltas under judgment
(`devdocs/docs/archived/churn-benchmark-determinism-plan-2026-07-04.md`). BR-019 fixed
the workload-side variance; this rule covers the residual host/system variance
that determinism cannot remove.

**Comply by:** Running `RUNS=3` minimum for any judged row; when a campaign
introduces a new shape/host/scale, running one A/A pair first and recording the
observed floor in the campaign note; phrasing conclusions as "delta X against a
same-code floor of Y".

**Tripwire:** None — design/judgment rule; repeated-row spread is visible in the
summarizer's per-workload medians and wall-second distribution.
