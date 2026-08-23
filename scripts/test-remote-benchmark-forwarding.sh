#!/bin/sh
# Static, side-effect-free checks for the remote benchmark transport surface.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script=$repo_dir/scripts/remote-benchmark.sh

sh -n "$script"

transport_names='CACHE_TAG_BENCHMARK_CONTRACT
CACHE_TAG_BENCH_CPUSET_CPUS
CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS
CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS
CACHE_TAG_BENCH_VINYL_CPUSET_CPUS
CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED
CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS
CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS
CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS
CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS
CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS
CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS
CACHE_TAG_BENCH_DRIVER_GOMAXPROCS
CACHE_TAG_BENCH_BACKEND_GOMAXPROCS
CACHE_TAG_BENCH_DRIVER_GOGC
CACHE_TAG_BENCH_BACKEND_GOGC
CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT
CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT
CACHE_TAG_BENCH_BACKEND_BODY_BYTES
CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL
CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL
CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT
CACHE_TAG_BENCH_OBJECTS
CACHE_TAG_BENCH_BUCKETS
CACHE_TAG_BENCH_STORAGE
CACHE_TAG_BENCH_HTTP_TIMEOUT
CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS
CACHE_TAG_BENCH_PERF_STAT
CACHE_TAG_BENCH_PERF_STAT_RUNS
CACHE_TAG_BENCH_PERF_STAT_WORKLOAD
CACHE_TAG_BENCH_PERF_STAT_EVENTS
CACHE_TAG_BENCH_STALE_DELIVER'

printf '%s\n' "$transport_names" | while IFS= read -r name; do
	grep -F "${name}=\$(quote \"\$" "$script" >/dev/null
	grep -E "export .*${name}" "$script" >/dev/null
done

grep -F 'envs="\$envs BENCHMARK_CONTRACT=\$CACHE_TAG_BENCHMARK_CONTRACT"' "$script" >/dev/null
grep -F "CACHE_TAG_BENCH_CODE_GENERATION=%s" "$script" >/dev/null
grep -F "CACHE_TAG_BENCH_RUNTIME_SET_INTERNING=%s" "$script" >/dev/null
grep -F "CACHE_TAG_BENCH_LEGACY_SET_INTERNING=%s" "$script" >/dev/null
grep -F 'envs="\$envs BENCH_CODE_GENERATION=\$CACHE_TAG_BENCH_CODE_GENERATION"' "$script" >/dev/null
grep -F 'envs="\$envs BENCH_RUNTIME_SET_INTERNING=\$CACHE_TAG_BENCH_RUNTIME_SET_INTERNING"' "$script" >/dev/null
grep -F 'envs="\$envs BENCH_LEGACY_SET_INTERNING=\$CACHE_TAG_BENCH_LEGACY_SET_INTERNING"' "$script" >/dev/null
grep -F "CACHE_TAG_BENCH_BUILD_CFLAGS=%s" "$script" >/dev/null
grep -F 'export BENCH_BUILD_CFLAGS="\$CACHE_TAG_BENCH_BUILD_CFLAGS"' "$script" >/dev/null
grep -F 'bench_build_cflags=%s' "$script" >/dev/null
grep -F "CACHE_TAG_BENCH_PERF_RECORD_CALL_GRAPH=%s" "$script" >/dev/null
grep -F 'export BENCH_PERF_RECORD_CALL_GRAPH="\$CACHE_TAG_BENCH_PERF_RECORD_CALL_GRAPH"' "$script" >/dev/null
grep -F 'envs="\$envs BENCH_CONCURRENT_TARGET_RPS=\$CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS"' "$script" >/dev/null
grep -F 'envs="\$envs OBJECTS=\$CACHE_TAG_BENCH_OBJECTS"' "$script" >/dev/null
grep -F 'envs="\$envs PERF_MODE=off"' "$script" >/dev/null
grep -F 'export BENCH_PERF_STAT="\$CACHE_TAG_BENCH_PERF_STAT"' "$script" >/dev/null
grep -F 'export BENCH_PERF_STAT_RUNS="\$CACHE_TAG_BENCH_PERF_STAT_RUNS"' "$script" >/dev/null
grep -F 'export BENCH_PERF_STAT_WORKLOAD="\$CACHE_TAG_BENCH_PERF_STAT_WORKLOAD"' "$script" >/dev/null
grep -F 'export BENCH_PERF_STAT_EVENTS="\$CACHE_TAG_BENCH_PERF_STAT_EVENTS"' "$script" >/dev/null
grep -F 'export BENCH_STALE_DELIVER="\$CACHE_TAG_BENCH_STALE_DELIVER"' "$script" >/dev/null
grep -F 'runtime-interning-decision-d1' "$script" >/dev/null
grep -F 'runtime-interning-decision-r0-1' "$script" >/dev/null
grep -F 'runtime-interning-decision-i1' "$script" >/dev/null
grep -F 'runtime-interning-decision-r1-1' "$script" >/dev/null
grep -F 'decision_cohort_fingerprint' "$script" >/dev/null
grep -F 'CACHE_TAG_LEGACY_DIRECT_VMOD_SRC' "$script" >/dev/null
grep -F 'CACHE_TAG_LEGACY_INTERNED_VMOD_SRC' "$script" >/dev/null
grep -F 'runtime-interning-decision-d1|runtime-interning-decision-i1|runtime-interning-decision-r0-1) skip_build=0' "$script" >/dev/null
grep -F 'runtime-interning-decision-d2|runtime-interning-decision-i2|runtime-interning-decision-r1-1|runtime-interning-decision-r0-2|runtime-interning-decision-r1-2) skip_build=1' "$script" >/dev/null
expected_order='runtime-interning-decision-d1 runtime-interning-decision-r0-1 runtime-interning-decision-i1 runtime-interning-decision-r1-1 runtime-interning-decision-d2 runtime-interning-decision-r0-2 runtime-interning-decision-i2 runtime-interning-decision-r1-2'
actual_order=$(sed -n '/runtime-interning-decision)/,/;;/p' "$script" | grep -o 'runtime-interning-decision-[a-z0-9-]*' | tr '\n' ' ' | sed 's/ $//')
[ "$actual_order" = "$expected_order" ]
# Both perf attachments must stay single-profiler on the remote host.
[ "$(grep -Fc 'envs="\$envs PERF_MODE=off"' "$script")" -eq 2 ]

if grep -F 'CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD=$(quote "$bench_workload_filter_override")' "$script" >/dev/null; then
	echo 'perf workload transport is coupled to workload filter' >&2
	exit 1
fi

if grep -F 'CACHE_TAG_BENCH_PERF_STAT_WORKLOAD=$(quote "$bench_perf_record_workload_override")' "$script" >/dev/null; then
	echo 'perf stat workload transport is coupled to perf record workload' >&2
	exit 1
fi

echo 'remote benchmark forwarding checks passed'
