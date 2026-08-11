# Set-interning fixed-work CPU-cost decision round

Rules reviewed: BR-001 through BR-026; applicable: BR-002, BR-008, BR-011, BR-013, BR-014, BR-016 through BR-020, and BR-023 through BR-026.

**Question:** Excluding retained-memory differences, how much load-phase compute does the current complete-membership-set interning implementation add relative to the direct-vector build for one shared five-tag set and one million unique five-tag sets?

**Host:** the rebuilt 2026-08-11 `ubuntu@51.159.202.218` cohort.

**Source:** one frozen current source and harness identity containing implementation `1680f6f`; the only intended B/P build difference is `--disable-set-interning` versus `--enable-set-interning`.

## Why existing evidence is insufficient

The old uninstrumented screen found enabled-minus-disabled achieved-rate differences of about -0.53% shared and -0.11% unique, both within the approximately 0.92% same-code drift. The later lock timers measured only enabled-path mutex-held components in the superseded implementation and omitted pre-lock sorting and hashing. Whole-command CPU and inherited hardware counters include the driver, backend, startup, purge, and teardown, so BR-013 prohibits attributing them to the cache process. The current post-hardening screen compared old enabled with new enabled, not disabled with current enabled.

These artifacts support “no detectable large regression” and a rough sub-1% end-to-end inference, but not a defensible current CPU-cost number.

## Required harness seam

Add required load-phase `perf stat` measurement targeting the uniquely identified `cache-main`/`vinyld` descendant and all of its threads. Use the deployed perf control protocol, which supports `-D -1` and `--control=fifo:CONTROL,ACK`:

1. The driver writes `load.start` and waits without issuing requests.
2. The controller resolves exactly one target, records PID, executable, and start-time ticks, launches counters disabled, sends `enable`, and waits for perf's `ack`.
3. Only after the acknowledgement does the controller write `load.ready`.
4. The driver performs the exact load, waits for pending work to reach zero, and writes `load.end`.
5. The controller sends `disable`, waits for acknowledgement, stops perf, and revalidates target identity.

Required mode fails on unsupported perf/control, missing or multiple targets, identity change, marker timeout/order failure, counter failure, empty/unparseable output, request-count mismatch, process failure, swap, or invalid workload provenance. Ordinary benchmark and existing `perf record` behaviour remain unchanged.

Retain raw per-run stat output and a manifest containing the phase, event set, perf version, command, target identity, marker/ack timestamps, return codes, and workload request evidence. The fixed event set is `task-clock`, `instructions`, `cycles`, and `ref-cycles`. `BENCH_INSTRUMENT_OBJ_MTX=0`.

Verify the new seam only through the documented Docker harness. Tests must prove that no request begins before `ready` and cover stale markers, missing/multiple target, perf attach failure, acknowledgement timeout, missing end, and target identity change.

## Frozen remote evidence

Use Default storage, eight clients, one million all-miss objects, five tags/object, xkey and no-index disabled, warm traffic disabled, no purge inside the measured load interval, and the two generated profiles `cutover-mostly-shared` and `cutover-mostly-unique`. Require exactly 1,000,000 requests and backend objects, 1,000,000 published objects, 5,000,000 edges, zero driver/attach/parse/limit errors, zero LRU nukes, zero swap, and quiescence.

Run fresh-build rows sequentially and interleaved:

| Row | Set interning | Repetitions/profile |
| --- | --- | ---: |
| B1 | disabled | 3 |
| P1 | enabled | 3 |
| B2 | disabled | 3 |
| P2 | enabled | 3 |

Stop after any build, provenance, required-stat, workload, checksum, swap, or validity failure. Do not add concurrency calibration, no-index lanes, mutex instrumentation, replacement rows, or profiling rows.

## Judgment

Primary metric: retired `vinyld` instructions per successful published object during the acknowledged load window. Secondary metrics: `vinyld` task-clock microseconds, cycles, and reference cycles per object. Driver load wall time is contextual only.

Report each repetition and per-row spread. Use B1/B2 and P1/P2 to establish the same-code floor. Claim an overhead only if its sign reproduces in both interleaved B/P comparisons and its magnitude exceeds roughly twice the relevant same-code variance under BR-024. Otherwise report the experiment's detection bound, not zero overhead.
