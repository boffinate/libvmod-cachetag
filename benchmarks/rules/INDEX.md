# Benchmark interpretation rules

One rule per file; each records the incident that motivated it and its tripwire
status. These exist because interpretation mistakes here have each cost half a
day or more — read this index before designing or interpreting any benchmark.

**Checklist convention:** every campaign note and report opens with a line
`Rules reviewed: BR-001..BR-026; applicable: BR-xxx (complied), ...` citing the
rules that constrain that campaign. A rule that is applicable but not complied
with must say why.

This checklist applies to new campaign plans, reports, and fresh benchmark interpretations. It does not apply to verbatim archival copies, file moves or renames, or reference notes that preserve an interpretation already completed and documented. Those requests should not trigger a new rules review, evidence analysis, or verification run unless the user explicitly asks for one.

**Campaign scope convention:** before remote execution, every campaign plan records its class (`development screen`, `decision round`, or `release acceptance`), the claims being tested, the minimum row-to-claim mapping, the maximum row/repetition budget, the expected wall-clock duration, and the stop condition. Benchmark rules are conditional constraints, not a cumulative checklist. Passing the planned claims ends the campaign; adding a lane, arm, scale, diagnostic rerun, or acceptance phase beyond the frozen budget requires owner approval. One exact replacement for the campaign's only planned repetition that is ineligible for its required judged scope for a BR-018-eligible reason is a slot-for-slot replacement, not a budget expansion. See [BR-018](BR-018-measurement-voids-fail-loud.md) and [BR-026](BR-026-campaign-scope-and-evidence-budget.md).

**Incident policy:** any benchmark misinterpretation that costs more than half a
day must produce a new rule here, a harness/summarizer tripwire, or both. Cap
the list around 25 rules — consolidate before extending past it.

**Lane lifecycle policy:** the benchmark matrix is tiered — the `regression`
group is the standing gate; scale/pre-release and Fellow-backlog lanes run when
warranted. A matrix lane added for a campaign is a *campaign lane*: when its
campaign or phase closes, it is either promoted into the `regression` group
(usually replacing a lane) or deleted from the matrix in the same change that
records the decision. Deleted lanes stay reproducible through the harness
commit recorded in each archived artifact — never keep a dead lane at HEAD
"just in case". This is what keeps the matrix from ratcheting up with every
feature.

## Validity of a run

- [BR-011](BR-011-run-validity-flags.md) — swap, under-saturation, and driver-bound rows are correctness evidence only
- [BR-012](BR-012-local-macos-rows-smoke-only.md) — local macOS Docker/OrbStack rows are smoke/counter evidence only
- [BR-016](BR-016-build-cache-contamination.md) — never reuse a build cache across an arm or storage-kind change
- [BR-017](BR-017-harness-identity-invalidates-rows.md) — a harness change invalidates earlier rows for the changed path
- [BR-018](BR-018-measurement-voids-fail-loud.md) — exclude overall-invalid repetitions; allow one exact slot replacement for the campaign's only eligible invalid repetition, retaining scoped reasons and the rejection
- [BR-020](BR-020-storage-envelope-and-residency.md) — storage envelope limits and residency validity

## Campaign design

- [BR-002](BR-002-interleave-campaign-rows.md) — interleave baseline/patched rows (B,P,B,P)
- [BR-008](BR-008-benchmark-host-sequential-only.md) — benchmark host runs one row at a time; no subagents
- [BR-009](BR-009-debug-none-vtc-mode-throttle.md) — benchmark VTCs must set `-p debug=none`
- [BR-014](BR-014-hardware-cohorts.md) — never compare across hardware cohorts or host rebuilds
- [BR-019](BR-019-deterministic-work-volume.md) — attribution requires a deterministic work volume
- [BR-024](BR-024-same-code-noise-floor.md) — establish a repeated same-code noise floor and complete metric-specific coverage before judging deltas
- [BR-025](BR-025-cpu-frequency-and-power-state.md) — record CPU governor/boost state; drift is a cohort change
- [BR-026](BR-026-campaign-scope-and-evidence-budget.md) — freeze how much evidence is enough before remote execution; do not silently promote a screen into an acceptance campaign

## Memory interpretation

- [BR-001](BR-001-build-inclusive-memory-peak.md) — cgroup `memory.peak` is a lifetime high-water mark
- [BR-005](BR-005-vinyltest-nonproduction-allocator.md) — vinyltest children run non-production `abort:true,junk:true`
- [BR-006](BR-006-allocator-decay-purge-signature.md) — jemalloc decay purging: post-mass-free tails and RSS cliffs
- [BR-007](BR-007-memory-capture-pid-provenance.md) — memory captures must prove which process they measured
- [BR-015](BR-015-containment-needs-arena-split.md) — containment claims need an arena/heap smaps decomposition

## Counter and snapshot semantics

- [BR-003](BR-003-final-post-snapshot-for-convergence.md) — judge convergence only from the final `post` snapshot
- [BR-004](BR-004-vsc-counters-update-on-vmod-calls.md) — VSC counters are published on a cadence plus read probes, not on every VMOD call; gauges are point-in-time

## Latency and CPU judgment

- [BR-013](BR-013-cpu-attribution-scope.md) — whole-tree CPU includes the driver; attribute before claiming
- [BR-023](BR-023-tail-budget-judgment.md) — judge tails from raw samples against the frozen owner budget

## Comparison scoping and known failure classes

- [BR-021](BR-021-xkey-comparison-scoping.md) — scope xkey comparisons precisely
- [BR-022](BR-022-fellow-failure-classes.md) — known Fellow failure classes are not new regressions

## Harness mechanics

- [BR-010](BR-010-failure-log-capture-is-head.md) — failure log capture is a head, not a tail; finite VTC log buffer

## Watch list (general-knowledge candidates, no project incident yet)

Not promoted to rules — the cap exists so the list stays readable. Promote one
only when it bites or a campaign makes it load-bearing.

- **Coordinated omission**: closed-loop load generators under-record latency
  during server stalls. Partially handled — the Phase 4 pacer skips missed
  slots without a catch-up burst and max-based gates are robust to it — but any
  new driver lane that reports percentiles should state its pacing model.
- **Transparent huge pages**: khugepaged collapse/compaction can stall
  allocation-heavy phases and inflate RSS attribution; the Phase 6 fault capture
  already records `thp_*` cgroup fields if this ever needs checking.
- **Loopback ephemeral-port/TIME_WAIT exhaustion**: very long high-RPS runs with
  keepalives disabled can distort tails for network reasons; the driver reuses
  connections by default, so this only matters for `BENCH_HTTP_DISABLE_KEEPALIVES=1`
  shapes.
- **Code-layout/ASLR sensitivity**: link order, binary layout, and environment
  size can move hot-loop performance a few percent with zero source change
  (Mytkowicz et al.); relevant if a small unexplained same-code delta ever
  survives BR-024's noise floor.
