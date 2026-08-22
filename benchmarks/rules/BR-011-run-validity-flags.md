# BR-011: Respect the summarizer's run-validity flags

**Rule:** A row with `swap_activity=1`, a low-CPU/low-memory-pressure warning, or
`likely_limit: harness/under-saturated` is correctness/counter evidence only —
never quote its wall time, CPU, or throughput as a performance result. If
no-index throughput roughly equals tag/xkey throughput, the run shape is
driver-bound and indistinguishable, not evidence of equal cost. A single
under-saturated row's CPU asymmetry with no supporting counter is scheduling
noise, not a cost.

**Why:** Phase 4 local smokes repeatedly swapped and were downgraded; a Phase 5
cap row showed control 37.11% vs held 20.85% CPU — opposite the expected
direction, n=1, no counter support.

**Comply by:** Checking the summarizer's `Swap:`, `Likely limit:` and warning
lines before quoting any number from a row; requiring per-process CPU or driver
saturation evidence before reading equal-throughput arms as equal-cost.

**Tripwire:** Implemented — `run_with_metrics.py` sets `swap_activity` and writes
`SWAP_DETECTED`; `summarize_results.py` warns on low pressure and classifies
`harness/under-saturated`.
