# Set-interning benchmark readiness

Rules reviewed: BR-001..BR-026; applicable: BR-001 (cgroup peaks require a runtime-only build cache), BR-002 (interleave arms), BR-008 (single sequential benchmark runner), BR-009 (generated VTCs use `debug=none`), BR-011 (invalidate swapped or undersaturated rows), BR-012 (local macOS is smoke/counter evidence only), BR-014 (keep one hardware cohort), BR-016 (separate/provenanced build caches), BR-017 (record harness identity), BR-019 (fixed work volume), BR-023 (judge raw tail samples), BR-024 (establish A/A noise floor), BR-025 (record CPU governor/boost), and BR-026 (freeze campaign scope before running).

**Date:** 2026-08-10
**Task:** Assess whether the existing benchmark suite can measure opt-in volatile membership-set interning against direct vector storage.

## Existing coverage

The generated VTC harness already captures post-load `CACHETAG.*` VSC snapshots, `index_memory_bytes`, load and warm-hit rates, process RSS/PSS/cgroup samples, and raw latency samples. `cutover-mostly-shared` is a useful multi-fold best case because every object has the same complete set. `cutover-mostly-unique`, `low-fanout-unique`, and `ten-unique-tags` are useful negative cases because complete sets differ per object. The `concurrent` profile can exercise attach throughput under request concurrency, and existing sampler/provenance mechanics are suitable once the two build arms are selectable.

## Blocking A/B gap

`scripts/benchmark-cachetag-vmod.sh` invokes `./bootstrap --prefix="$prefix"` without an environment-provided configure-argument channel. Since set interning defaults to disabled, every current benchmark arm builds the direct representation. `benchmarks/build_provenance.sh` hashes sources and storage kind but not configure arguments, so merely adding an argument without extending provenance would permit a stale disabled build to masquerade as an enabled `SKIP_BUILD=1` arm.

## Remaining workload and reporting gaps

- The raw VSC capture includes the new `volatile_interned_*` metrics, but `benchmarks/summarize_results.py` does not promote them to row/summary fields.
- The current profiles cover only the endpoints. `five-unique-five-shared` and `cutover-mixed` still create a distinct complete set for every object because one or more component tags are unique; they do not model intermediate exact-set reuse.
- The trace-derived `stable-set-regeneration` and `listing-set-mutation` scenarios are absent. They are needed to measure reference reuse and registry reclamation after the last object disappears.
- No profile represents the two observed CMS tag-count distributions: 90-day TTL-eligible fills (5.08 objects/set and 80.6% pair elimination) and 30-day fill stream (2.44 objects/set and 66.7% pair elimination), including their long membership-set tail.

## Recommended minimum next change

Add a `CACHE_TAG_CONFIGURE_ARGS` (or narrower `CACHE_TAG_BENCH_SET_INTERNING`) channel to the local and remote benchmark wrappers, record the resolved option in build provenance and result metadata, and make `SKIP_BUILD=1` reject a toggle mismatch. Promote the six intern VSC fields in the summarizer. Then run a frozen remote development screen with interleaved disabled/enabled arms: `cutover-mostly-shared` at several multi-fold widths, `cutover-mostly-unique` at the same widths, and the concurrent variants. Add the CMS-weighted stable-regeneration and mutation rows before any production-default decision.

No benchmark was run during this review, and no benchmark harness code was changed.
