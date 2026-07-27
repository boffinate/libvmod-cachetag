# BR-012: Local macOS Docker/OrbStack rows are smoke and counter evidence only

**Rule:** Never publish final wall-time, CPU, or RAM claims from local macOS
Docker/OrbStack runs; use them for correctness, VSC counters, and harness
validation only.

**Why:** macOS virtualization and OrbStack filesystem noise distort wall time,
hardware perf counters are blocked (`perf_event_status=unavailable`), and local
ARM64 allocator/RSS behavior differs from the Linux benchmark hosts
(`devdocs/docs/archived/benchmarking-plan.md`, `benchmarks/README.md` Large Runs).

**Comply by:** Running performance rows on the remote Linux host via
`scripts/remote-benchmark.sh`; when a local row must be cited, labelling it
local-smoke.

**Tripwire:** Partial — the harness records `perf_event_status`/`perf_event_error`
so blocked-counter rows are identifiable.
