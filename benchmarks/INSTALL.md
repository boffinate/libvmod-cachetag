# Installing the benchmark environment

The benchmark harness builds Vinyl Cache, cachetag, and optional Slash or xkey components inside its own image. You do not need to install their build dependencies on the host.

For how to choose a workload and interpret the results, read [README.md](README.md) and the [benchmark rules](rules/INDEX.md). This document only covers preparing a machine to run the harness.

## Local benchmark host

You need a machine with Docker available to the account that will run the benchmarks. The repository layout should include:

```text
workspace/
├── libvmod-cachetag/
├── vinyl-cache/
├── slash/                 # required for Buddy and Fellow runs
└── varnish-modules/       # optional; enables the xkey baseline
```

`vinyl-cache/` must be a sibling of this repository. `slash/` is needed when `BENCH_STORAGE_KIND` is `buddy` or `fellow`. The harness detects `varnish-modules/` automatically; set `RUN_XKEY=0` when you do not have it.

Build the image from the `libvmod-cachetag` checkout:

```sh
scripts/build-benchmark-image.sh
```

The image contains the compiler toolchain, autotools, Go, Python, `perf`, and the utilities used by the measurement scripts. Rebuild it after changing [docker/vinyl-cache-ubuntu-build.Dockerfile](../docker/vinyl-cache-ubuntu-build.Dockerfile), or use `DOCKER_BUILD_NO_CACHE=1` to rebuild every package layer.

Run a small smoke benchmark once the image is ready:

```sh
OBJECTS=1000 TAGS_PER_OBJECT=4 RUNS=1 RUN_XKEY=0 PERF_MODE=auto \
  scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

The first run builds Vinyl and cachetag into the benchmark build cache. Later runs can set `SKIP_BUILD=1`, but only when the source revisions and the selected storage kind are unchanged. The harness checks that provenance before reusing the cache.

For a Buddy or Fellow lane, point the harness at the Slash checkout:

```sh
BENCH_STORAGE_KIND=buddy SLASH_SRC=../slash \
  scripts/benchmark-cachetag-vmod.sh ../vinyl-cache

BENCH_STORAGE_KIND=fellow SLASH_SRC=../slash \
  scripts/benchmark-cachetag-vmod.sh ../vinyl-cache
```

The benchmark harness applies the cachetag Slash patch stack before it builds Slash for both storage kinds. That shared benchmark build does not make the patches a requirement for a standalone Buddy installation. Fellow lanes enable persistent cachetag metadata by default; Buddy lanes do not.

Use a native Linux host for measurements you intend to compare or publish. A development machine is useful for smoke runs, but virtualized environments often cannot provide hardware counters and may distort filesystem, memory, and timing results.

## Remote benchmark host

The remote wrapper provisions a fresh Debian- or Ubuntu-like server, synchronizes the required source trees over SSH, builds the image, runs a named matrix, and downloads the result bundle.

Before running it, make sure that:

- your local account can use `ssh`, `scp`, and `rsync` to reach the server;
- the remote account is `root` or has passwordless `sudo`;
- the server has enough RAM and disk for the selected matrix; and
- the local workspace contains `vinyl-cache/`, plus `slash/` for Buddy or Fellow matrices.

Provision the server and sync the checkouts:

```sh
scripts/remote-benchmark.sh setup user@host
```

Setup installs Docker, Git, SSH and measurement utilities, starts Docker when systemd is available, attempts to set `kernel.perf_event_paranoid=1`, and builds the benchmark image. It uses `sudo -n`, so it stops rather than prompting for a password.

Run a small matrix after setup:

```sh
scripts/remote-benchmark.sh run user@host sanity-smoke
```

The default remote workspace is `~/cachetag-bench`. Set `CACHE_TAG_REMOTE_DIR` to use another path. The wrapper normally resynchronizes local sources before every run; set `CACHE_TAG_REMOTE_SYNC=0` only when you deliberately want to run the already-synced checkout.

For storage-specific smoke runs:

```sh
scripts/remote-benchmark.sh run user@host buddy-smoke
scripts/remote-benchmark.sh run user@host fellow-smoke
```

Fellow matrices check both memory and disk headroom before starting. Read `scripts/remote-benchmark.sh --help` for the matrix catalogue, capacity defaults, and supported overrides.

## Results and maintenance

Local results are written to `benchmarks/results/<timestamp>/`. Remote runs download their artifacts to `benchmarks/remote-results/<date>_<host>/` unless you set `CACHE_TAG_FETCH_DIR` or pass an explicit destination.

Use `benchmarks/summarize_results.py` on either an extracted result directory or a downloaded `.tgz` bundle. Keep the raw result directory with any report: it records the build provenance, generated workload, VSC counters, driver metrics, timing samples, and host metadata needed to interpret the row.
