# BR-013: Whole-tree CPU numbers include the driver; attribute before claiming

**Rule:** Do not attribute `.time` CPU seconds or inherited hardware counters to
the VMOD: `run_with_metrics.py` measures the whole `vinyltest` tree (vinyld +
driver + backend + shell). `PERF_MODE` records inherited counters, not call
stacks — use the opt-in `perf record` wrapper for hot-path attribution, and
validate off-CPU tooling (e.g. `offcputime-bpfcc`) on the host before treating
its output as load-bearing.

**Why:** Driver work is a large share of tree CPU on load-heavy profiles
(`devdocs/docs/archived/benchmarking-plan.md`); the Phase 4 host `offcputime-bpfcc`
attempt produced empty output, and tiny load windows (`OBJECTS=100`, ~21 ms)
produced no usable perf samples
(`devdocs/docs/archived/cachetag-cold-load-differential-profiling-plan-2026-06-23.md`).

**Comply by:** Citing per-process/cgroup attribution (tracked `vinyld` fields,
`perf record` on the target process) for VMOD cost claims; smoke-testing any
off-CPU tool on the host before a load-bearing run.

**Tripwire:** None.
