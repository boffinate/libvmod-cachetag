#!/bin/sh
# Focused Docker/Vinyl contract for xkey stored-header behaviour.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}
tmpdir=${TMPDIR:-/tmp}
test -d "$tmpdir" && test -w "$tmpdir" || {
	echo "TMPDIR must name a writable directory: $tmpdir" >&2
	exit 2
}
results=$(mktemp -d "$tmpdir/cachetag-xkey-contract.XXXXXX")

cleanup_results() {
	status=$?
	if rm -rf "$results"; then
		return "$status"
	fi

	# Docker runs the benchmark as root. On Linux, hand a bind mount's files
	# back to the invoking user before retrying host-side cleanup.
	results_abs=$(CDPATH= cd -- "$results" && pwd) || {
		echo "cannot resolve contract results directory for cleanup: $results" >&2
		if [ "$status" -eq 0 ]; then
			return 1
		fi
		return "$status"
	}
	if $docker_cmd run --rm -v "$results_abs:/results" "$image" \
		chown -R "$(id -u):$(id -g)" /results >/dev/null 2>&1 && rm -rf "$results"; then
		return "$status"
	fi

	echo "cannot clean xkey contract results directory: $results" >&2
	if [ "$status" -eq 0 ]; then
		return 1
	fi
	return "$status"
}
trap cleanup_results EXIT INT TERM HUP

RESULTS_DIR=$results \
BENCHMARK_CONTRACT=development-v1 \
BENCH_PROFILE=xkey-contract \
BENCH_WORKLOAD_FILTER=xkey_xkey_contract \
OBJECTS=16 TAGS_PER_OBJECT=4 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=0 \
BENCH_WARM_SECONDS=1 BENCH_RESIDENCY_VALIDATE_OBJECTS=16 \
VTC_TIMEOUT=300 \
"$repo_dir/scripts/benchmark-cachetag-vmod.sh" "$repo_dir/../vinyl-cache"

for phase in load purge; do
	driver="$results/xkey_xkey_contract_${phase}.run-1.driver"
	test -s "$driver"
	grep -qx 'driver_errors=0' "$driver"
done
echo "xkey Vinyl compatibility contract passed"
