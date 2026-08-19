#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/cachetag-interning-screen-preflight.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

fake_docker=$tmp_dir/docker
printf '%s\n' \
    '#!/bin/sh' \
    'if [ "$1" = image ] && [ "$2" = inspect ]; then' \
    '    printf "%s\\n" sha256:interning-screen-preflight-test' \
    '    exit 0' \
    'fi' \
    'printf "%s\\n" preflight-regression-docker-run >&2' \
    'exit 73' > "$fake_docker"
chmod +x "$fake_docker"

run_preflight() {
    env \
    DOCKER="$fake_docker" \
    VINYL_DOCKER_IMAGE=interning-screen-preflight-test \
    VINYL_CACHE_SRC="$repo_dir/../vinyl-cache" \
    XKEY_SRC="$repo_dir" \
    BENCHMARK_CONTRACT=interning-screen-v1 \
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
    "$@" \
    sh "$repo_dir/scripts/benchmark-cachetag-vmod.sh"
}

set +e
output=$(run_preflight RUN_XKEY=0 RUN_NOINDEX=0 2>&1)
status=$?
set -e
[ "$status" -eq 73 ] || {
    printf '%s\n' "$output" >&2
    printf '%s\n' "interning-screen-v1 preflight did not reach the Docker run boundary (status $status)" >&2
    exit 1
}
case "$output" in
    *preflight-regression-docker-run*) ;;
    *) printf '%s\n' "$output" >&2; exit 1 ;;
esac
case "$output" in
    *'run_xkey: parameter not set'*|*'bench_'*'parameter not set'*)
        printf '%s\n' "$output" >&2
        printf '%s\n' 'interning-screen-v1 preflight evaluated an unbound harness variable' >&2
        exit 1
        ;;
esac

for rejected in RUN_XKEY=1 RUN_NOINDEX=1; do
    set +e
    output=$(run_preflight RUN_XKEY=0 RUN_NOINDEX=0 "$rejected" 2>&1)
    status=$?
    set -e
    [ "$status" -eq 2 ] || {
        printf '%s\n' "$output" >&2
        printf '%s\n' "interning-screen-v1 did not reject $rejected (status $status)" >&2
        exit 1
    }
    case "$rejected:$output" in
        RUN_XKEY=1:*'interning-screen-v1 requires RUN_XKEY=0'*) ;;
        RUN_NOINDEX=1:*'interning-screen-v1 requires RUN_NOINDEX=0'*) ;;
        *) printf '%s\n' "$output" >&2; exit 1 ;;
    esac
done

printf '%s\n' 'interning-screen-v1 preflight regression: PASS'
