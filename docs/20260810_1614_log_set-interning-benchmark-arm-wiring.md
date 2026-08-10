# Set-interning benchmark arm wiring

Rules reviewed: BR-001 through BR-026; applicable: BR-001 (runtime-only cache needs cgroup peak collection), BR-002 (interleave arms), BR-008 (sequential runner), BR-009 (debug disabled), BR-011 (no swap), BR-012 (local macOS Docker is smoke/counter validation only), BR-014 (same hardware), BR-016 (separate, provenance-checked builds), BR-017 (harness identity), BR-019 (deterministic workload), BR-023 (raw tail), BR-024 (A/A noise), BR-025, and BR-026.

**Date:** 2026-08-10

**Task:** Make the benchmark harness compare direct membership vectors with opt-in hash-consed membership sets, without running a remote benchmark.

## Changes

- `BENCH_SET_INTERNING=0|1` now controls the cachetag configure argument used by `scripts/benchmark-cachetag-vmod.sh`: `0` selects `--disable-set-interning` and `1` selects `--enable-set-interning`.
- `CACHE_TAG_BENCH_SET_INTERNING=0|1` is forwarded by `scripts/remote-benchmark.sh` to that local-Docker benchmark wrapper for later use on a prepared server.
- Build provenance records the selected setting and refuses `SKIP_BUILD=1` if it differs from the cached build, satisfying BR-016 and preventing an accidental cross-arm comparison.
- Benchmark metadata records the selected setting and resulting configure argument.
- `benchmarks/summarize_results.py` includes all six `volatile_interned_*` VSC counters in the summary data and human-readable workload rows. This lets disabled builds report consistent, zero-valued counters.
- Corrected a pre-existing quote-escaping defect in `scripts/benchmark-cachetag-vmod.sh`'s embedded Docker shell program; it prevented the wrapper from being parsed by both `sh -n` and `bash -n`.

The setting affects cachetag's Default/Buddy volatile index representation. Fellow uses its separate on-disk index path, so it is not a meaningful arm for this comparison.

## Local Docker validation

Used the deterministic `cutover-mostly-shared` profile with 100 objects, five tags per object, one run, no warm-up, Default storage only, and `PERF_MODE=off`. This is a smoke and counter check, not a performance result.

- A disabled-arm Docker build and cached rerun completed successfully at `/private/tmp/libvmod-cachetag-set-interning-smoke-disabled-rerun`; its metadata records `bench_set_interning=0` and all six final `volatile_interned_*` counters are zero.
- Reusing that disabled cache with `BENCH_SET_INTERNING=1` failed before running a VTC with `BUILD PROVENANCE MISMATCH [BR-016]`, identifying the recorded and requested interning settings.
- An enabled-arm Docker build and cached rerun completed successfully at `/private/tmp/libvmod-cachetag-set-interning-smoke-enabled-rerun`; its metadata records `bench_set_interning=1`. The final workload summary reports one interned set, 100 set references, 99 hits, one miss, 72 B of set storage, and 512 B of table storage.
- Ran `benchmarks/summarize_results.py` inside the project Docker image over both result directories. Both sets validate as complete; the enabled summary reports the expected non-zero counters and the disabled summary reports zeroes.

Shell syntax checks passed for `scripts/benchmark-cachetag-vmod.sh`, `scripts/remote-benchmark.sh`, and `benchmarks/build_provenance.sh`. `BENCH_SET_INTERNING=2` is rejected before Docker starts.

## Scope and next step

No remote command was run. The local runs establish only that both configure arms, counters, provenance, and result processing work. They do not establish a CPU, memory, or latency benefit on production-like hardware.

The existing positive profile creates one shared complete membership set, and the existing unique profiles create distinct complete sets. Before a remote campaign, add CMS-weighted profiles with controlled intermediate exact-set reuse, such as stable-set regeneration and listing-set mutation, then freeze an interleaved run plan with A/A baselining and cgroup peak collection.
