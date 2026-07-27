# BR-022: Known Fellow failure classes are not new regressions

**Rule:** Before diagnosing a Fellow-lane failure as a new cachetag or Fellow
regression, check it against the known classes: the `cache_main.c:388` big-quit
is post-failure shutdown/reset pollution (only diagnose the 1M incremental-churn
timeout as shutdown if an artifact shows `driver_errors=0` and full churn
completion first); the 5M load-time "Worker Pool Queue does not move" watchdog
and the Fellow close-hang are separate failures, neither evidence against the
close-time fix; and `fellow_persistent_purgemap_resident_zero` barrier-skips are
expected on the FDO-direct path — zero post-restart VMOD membership is the
designed state, not data loss.

**Why:** Each class has already consumed a diagnosis cycle
(`devdocs/docs/archived/2026-07-10-fellow-purgemap-1m-churn-timeout-briefing.md`,
`devdocs/docs/archived/fellow-xkey-memory-profiling-plan-2026-06-25.md`).

**Comply by:** Matching the failure signature against this list and the Fellow
briefing docs before opening a new investigation.

**Tripwire:** None — diagnosis-time rule.
