# Testing

We always build and test in Docker against a Vinyl development prefix. We've scripted the setup, and the script builds Vinyl, installs it into a temporary prefix, builds this VMOD standalone, and runs the checks. The Vinyl checkout remains untouched.

Full distribution check (`make distcheck`):

```sh
scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Faster standalone suite, skipping the `distcheck` install/uninstall/archive checks:

```sh
CACHE_TAG_CHECK_TARGET=check scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Smoke check that the VMOD builds, loads into Vinyl, and serves one request:

```sh
CACHE_TAG_CHECK_TARGET=check CACHE_TAG_TESTS=vtc/cachetag_c00000.vtc \
  scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Run a single regression:

```sh
CACHE_TAG_CHECK_TARGET=check CACHE_TAG_TESTS=vtc/cachetag_r00004.vtc \
  scripts/test-with-vinyl-cache.sh ../vinyl-cache
```

Full Fellow integration check, including patched Slash/Fellow and the persistent cachetag VTCs (set `SLASH_SRC=/path/to/slash` when the Slash checkout is not next to this repository):

```sh
scripts/test-fellow-with-vinyl-cache.sh ../vinyl-cache
```

After configuring this project against a Vinyl prefix, the Docker check is also available as a make target:

```sh
make check-with-vinyl-cache VINYL_CACHE_SRC=/path/to/vinyl-cache
```

The development tooling under `scripts/`, `benchmarks/`, and `docker/` ships in the git repository only, not in release tarballs, so clone the repository if you want to run or read them.
