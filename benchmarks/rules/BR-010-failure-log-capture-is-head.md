# BR-010: Harness failure log capture is a head, not a tail

**Rule:** When diagnosing a failing VTC whose interesting output (for example a
failing `EXPECT`) appears late in the log, raise
`CACHE_TAG_FAILURE_LOG_LINES` (for example to `500000`) before rerunning;
default capture truncates from the front and can cut off the failure itself.

**Why:** A failing EXPECT was truncated out of a captured log during cachetag
harness debugging, costing a rerun cycle to see the actual assertion. Related:
`vinyltest` has a finite log buffer (`VTC_LOG_BYTES`) — an oversized VTC hits
`vtc_log.c` "vtclog_left > l" / "Too many digits for real", which is a harness
limit, not a cachetag result (the monolithic `pm00026` ten-cycle test was
rejected for this).

**Comply by:** Setting `CACHE_TAG_FAILURE_LOG_LINES=500000` for failure
reproduction runs; checking the end of a captured log is present before
interpreting an absence as evidence.

**Tripwire:** None.
