# BR-009: Benchmark VTCs must start Vinyl with `-p debug=none`

**Rule:** Never benchmark through a `vinyltest`-started Vinyl without
`-p debug=none`; `vinyltest` otherwise enables `debug=+vtc_mode`, which
throttles backend fetch throughput enough to dominate results.

**Why:** `vtc_mode` is designed for regression-test determinism, not load. Early
benchmark rows measured the throttle, not the VMOD.

**Comply by:** Using the generated benchmark VTCs, which set `-p debug=none`
explicitly; checking the `vinyl` startup arguments in any hand-written benchmark
VTC.

**Tripwire:** Implemented in substance — `generate_cachetag_benchmark_vtc.py`
emits `-p debug=none` in every generated workload; hand-written VTCs remain a
review item.
