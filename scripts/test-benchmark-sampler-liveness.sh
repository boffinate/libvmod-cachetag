#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}

"$docker_cmd" run --rm \
	-v "$repo_dir:/cachetag-host:ro" \
	"$image" \
	sh -c 'sh -n /cachetag-host/scripts/benchmark-cachetag-vmod.sh /cachetag-host/scripts/remote-benchmark.sh && test "$(grep -Fc -- "--no-inline --sort comm,dso,symbol" /cachetag-host/scripts/benchmark-cachetag-vmod.sh)" -eq 2 && test "$(grep -Fc -- "--call-graph \"\$BENCH_PERF_RECORD_CALL_GRAPH\"" /cachetag-host/scripts/benchmark-cachetag-vmod.sh)" -eq 2 && test "$(grep -Fc -- "perf script -i \"\$perf_data\" --no-inline" /cachetag-host/scripts/benchmark-cachetag-vmod.sh)" -eq 3 && test "$(grep -Fc -- "awk \"BEGIN" /cachetag-host/scripts/benchmark-cachetag-vmod.sh)" -eq 2 && test "$(grep -Fc -- "cachetag_|vmod_cachetag" /cachetag-host/scripts/benchmark-cachetag-vmod.sh)" -eq 2 && grep -F -- "perf-script-cachetag-locks.txt" /cachetag-host/scripts/benchmark-cachetag-vmod.sh >/dev/null && sh /cachetag-host/scripts/test-remote-benchmark-forwarding.sh && python3 /cachetag-host/benchmarks/test_run_with_phase_perf.py && python3 /cachetag-host/benchmarks/test_sampler_liveness.py && python3 /cachetag-host/benchmarks/test_decompose_cache_main_smaps.py'
