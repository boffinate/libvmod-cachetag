#!/bin/sh
# Run the benchmark harness unit tests inside the benchmark Docker image.
#
# Host-local Python runs are not verification for this repository (AGENTS.md);
# these tests must execute in the same image that runs the benchmark.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}

for script in "$repo_dir"/scripts/*.sh; do
	sh -n "$script"
done

"$docker_cmd" run --rm \
	-v "$repo_dir:/cachetag-host:ro" \
	-w /cachetag-host/benchmarks \
	"$image" \
	sh -c '
set -eu
status=0
for test in test_*.py; do
	printf "== %s\n" "$test"
	python3 "$test" || status=1
done
exit $status
'

echo 'benchmark python harness tests passed'
