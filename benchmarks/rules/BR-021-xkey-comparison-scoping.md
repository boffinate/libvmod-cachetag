# BR-021: Scope xkey comparisons precisely

**Rule:** Scope every comparison claim to `xkey` (never `ykey`); never use xkey
as a Fellow restart/persistence gate (it has no resurrection model — the valid
comparison is in-memory xkey vs cachetag+Fellow); derive xkey per-edge memory by
dividing its aggregate byte counters by the expected relation count (it exposes
no edge counters); and report xkey's post-purge `g_bytes=0` separately from
cachetag's retained/reusable structures — different lifecycle strategies, not
identical memory states.

**Why:** Each of these misreadings appeared during the memory-profiling
campaigns (`devdocs/docs/archived/benchmarking-plan.md`,
`devdocs/docs/archived/fellow-xkey-memory-profiling-plan-2026-06-25.md`,
`devdocs/docs/archived/2026-07-08-purgemap-cutover-benchmark-plan.md`).

**Comply by:** Writing comparison sentences that name the baseline and the
lifecycle difference; checking tag-shape driver output before claiming a
tags-per-object shape.

**Tripwire:** None — claim-writing rule.
