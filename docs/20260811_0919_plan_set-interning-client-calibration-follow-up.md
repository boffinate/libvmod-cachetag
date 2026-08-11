# Set-interning client-calibration follow-up

Rules reviewed: BR-001 through BR-026; applicable: BR-002, BR-008, BR-011, BR-014, BR-016 through BR-020, and BR-023 through BR-026.

**Status:** Stopped before execution. A lower-client search could locate the achieved-rate peak but cannot solve the observed offered-load/harness under-saturation: yesterday's move from 24 to 48 clients reduced both rate and average CPU. The actual decision is incremental fixed-work compute cost with set interning disabled versus enabled, which does not require whole-host saturation. No remote row from this plan was run.

**Host:** the rebuilt 2026-08-11 `ubuntu@51.159.202.218` cohort.

**Sources:** baseline `a12a849`; patched implementation `1680f6f`.

**Reason for follow-up:** the first post-change campaign fixed 24 clients without rechecking the earlier client curve. Yesterday's same-class host calibration was valid but under-saturated at both 24 and 48 clients; 24 achieved 89,565.59 requests/s at 66.39% average CPU, while 48 fell to 76,891.16 requests/s at 63.87% average CPU. A direct jump to 48 is therefore unsupported.

## Calibration

Use the patched source, set interning enabled, Default storage, five tags/object, the `noindex_load` filter, xkey disabled, one repetition, and no mutex instrumentation. Run sequentially at 24, 32, and 36 clients. C24 is a fresh source-synced build; later calibration points may reuse that exact provenance-checked build.

If 32 and 36 both trail 24 while remaining valid, add 28 to locate the rollover between 24 and 32. If rate is still rising at 36 and the row remains under-saturated, add 40. Continue in four-client increments only while achieved rate rises, stopping at the first clear rollover or 48 clients. Stop immediately for invalidity, process failure, checksum failure, or swap.

A row is saturation-eligible only if the summarizer accepts all required scopes and does not classify it `harness/under-saturated`. Choose the smallest saturated client count within 2% of the highest saturated achieved rate. If no point is saturation-eligible, choose the highest-rate valid point for one final diagnostic B/P screen, but do not make a throughput, CPU, or latency claim.

## Comparison

At the selected client count run fresh-build B1, P1, B2, P2 rows sequentially and interleaved. Each row uses `local-cost-attach-1m`, `cutover-mostly-shared,cutover-mostly-unique`, three repetitions/profile, Default storage, five tags/object, set interning enabled, xkey/no-index comparison arms disabled, and no mutex instrumentation.

Retain the existing 24-client artifacts as diagnostic evidence; do not rerun them as comparison rows unless 24 is selected by the calibration. Stop after any invalidity, process failure, checksum failure, or swap. No replacement or extra profiling row is authorized.

Fetch under `benchmarks/remote-results/20260811_51.159.202.218/set-interning-client-calibration/` and summarize only inside `vinyl-cache-ubuntu-build`.
