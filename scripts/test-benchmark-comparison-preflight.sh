#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/cachetag-comparison-preflight.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

fake_docker=$tmp_dir/docker
printf '%s\n' \
    '#!/bin/sh' \
    'if [ "$1" = image ] && [ "$2" = inspect ]; then' \
    '    printf "%s\\n" sha256:comparison-preflight-test' \
    '    exit 0' \
    'fi' \
    'printf "%s\\n" preflight-regression-docker-run >&2' \
    'exit 73' > "$fake_docker"
chmod +x "$fake_docker"

set +e
output=$(
    DOCKER="$fake_docker" \
    VINYL_DOCKER_IMAGE=comparison-preflight-test \
    VINYL_CACHE_SRC="$repo_dir/../vinyl-cache" \
    XKEY_SRC="$repo_dir" \
    RUN_XKEY=1 \
    BENCHMARK_CONTRACT=comparison-v1 \
    BENCH_COMPARISON_MEMORY_ENDPOINTS=1 \
    BENCH_DRIVER_HEADROOM_REQUIRED=1 \
    BENCH_DRIVER_HEADROOM_TARGET_RPS=100 \
    BENCH_CONCURRENT_TARGET_RPS=100 \
    BENCH_WARM_SECONDS=1 \
    BENCH_DRIVER_CPUSET_CPUS=0 \
    BENCH_BACKEND_CPUSET_CPUS=1 \
    BENCH_VINYL_CPUSET_CPUS=2 \
    BUILD_DIR="$tmp_dir/build" \
    RESULTS_DIR="$tmp_dir/results" \
    sh "$repo_dir/scripts/benchmark-cachetag-vmod.sh" 2>&1
)
status=$?
set -e

[ "$status" -eq 73 ] || {
    printf '%s\n' "$output" >&2
    printf 'comparison-v1 preflight did not reach the Docker run boundary (status %s)\n' "$status" >&2
    exit 1
}
case "$output" in
    *preflight-regression-docker-run*) ;;
    *) printf '%s\n' "$output" >&2; exit 1 ;;
esac
case "$output" in
    *'run_xkey: parameter not set'*|*'bench_'*'parameter not set'*)
        printf '%s\n' "$output" >&2
        printf '%s\n' 'comparison-v1 preflight evaluated an unbound harness variable' >&2
        exit 1
        ;;
esac

printf '%s\n' 'comparison-v1 preflight regression: PASS'
