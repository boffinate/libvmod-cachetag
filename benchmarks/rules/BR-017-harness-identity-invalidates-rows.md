# BR-017: A harness change invalidates earlier rows for the changed path

**Rule:** When the harness, driver, or measured-path instrumentation changes
mid-campaign, supersede earlier rows for that path rather than citing them; and
never relabel artifacts collected for one purpose (e.g. calibration) as
acceptance evidence for a different candidate.

**Why:** The request-epoch-lease and seal-publication change invalidated
otherwise-valid Stage B rows; the first 10M pair was rejected because harness
identity changed.
The Phase 10 decision record likewise forbids citing pre-change artifacts as
final evidence for a changed path.

**Comply by:** Recording the harness commit in each campaign note; when a
harness change is additive-only (new counters, no measured-path change), saying
so explicitly instead of silently keeping old rows.

**Tripwire:** Partial — remote artifacts record the harness commit; the
cross-row comparison is manual.
