# BR-023: Judge pause/tail budgets from raw samples against a frozen budget

**Rule:** Judge latency-tail acceptance from raw sample files, not only
precomputed percentiles; judge against the owner-ratified budget ("no
VMOD-attributed request sample above 15 ms" as of Phase 4), and keep the two
verdicts separate: a wall-clock tail proven external to the VMOD (host noise,
allocator decay — see BR-006) does not fail the VMOD gate but must still be
characterized in the record. Acceptance thresholds are frozen and dated;
later confirmation rows confirm or reject them and never silently move the bar.

**Why:** Percentile-only reads hid the shape of the Phase 4 tails; the 190 ms
refill convoy and the jemalloc cycle-6 tail both required raw-sample and
attribution analysis to classify correctly
(`devdocs/docs/archived/20260714_0928_note_phase4-low-water-rearm-remote.md`,
`devdocs/docs/archived/20260715_0835_report_phase6-allocator-probes-remote.md`).

**Comply by:** Citing the budget by name in every gate judgment; using the
`latency_samples.tsv`/attribution artifacts for tail claims; recording
threshold changes as explicit dated decisions.

**Tripwire:** None — judgment rule; the attribution artifacts are the evidence
substrate.
