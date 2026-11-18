# Testing

We build and test in Docker against each engine's installed development SDK. The wrappers use `scripts/test-with-vcache.sh` to build the engine, install it into a temporary prefix, then build and test Cachetag without source API translation. Both source checkouts remain untouched. Use Varnish Cache 9.1 or Vinyl Cache 9.1 sources, or a compatible trunk checkout.

Build the dependency image once:

```sh
docker build -t vinyl-cache-ubuntu-build -f docker/vinyl-cache-ubuntu-build.Dockerfile .
```

Full distribution checks (`make distcheck`), including a build from the generated archive:

```sh
scripts/test-with-vinyl-cache.sh ../vinyl-cache
scripts/test-with-varnish-cache.sh ../varnish-cache
```

Both wrappers accept the same `CACHE_TAG_*` options. Set `VCACHE_DOCKER_IMAGE` to use a different dependency image (`VINYL_DOCKER_IMAGE` remains accepted). After the suite, `scripts/test-vcache-configure.sh` checks automatic SDK selection, the production build, version rejection, and the optional release manifest gate. The test runner uses `vtest` with the selected SDK's extension and the common `vcache` command. The `vinyl_vsc` spelling in `src/cachetag.vsc` is intentional: both 9.1 engines use it in their shared counter generator.

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

The Docker checks are also available as make targets:

```sh
make check-with-vinyl-cache VINYL_CACHE_SRC=/path/to/vinyl-cache
make check-with-varnish-cache VARNISH_CACHE_SRC=/path/to/varnish-cache
```

The development tooling under `scripts/`, `benchmarks/`, and `docker/` ships in the git repository only, not in release tarballs, so clone the repository if you want to run or read them.
