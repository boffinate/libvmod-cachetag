# BR-003: Judge retained-floor convergence only from the final `post` snapshot

**Rule:** Read retained-index-floor and low-water convergence claims
(`resize_active_bytes`, `index_memory_bytes`, `resize_low_water_rearms`) from the
final `_post.run-N.stats` teardown snapshot, never from `phase4_post_*` row keys
or by grepping `_phase4_post.run-N.stats`.

**Why:** The `phase4_post` snapshot is taken at the end of the post measurement
window while traffic can still be deferring the designed low-water shrink, so it
reports pre-convergence table sizes. A P1m row on 2026-07-14 read 96 MiB there
against a true converged 16 MiB.

**Comply by:** Quoting `summarize_results.py`'s `Phase 4 resize VSC` and
`resize events` lines, which use the final teardown snapshot; more generally,
judging any convergence claim from a snapshot at least one full
rearm-plus-observe cycle after traffic stops.

**Tripwire:** Implemented in substance — the summarizer's convergence lines are
sourced from the final `post` snapshot, so using the summarizer complies.
