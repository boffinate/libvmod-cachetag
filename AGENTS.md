# Agent Runbook

This repository is a standalone Vinyl Cache VMOD. Do not copy these sources into
the Vinyl Cache tree, and do not use host-local builds as verification. Build
and test it in Docker against a Vinyl development prefix.

## Layout

- `../vinyl-cache` is the expected sibling Vinyl Cache checkout.
- `vinyl-cache-ubuntu-build` is the expected local Docker image.
- `src/` contains the VMOD sources, VCC interface, VSC counters, and VTC tests.
- `scripts/test-with-vinyl-cache.sh` is the authoritative test entry point.
- `scripts/benchmark-cachetag-vmod.sh` builds the standalone VMOD for benchmark
  VTCs; it also does not modify the Vinyl checkout.

## Documentation/note file naming

Use the structure `YYYYMMDD_HHMM_[type]_[description].md`, where `[type]` is `note` `plan` `report` or other descriptive term. `[description]` is a short hyphen-separated description of the contents. If it relates to a planned step include this at the start of the description e.g. `phase-4` or `step-2`.

## Required Rules

- Do not edit files under `../vinyl-cache` to test cachetag.
- Do not treat host-local autotools output as meaningful verification.
- Do not compile or type-check benchmark helper binaries on the host. The Go
  HTTP backend/driver helpers are part of the benchmark harness and must be
  built inside `scripts/benchmark-cachetag-vmod.sh` or the documented remote
  benchmark wrapper.
- Do not use host-local generated VTC runs as verification. Generate and run
  benchmark VTCs through the Docker/OrbStack benchmark harness.
- Generated autotools/build files such as `Makefile`, `configure`, `build-aux/`,
  `m4/`, `.libs/`, `.deps/`, and `libvmod-cachetag-*.tar.gz` are ignored build
  artifacts. Do not commit them.
- Because this is a research project keep a diagnostic log of what you discover and changes you make, to refer back to later. This is different to Git commit messages because it records failures as well as successes, benchmark data, options tried, so in future we know if we have tried something before and what the outcome was.
- Backwards compatibility is not needed; there are no users of this project yet
- At the start of a performance or benchmarking session, read `devdocs/docs/agent-session-brief.md` before loading broader benchmark history.
- Before designing or interpreting any benchmark, read `benchmarks/rules/INDEX.md`
  (one interpretation rule per file, each with its motivating incident). Campaign
  notes and reports must open with a `Rules reviewed: ...` line citing the
  applicable BR rules, and any benchmark misinterpretation that costs more than
  half a day must produce a new rule there, a harness tripwire, or both.

## Common Commands

Full standalone distribution check:

```sh
scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Fast standalone test suite, skipping `distcheck` install/uninstall/archive
checks:

```sh
CACHE_TAG_CHECK_TARGET=check scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Smoke check that the VMOD builds, loads into Vinyl, and serves one request:

```sh
CACHE_TAG_CHECK_TARGET=check CACHE_TAG_TESTS=vtc/cachetag_c00000.vtc \
  scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Run a specific regression:

```sh
CACHE_TAG_CHECK_TARGET=check CACHE_TAG_TESTS=vtc/cachetag_r00004.vtc \
  scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

After configuring locally against a Vinyl prefix, the Make target is:

```sh
make check-with-vinyl-cache VINYL_CACHE_SRC=../vinyl-cache
```

## What The Test Wrapper Does

`scripts/test-with-vinyl-cache.sh`:

1. Starts Docker with the Vinyl checkout mounted read-only.
2. Configures and builds Vinyl in `/tmp/vinyl-build`.
3. Installs Vinyl into `/tmp/vinyl-prefix`.
4. Copies this VMOD source into `/tmp/cachetag-src`, excluding ignored build
   artifacts.
5. Runs `./bootstrap --prefix=/tmp/vinyl-prefix` with `CACHE_TAG_CONFIGURE_ARGS`, which defaults to `--enable-demo-diagnostics --enable-test-hooks` so the full diagnostic VCL surface the suite expects is built. Set `CACHE_TAG_CONFIGURE_ARGS=""` to build and test the production surface instead; the VTC lists in `src/Makefile.am` shrink to the core tests automatically.
6. Runs `make`.
7. Runs `make distcheck` by default, or `CACHE_TAG_CHECK_TARGET` when set. `distcheck` reconfigures with the same diagnostic flags as the outer build (`DISTCHECK_CONFIGURE_FLAGS` propagation in `Makefile.am`/`configure.ac`).

The Vinyl source tree should be unchanged after the script exits.

## Expected Passing Output

Fellow-backed matrix, including generated Fellow-storage copies of the explicit
storage-agnostic lifecycle/race VTC list plus the explicit persistent FDO and
SIGKILL VTC lists:

```sh
scripts/test-fellow-with-vinyl-cache.sh ../vinyl-cache
```

For `CACHE_TAG_CHECK_TARGET=check` with the default diagnostic-surface build,
expect the standalone WAL test and the 53 storage-agnostic VTCs in `VTC_TESTS`
(16 `c`, 7 `r`, 30 `pm`) to pass:

```text
# TOTAL: 54
# PASS:  54
# FAIL:  0
# ERROR: 0
```

With `CACHE_TAG_CONFIGURE_ARGS=""` (production surface: no demo diagnostics,
no test hooks) only the core VTCs run, and the expected total is 38 (the WAL
test plus 37 VTCs: 15 `c`, 7 `r`, 15 `pm`).

For the default `distcheck`, expect:

```text
libvmod-cachetag-1.0.2 archives ready for distribution:
libvmod-cachetag-1.0.2.tar.gz
```

## When Debugging Failures

- If Docker cannot connect, fix Docker/OrbStack first.
- If `vinylapi` is missing inside the VMOD configure step, the Vinyl install
  phase failed or `PKG_CONFIG_PATH` was not set.
- If VTCs cannot import `cachetag`, check `src/.libs/libvmod_cachetag.so` inside
  the container build and the `vmod_path` from `src/Makefile.am`.
- If `distcheck` fails but `check` passes, inspect archive/distribution
  packaging first, not VMOD runtime behavior.
