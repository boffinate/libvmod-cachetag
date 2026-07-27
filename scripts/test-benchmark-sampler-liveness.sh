#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}

"$docker_cmd" run --rm \
	-v "$repo_dir:/cachetag-host:ro" \
	"$image" \
	sh -c 'sh -n /cachetag-host/scripts/benchmark-cachetag-vmod.sh /cachetag-host/scripts/remote-benchmark.sh && python3 /cachetag-host/benchmarks/test_sampler_liveness.py'
