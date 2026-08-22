#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage:
  scripts/remote-benchmark.sh setup user@host
  scripts/remote-benchmark.sh run user@host MATRIX [LOCAL_DIR]
  scripts/remote-benchmark.sh fetch user@host LOCAL_DIR
  scripts/remote-benchmark.sh all user@host MATRIX LOCAL_DIR

Run cachetag VMOD benchmarks on a fresh Debian/Ubuntu Linux server over SSH.
The default remote workspace is relative to the SSH user's home, so root SSH and
cloud-image users such as ubuntu both work. Non-root users need passwordless
sudo unless they already have permission to run Docker.

Environment:
  CACHE_TAG_REMOTE_DIR     remote workspace (default: cachetag-bench)
  CACHE_TAG_FETCH_DIR      default local download directory for run/all
                           (default: benchmarks/remote-results/YYYYMMDD_host)
  VINYL_DOCKER_IMAGE       Docker image name (default: vinyl-cache-ubuntu-build)
  CACHE_TAG_REMOTE_SYNC    1 to rsync local checkouts before run (default: 1)
  CACHE_TAG_BENCH_CLIENTS  Remote load/validation client count. Defaults to a
                           hardware-derived value capped at 8.
  CACHE_TAG_VINYL_THREAD_POOL_MAX
                           Maximum Vinyl workers in each pool. Defaults to the
                           host logical-CPU count and is independent of clients.
                           The remote harness explicitly uses two worker pools;
                           keep this and the per-pool maximum fixed throughout
                           client sweeps.
  CACHE_TAG_PRESSURE_READERS
                           Pressure matrix reader goroutines. Empty means auto.
  CACHE_TAG_PRESSURE_WRITERS
                           Pressure matrix writer goroutines. Empty means auto.
  CACHE_TAG_PRESSURE_PURGERS
                           Pressure matrix purger goroutines. Empty means auto.
  CACHE_TAG_PRESSURE_TARGET_RPS
                           Pressure matrix aggregate read/write offered RPS.
                           Empty means auto; 0 means unbounded.
  CACHE_TAG_PRESSURE_PURGE_RATE
                           Pressure matrix purge requests/second. Empty means
                           auto.
  CACHE_TAG_SKIP_BUILD     1 to reuse the remote benchmark build cache; reuse is
                           provenance-checked against the synced sources (BR-016)
  CACHE_TAG_BENCH_PERF_RECORD
                           Pass BENCH_PERF_RECORD to the benchmark container
                           for opt-in perf record profiling (default: empty)
  CACHE_TAG_BENCH_PERF_RECORD_SCOPE
                           command or system (default: benchmark default)
  CACHE_TAG_BENCH_PERF_RECORD_PHASE
                           command, load, warm, or concurrent
                           (default: benchmark default)
  CACHE_TAG_BENCH_PERF_RECORD_TARGET
                           vinyld or descendants for phase profiles
                           (default: benchmark default)
  CACHE_TAG_BENCH_PERF_RECORD_RUNS
                           Number of runs per workload to record, or all
                           (default: benchmark default)
  CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD
                           Optional workload basename to profile
                           (default: empty)
  CACHE_TAG_BENCH_WORKLOAD_FILTER
                           Optional workload basename to run after generation
                           (default: empty)
  CACHE_TAG_BENCH_VALIDATE_RESIDENCY
                           Override BENCH_VALIDATE_RESIDENCY for the selected
                           matrix (default: matrix default)
  CACHE_TAG_BENCH_WARM_SECONDS
                           Override BENCH_WARM_SECONDS for the selected matrix
                           (default: matrix default)
  CACHE_TAG_BENCH_SKIP_PURGE
                           Override BENCH_SKIP_PURGE for phased-purge load-only
                           probes (default: matrix default)
  CACHE_TAG_BENCH_RESTART_TAG_PROFILE
                           Override BENCH_RESTART_TAG_PROFILE for restart
                           demand-load matrices (default: matrix default)
  CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT
                           Override BENCH_RESTART_TOUCH_PERCENT for restart
                           demand-load matrices (default: matrix default)
  CACHE_TAG_VTC_QUIET      Set VTC_QUIET on the remote run, 0 for full VTC logs
                           (default: benchmark default)
  CACHE_TAG_BENCH_PROFILE  Override BENCH_PROFILE for the selected matrix
                           (default: empty)
  CACHE_TAG_TAGS_PER_OBJECT
                           Override TAGS_PER_OBJECT for the selected matrix
                           (default: matrix default)
  CACHE_TAG_TAG_LENGTH_CLASS
                           Override BENCH_TAG_LENGTH_CLASS: short, default,
                           or long (default: matrix default)
  CACHE_TAG_VALIDATE_TAG_SHAPE
                           Override BENCH_VALIDATE_TAG_SHAPE (default: matrix
                           default)
  CACHE_TAG_CHURN_CYCLES   Override CHURN_CYCLES for short-ttl-high-churn
                           profiles (default: matrix default)
  CACHE_TAG_BENCH_TTL      Override generated benchmark VCL TTL for selected
                           profiles (default: matrix/profile default)
  CACHE_TAG_CHURN_COMPACT_EACH_CYCLE
                           Set CACHE_TAG_CHURN_COMPACT_EACH_CYCLE for
                           deterministic churn runs (default: matrix/profile
                           default)
  CACHE_TAG_BENCH_STORAGE_KIND
                           Override BENCH_STORAGE_KIND: default, fellow, or buddy
                           (default: matrix default)
  CACHE_TAG_BUDDY_SIZE     Override BENCH_BUDDY_SIZE (default: matrix default)
  CACHE_TAG_BUDDY_RESERVE_CHUNKS
                           Override BENCH_BUDDY_RESERVE_CHUNKS (default:
                           matrix default)
  CACHE_TAG_FELLOW_SIZE    Override BENCH_FELLOW_SIZE (default: matrix default)
  CACHE_TAG_FELLOW_SEGMENT_SIZE
                           Override BENCH_FELLOW_SEGMENT_SIZE
                           (default: benchmark default)
  CACHE_TAG_FELLOW_BLOCK_SIZE
                           Override BENCH_FELLOW_BLOCK_SIZE
                           (default: benchmark default)
  CACHE_TAG_CACHE_TAG_PERSIST
                           Override BENCH_CACHE_TAG_PERSIST (default: matrix
                           default)
  CACHE_TAG_WAL_FSYNC      Override BENCH_CACHE_TAG_WAL_FSYNC (default: strict)
  CACHE_TAG_SWEEP_BATCH_OBJECTS
                           Override BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS
  CACHE_TAG_SWEEP_BATCH_HOLD
                           Override BENCH_CACHE_TAG_SWEEP_BATCH_HOLD
  CACHE_TAG_SWEEP_BATCH_YIELD
                           Override BENCH_CACHE_TAG_SWEEP_BATCH_YIELD
  CACHE_TAG_SHUTDOWN_DRAIN_SECONDS
                           Override BENCH_SHUTDOWN_DRAIN_SECONDS
                           (default: benchmark default)
  CACHE_TAG_RUN_NOINDEX    Override RUN_NOINDEX for the selected matrix
                           (default: matrix default)
  CACHE_TAG_RUN_XKEY       Override RUN_XKEY for the selected matrix
                           (default: matrix default)
  CACHE_TAG_ALLOW_STALE_AFTER_PURGE
                           Set BENCH_ALLOW_STALE_AFTER_PURGE for stale-window
                           measurement runs (default: empty)
  CACHE_TAG_ALLOW_LRU_NUKED
                           Set BENCH_ALLOW_LRU_NUKED for over-resident
                           shutdown/log-growth probes (default: empty)
  CACHE_TAG_PURGE_SETTLE_MS
                           Set BENCH_PURGE_SETTLE_MS on the remote run
                           (default: empty)
  CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS
                           Set BENCH_PURGE_WINDOW_TIMEOUT_MS on the remote run
                           (default: empty)
  CACHE_TAG_PURGE_WINDOW_CONCURRENCY
                           Set BENCH_PURGE_WINDOW_CONCURRENCY on the remote run
                           (default: empty)
  CACHE_TAG_BENCH_PERF_FREQ
                           perf record frequency (default: benchmark default)
  CACHE_TAG_RUNS_OVERRIDE   Override RUNS for the selected matrix
                           (default: matrix default)
  CACHE_TAG_BENCHMARK_CONTRACT
                           Set BENCHMARK_CONTRACT (development-v1 or
                           comparison-v1; default: development-v1)
  CACHE_TAG_BENCH_CPUSET_CPUS
                           Docker CPU set for the benchmark container
  CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS
                           CPU set for the Go driver taskset placement
  CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS
                           CPU set for the origin backend taskset placement
  CACHE_TAG_BENCH_VINYL_CPUSET_CPUS
                           CPU set for the Vinyl process tree
  CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED
                           Require the executable trivial-endpoint headroom gate
  CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS
                           Headroom target RPS (must match offered RPS)
  CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS
                           Headroom probe duration in seconds
  CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS
                           Generic offered RPS override for the selected row
  CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS
                           Enable comparison cache-main endpoint captures
  CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS
                           Post-load cache-main quiescence delay
  CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS
                           Post-load confirmation quiescence delay
  CACHE_TAG_BENCH_DRIVER_GOMAXPROCS / CACHE_TAG_BENCH_BACKEND_GOMAXPROCS
                           Pin Go scheduler capacity for driver/backend
  CACHE_TAG_BENCH_DRIVER_GOGC / CACHE_TAG_BENCH_BACKEND_GOGC
                           Pin Go GC percentage for driver/backend
  CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT / CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT
                           Pin Go memory limit for driver/backend
  CACHE_TAG_BENCH_BACKEND_BODY_BYTES
                           Origin body size for the selected row
  CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL
                           Host sampler interval in seconds
  CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL / CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT
                           Detailed memory sampler interval/timeout
  CACHE_TAG_BENCH_OBJECTS / CACHE_TAG_BENCH_BUCKETS / CACHE_TAG_BENCH_STORAGE
                           Override synthetic row object, bucket and storage scale
  CACHE_TAG_BENCH_HTTP_TIMEOUT / CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS
                           Override request timeout and residency probes
  CACHE_TAG_INSTRUMENT_OBJ_MTX
                           Set BENCH_INSTRUMENT_OBJ_MTX for opt-in request-lock
                           and resize attribution (default: disabled)
  CACHE_TAG_MALLOC_CONF     Set BENCH_MALLOC_CONF for jemalloc diagnostic rows
  CACHE_TAG_MALLOC_ARENA_MAX
                           Optional glibc allocator fallback
  CACHE_TAG_MALLOC_TRIM_THRESHOLD
                           Optional glibc trim-threshold fallback
  CACHE_TAG_REMOTE_CLEAN_STALE
                           1 to remove old labelled benchmark containers
                           before a run, 0 to refuse (default: 0)
  REMOTE_DOCKER            remote Docker command: auto, docker, or
                           'sudo -n docker' (default: auto)
  REMOTE_DOCKER_RUN_ARGS   extra remote docker run args
                           (default: --cap-add PERFMON)

Matrices:
  Regression set (run before and after any performance-relevant change; see
  benchmarks/rules/INDEX.md for the interpretation rules):
  regression              core regression group, in order: sanity-smoke,
                          local-cost-attach-1m, local-cost-warm-1m,
                          local-cost-pressure-1m,
                          churn-deterministic-incremental-100k,
                          phase4-sweep-default-1m,
                          phase6-fill-drain-default-1m,
                          phase6-fill-drain-buddy-1m
  sanity-smoke            1k objects, all profiles, one run
  local-cost-attach-1m    1M attach-only lane, no residency validation
  local-cost-warm-1m      1M warm-hit lane, sampled residency validation
  local-cost-pressure-1m  1M read-purge pressure lane
  churn-deterministic-incremental-100k
                          100k deterministic rotating churn incremental lane
  phase4-sweep-default-1m Default-storage sweep/compact pause-latency lane with
                          attribution artifacts
  phase6-fill-drain-default-1m
                          Default-storage repeated fill/drain stability lane
  phase6-fill-drain-buddy-1m
                          Buddy-storage repeated fill/drain stability lane

  Scale and pre-release lanes (run when a release or a scale-sensitive change
  warrants them):
  sanity-10k              10k objects, all profiles, three runs
  pressure-100k           100k objects, pressure subset, sampled validation
  pressure-1m             1M objects, pressure subset, sampled validation
  pressure-5m             5M objects, pressure subset, sampled validation
  local-cost-100k         100k local-cost attach, warm-hit, and pressure group
  local-cost-1m           1M local-cost attach, warm-hit, and pressure group
  local-cost-attach-100k  100k attach-only lane, no residency validation
  local-cost-warm-100k    100k warm-hit lane, sampled residency validation
  local-cost-pressure-100k
                          100k read-purge pressure lane
  local-cost-pressure-paired-100k
                          100k pressure lane with no-index, xkey, and cachetag
  local-cost-pressure-paired-1m
                          1M pressure lane with no-index, xkey, and cachetag
  lowfanout-10m           10M low-fanout-unique with xkey comparison
  purgemap-fanout-attach-10m
                          10M high-fanout attach gate (attach-only)
  fanout-100k             100k objects, extreme-high-fanout, three runs
  fanout-1m               1M objects, extreme-high-fanout
  fanout-5m               5M objects, extreme-high-fanout
  fanout-10m              10M objects, extreme-high-fanout
  fanout-20m              20M objects, extreme-high-fanout
  fanout-30m              30M objects, extreme-high-fanout
  eviction-100k           100k objects, eviction only
  phase4-sweep-buddy-1m   Buddy-storage sweep/compact pause-latency lane
  phase6-fill-drain-noindex-default-1m
                          plain-Vinyl generation-ban ownership control for
                          allocator-tail attribution (rules/BR-006)
  churn-deterministic-full-100k
                          100k deterministic rotating churn full-expiry lane
  churn-deterministic-incremental-1m
                          1m deterministic rotating churn incremental lane
  churn-deterministic-incremental-5m
                          5m deterministic rotating churn incremental lane
  buddy-smoke             1k low-fanout-unique Buddy smoke lane with raw,
                          xkey, and cachetag workloads
  buddy-local-cost-100k   100k Buddy attach, warm-hit, and pressure group
  buddy-local-cost-attach-100k
                          100k Buddy attach-only lane
  buddy-local-cost-warm-100k
                          100k Buddy warm-hit lane
  buddy-local-cost-pressure-100k
                          100k Buddy read-purge pressure lane
  buddy-local-cost-pressure-paired-100k
                          100k Buddy pressure lane with no-index, xkey, and
                          cachetag
  backend-local-cost-100k Default, Buddy, and Fellow 100k local-cost lanes on
                          one host
  full                    maintenance group: regression plus pressure-100k,
                          pressure-1m, pressure-5m, eviction-100k, and
                          fellow-restart-idle-memory

  Fellow backlog lanes (run when Fellow persistence work resumes):
  fellow-smoke            1k low-fanout-unique Fellow persistent smoke lane
  fellow-local-cost-100k  100k low-fanout-unique Fellow persistent lane
  fellow-local-cost-1m    1M low-fanout-unique Fellow persistent grouped-WAL lane
  fellow-shutdown-5m      5M Fellow persistent grouped-WAL shutdown/log-growth
                          probe, allows eviction/residency loss
  fellow-shutdown-10m     10M Fellow persistent grouped-WAL shutdown/log-growth
                          probe, allows eviction/residency loss
  fellow-storage-attach-100k
                          100k Fellow no-index storage attach-only lane
  fellow-volatile-attach-100k
                          100k low-fanout-unique cachetag on Fellow without
                          cachetag persistence, attach-only
  fellow-persistent-attach-100k
                          100k low-fanout-unique cachetag on Fellow with
                          grouped persistent metadata, attach-only
  fellow-fanout-storage-attach-100k
                          100k extreme-high-fanout Fellow no-index storage
                          attach-only lane
  fellow-fanout-volatile-attach-100k
                          100k extreme-high-fanout volatile cachetag on
                          Fellow attach-only lane
  fellow-fanout-persistent-attach-100k
                          100k extreme-high-fanout persistent cachetag on
                          Fellow attach-only lane
  fellow-memory-paired-100k
                          100k Fellow no-index, volatile cachetag, and
                          persistent cachetag memory lanes
  fellow-memory-fanout-paired-100k
                          100k extreme-high-fanout Fellow no-index, volatile
                          cachetag, and persistent cachetag memory lanes
  fellow-memory-paired-1m 1M Fellow no-index, volatile cachetag, and
                          persistent cachetag memory lanes
  fellow-memory-paired-5m 5M Fellow no-index, volatile cachetag, and
                          persistent cachetag load-only memory lanes
  fellow-restart-idle-memory
                          Restart persistent cachetag on Fellow and capture
                          memory before post-restart traffic
  fellow-restart-idle-memory-1m
                          1M-object restart idle-memory scale lane
  fellow-restart-first-touch
                          Restart persistent cachetag on Fellow, touch a
                          sampled object set, and capture hydration deltas
  fellow-restart-cold-purge
                          Restart persistent cachetag on Fellow and purge a
                          cold key set
  fellow-restart-hot-purge
                          Repeat the cold purge key set after cache warmup
  stream1-boundary-control-125k
                          Campaign control: exact 125002 persistent purges,
                          unbounded history, only the empty startup checkpoint
  stream1-boundary-capped-125k
                          Campaign candidate: exact 125002 persistent purges,
                          100k cap and checkpoint expected
  stream1-default-control-1500k
                          Scale control: exact 1500003 persistent purges with
                          unbounded resident history
  stream1-default-capped-1500k
                          Scale candidate: exact 1500003 persistent purges
                          crossing the default 1M cap
  stream1-overlap-control-100k
                          Exact 100k preseed plus one trigger and 50k fixed
                          reads, with unbounded history
  stream1-overlap-capped-100k
                          Same overlap work with a 100k checkpointing cap
  stream1-overlap-control-1m
                          Default-scale overlap control with exact work
  stream1-overlap-capped-1m
                          Default 1M-cap overlap candidate with exact work

  Retired campaign matrices (Proposal 8 M0/M3 gates, purgemap-cutover groups,
  Phase 4 sweep scale ladder, Phase 5 held-publication lanes, selected-10m/20m/
  30m, eviction-1m, local-cost-resize-pressure-260k) were removed after their
  decisions closed; reproduce them from the harness commit recorded in each
  archived artifact.
EOF
}

case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

cmd=${1:-}
target=${2:-}
matrix=${3:-}
local_dest=${4:-}

if [ -z "$cmd" ] || [ -z "$target" ]; then
	usage >&2
	exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
workspace_dir=$(CDPATH= cd -- "$repo_dir/.." && pwd)
remote_dir=${CACHE_TAG_REMOTE_DIR:-cachetag-bench}
target_host=$target
case "$target_host" in
	*@*) target_host=${target_host##*@} ;;
esac
target_label=$(printf '%s' "$target_host" | tr '/:' '__')
fetch_date=$(date +%Y%m%d)
default_fetch_dir=${CACHE_TAG_FETCH_DIR:-"$repo_dir/benchmarks/remote-results/${fetch_date}_${target_label}"}
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
remote_sync=${CACHE_TAG_REMOTE_SYNC:-1}
bench_clients_override=${CACHE_TAG_BENCH_CLIENTS:-}
vinyl_threads_override=${CACHE_TAG_VINYL_THREAD_POOL_MAX:-}
pressure_readers_override=${CACHE_TAG_PRESSURE_READERS:-}
pressure_writers_override=${CACHE_TAG_PRESSURE_WRITERS:-}
pressure_purgers_override=${CACHE_TAG_PRESSURE_PURGERS:-}
pressure_target_rps_override=${CACHE_TAG_PRESSURE_TARGET_RPS:-}
pressure_purge_rate_override=${CACHE_TAG_PRESSURE_PURGE_RATE:-}
skip_build=${CACHE_TAG_SKIP_BUILD:-0}
# Retained only because the quoted remote environment line is a stable public
# transport surface. The benchmark harness no longer consumes or honours it.
allow_stale_build=
bench_perf_record_override=${CACHE_TAG_BENCH_PERF_RECORD:-}
bench_perf_record_scope_override=${CACHE_TAG_BENCH_PERF_RECORD_SCOPE:-}
bench_perf_record_phase_override=${CACHE_TAG_BENCH_PERF_RECORD_PHASE:-}
bench_perf_record_target_override=${CACHE_TAG_BENCH_PERF_RECORD_TARGET:-}
bench_perf_record_runs_override=${CACHE_TAG_BENCH_PERF_RECORD_RUNS:-}
bench_perf_record_workload_override=${CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD:-}
bench_workload_filter_override=${CACHE_TAG_BENCH_WORKLOAD_FILTER:-}
bench_validate_residency_override=${CACHE_TAG_BENCH_VALIDATE_RESIDENCY:-}
bench_warm_seconds_override=${CACHE_TAG_BENCH_WARM_SECONDS:-}
bench_skip_purge_override=${CACHE_TAG_BENCH_SKIP_PURGE:-}
bench_restart_tag_profile_override=${CACHE_TAG_BENCH_RESTART_TAG_PROFILE:-}
bench_restart_touch_percent_override=${CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT:-}
vtc_quiet_override=${CACHE_TAG_VTC_QUIET:-}
bench_profile_override=${CACHE_TAG_BENCH_PROFILE:-}
tags_per_object_override=${CACHE_TAG_TAGS_PER_OBJECT:-}
tag_length_class_override=${CACHE_TAG_TAG_LENGTH_CLASS:-}
validate_tag_shape_override=${CACHE_TAG_VALIDATE_TAG_SHAPE:-}
churn_cycles_override=${CACHE_TAG_CHURN_CYCLES:-}
bench_ttl_override=${CACHE_TAG_BENCH_TTL:-}
churn_compact_each_cycle_override=${CACHE_TAG_CHURN_COMPACT_EACH_CYCLE:-}
bench_storage_kind_override=${CACHE_TAG_BENCH_STORAGE_KIND:-}
buddy_size_override=${CACHE_TAG_BUDDY_SIZE:-}
buddy_reserve_chunks_override=${CACHE_TAG_BUDDY_RESERVE_CHUNKS:-}
fellow_size_override=${CACHE_TAG_FELLOW_SIZE:-}
fellow_segment_size_override=${CACHE_TAG_FELLOW_SEGMENT_SIZE:-}
fellow_block_size_override=${CACHE_TAG_FELLOW_BLOCK_SIZE:-}
cachetag_persist_override=${CACHE_TAG_CACHE_TAG_PERSIST:-}
wal_fsync_override=${CACHE_TAG_WAL_FSYNC:-}
sweep_batch_objects_override=${CACHE_TAG_SWEEP_BATCH_OBJECTS:-}
sweep_batch_hold_override=${CACHE_TAG_SWEEP_BATCH_HOLD:-}
sweep_batch_yield_override=${CACHE_TAG_SWEEP_BATCH_YIELD:-}
shutdown_drain_seconds_override=${CACHE_TAG_SHUTDOWN_DRAIN_SECONDS:-}
run_noindex_override=${CACHE_TAG_RUN_NOINDEX:-}
run_xkey_override=${CACHE_TAG_RUN_XKEY:-}
allow_stale_after_purge_override=${CACHE_TAG_ALLOW_STALE_AFTER_PURGE:-}
allow_lru_nuked_override=${CACHE_TAG_ALLOW_LRU_NUKED:-}
purge_settle_ms_override=${CACHE_TAG_PURGE_SETTLE_MS:-}
purge_window_timeout_ms_override=${CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS:-}
purge_window_concurrency_override=${CACHE_TAG_PURGE_WINDOW_CONCURRENCY:-}
bench_perf_freq_override=${CACHE_TAG_BENCH_PERF_FREQ:-}
runs_override=${CACHE_TAG_RUNS_OVERRIDE:-}
instrument_obj_mtx_override=${CACHE_TAG_INSTRUMENT_OBJ_MTX:-}
malloc_conf_override=${CACHE_TAG_MALLOC_CONF:-}
malloc_arena_max_override=${CACHE_TAG_MALLOC_ARENA_MAX:-}
malloc_trim_threshold_override=${CACHE_TAG_MALLOC_TRIM_THRESHOLD:-}
benchmark_contract_override=${CACHE_TAG_BENCHMARK_CONTRACT:-}
bench_cpuset_override=${CACHE_TAG_BENCH_CPUSET_CPUS:-}
bench_driver_cpuset_override=${CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS:-}
bench_backend_cpuset_override=${CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS:-}
bench_vinyl_cpuset_override=${CACHE_TAG_BENCH_VINYL_CPUSET_CPUS:-}
bench_driver_headroom_required_override=${CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED:-}
bench_driver_headroom_target_rps_override=${CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS:-}
bench_driver_headroom_seconds_override=${CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS:-}
bench_concurrent_target_rps_override=${CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS:-}
bench_comparison_memory_endpoints_override=${CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS:-}
bench_memory_post_load_quiet_seconds_override=${CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS:-}
bench_memory_confirmation_quiet_seconds_override=${CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS:-}
bench_driver_gomaxprocs_override=${CACHE_TAG_BENCH_DRIVER_GOMAXPROCS:-}
bench_backend_gomaxprocs_override=${CACHE_TAG_BENCH_BACKEND_GOMAXPROCS:-}
bench_driver_gogc_override=${CACHE_TAG_BENCH_DRIVER_GOGC:-}
bench_backend_gogc_override=${CACHE_TAG_BENCH_BACKEND_GOGC:-}
bench_driver_gomemlimit_override=${CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT:-}
bench_backend_gomemlimit_override=${CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT:-}
bench_backend_body_bytes_override=${CACHE_TAG_BENCH_BACKEND_BODY_BYTES:-}
bench_system_sample_interval_override=${CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL:-}
bench_detailed_memory_interval_override=${CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL:-}
bench_detailed_memory_timeout_override=${CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT:-}
bench_objects_override=${CACHE_TAG_BENCH_OBJECTS:-}
bench_buckets_override=${CACHE_TAG_BENCH_BUCKETS:-}
bench_storage_override=${CACHE_TAG_BENCH_STORAGE:-}
bench_http_timeout_override=${CACHE_TAG_BENCH_HTTP_TIMEOUT:-}
bench_residency_validate_objects_override=${CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS:-}
clean_stale=${CACHE_TAG_REMOTE_CLEAN_STALE:-0}
remote_docker=${REMOTE_DOCKER:-auto}
remote_docker_run_args=${REMOTE_DOCKER_RUN_ARGS:---cap-add PERFMON}

quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

remote_sh() {
	ssh "$target" "REMOTE_DIR=$(quote "$remote_dir") VINYL_DOCKER_IMAGE=$(quote "$image") REMOTE_DOCKER=$(quote "$remote_docker") REMOTE_DOCKER_RUN_ARGS=$(quote "$remote_docker_run_args") CACHE_TAG_BENCH_CLIENTS=$(quote "$bench_clients_override") CACHE_TAG_VINYL_THREAD_POOL_MAX=$(quote "$vinyl_threads_override") CACHE_TAG_PRESSURE_READERS=$(quote "$pressure_readers_override") CACHE_TAG_PRESSURE_WRITERS=$(quote "$pressure_writers_override") CACHE_TAG_PRESSURE_PURGERS=$(quote "$pressure_purgers_override") CACHE_TAG_PRESSURE_TARGET_RPS=$(quote "$pressure_target_rps_override") CACHE_TAG_PRESSURE_PURGE_RATE=$(quote "$pressure_purge_rate_override") CACHE_TAG_SKIP_BUILD=$(quote "$skip_build") CACHE_TAG_ALLOW_STALE_BUILD=$(quote "$allow_stale_build") CACHE_TAG_BENCH_PERF_RECORD=$(quote "$bench_perf_record_override") CACHE_TAG_BENCH_PERF_RECORD_SCOPE=$(quote "$bench_perf_record_scope_override") CACHE_TAG_BENCH_PERF_RECORD_PHASE=$(quote "$bench_perf_record_phase_override") CACHE_TAG_BENCH_PERF_RECORD_TARGET=$(quote "$bench_perf_record_target_override") CACHE_TAG_BENCH_PERF_RECORD_RUNS=$(quote "$bench_perf_record_runs_override") CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD=$(quote "$bench_perf_record_workload_override") CACHE_TAG_BENCH_WORKLOAD_FILTER=$(quote "$bench_workload_filter_override") CACHE_TAG_BENCH_VALIDATE_RESIDENCY=$(quote "$bench_validate_residency_override") CACHE_TAG_BENCH_WARM_SECONDS=$(quote "$bench_warm_seconds_override") CACHE_TAG_BENCH_SKIP_PURGE=$(quote "$bench_skip_purge_override") CACHE_TAG_BENCH_RESTART_TAG_PROFILE=$(quote "$bench_restart_tag_profile_override") CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT=$(quote "$bench_restart_touch_percent_override") CACHE_TAG_VTC_QUIET=$(quote "$vtc_quiet_override") CACHE_TAG_BENCH_PROFILE=$(quote "$bench_profile_override") CACHE_TAG_TAGS_PER_OBJECT=$(quote "$tags_per_object_override") CACHE_TAG_TAG_LENGTH_CLASS=$(quote "$tag_length_class_override") CACHE_TAG_VALIDATE_TAG_SHAPE=$(quote "$validate_tag_shape_override") CACHE_TAG_BENCH_TTL=$(quote "$bench_ttl_override") CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=$(quote "$churn_compact_each_cycle_override") CACHE_TAG_BENCH_STORAGE_KIND=$(quote "$bench_storage_kind_override") CACHE_TAG_BUDDY_SIZE=$(quote "$buddy_size_override") CACHE_TAG_BUDDY_RESERVE_CHUNKS=$(quote "$buddy_reserve_chunks_override") CACHE_TAG_FELLOW_SIZE=$(quote "$fellow_size_override") CACHE_TAG_FELLOW_SEGMENT_SIZE=$(quote "$fellow_segment_size_override") CACHE_TAG_FELLOW_BLOCK_SIZE=$(quote "$fellow_block_size_override") CACHE_TAG_CACHE_TAG_PERSIST=$(quote "$cachetag_persist_override") CACHE_TAG_WAL_FSYNC=$(quote "$wal_fsync_override") CACHE_TAG_SWEEP_BATCH_OBJECTS=$(quote "$sweep_batch_objects_override") CACHE_TAG_SWEEP_BATCH_HOLD=$(quote "$sweep_batch_hold_override") CACHE_TAG_SWEEP_BATCH_YIELD=$(quote "$sweep_batch_yield_override") CACHE_TAG_SHUTDOWN_DRAIN_SECONDS=$(quote "$shutdown_drain_seconds_override") CACHE_TAG_RUN_NOINDEX=$(quote "$run_noindex_override") CACHE_TAG_RUN_XKEY=$(quote "$run_xkey_override") CACHE_TAG_ALLOW_STALE_AFTER_PURGE=$(quote "$allow_stale_after_purge_override") CACHE_TAG_ALLOW_LRU_NUKED=$(quote "$allow_lru_nuked_override") CACHE_TAG_PURGE_SETTLE_MS=$(quote "$purge_settle_ms_override") CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS=$(quote "$purge_window_timeout_ms_override") CACHE_TAG_PURGE_WINDOW_CONCURRENCY=$(quote "$purge_window_concurrency_override") CACHE_TAG_BENCH_PERF_FREQ=$(quote "$bench_perf_freq_override") CACHE_TAG_RUNS_OVERRIDE=$(quote "$runs_override") CACHE_TAG_INSTRUMENT_OBJ_MTX=$(quote "$instrument_obj_mtx_override") CACHE_TAG_MALLOC_CONF=$(quote "$malloc_conf_override") CACHE_TAG_MALLOC_ARENA_MAX=$(quote "$malloc_arena_max_override") CACHE_TAG_MALLOC_TRIM_THRESHOLD=$(quote "$malloc_trim_threshold_override") CACHE_TAG_BENCHMARK_CONTRACT=$(quote "$benchmark_contract_override") CACHE_TAG_BENCH_CPUSET_CPUS=$(quote "$bench_cpuset_override") CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS=$(quote "$bench_driver_cpuset_override") CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS=$(quote "$bench_backend_cpuset_override") CACHE_TAG_BENCH_VINYL_CPUSET_CPUS=$(quote "$bench_vinyl_cpuset_override") CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED=$(quote "$bench_driver_headroom_required_override") CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS=$(quote "$bench_driver_headroom_target_rps_override") CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS=$(quote "$bench_driver_headroom_seconds_override") CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS=$(quote "$bench_concurrent_target_rps_override") CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS=$(quote "$bench_comparison_memory_endpoints_override") CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS=$(quote "$bench_memory_post_load_quiet_seconds_override") CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS=$(quote "$bench_memory_confirmation_quiet_seconds_override") CACHE_TAG_BENCH_DRIVER_GOMAXPROCS=$(quote "$bench_driver_gomaxprocs_override") CACHE_TAG_BENCH_BACKEND_GOMAXPROCS=$(quote "$bench_backend_gomaxprocs_override") CACHE_TAG_BENCH_DRIVER_GOGC=$(quote "$bench_driver_gogc_override") CACHE_TAG_BENCH_BACKEND_GOGC=$(quote "$bench_backend_gogc_override") CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT=$(quote "$bench_driver_gomemlimit_override") CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT=$(quote "$bench_backend_gomemlimit_override") CACHE_TAG_BENCH_BACKEND_BODY_BYTES=$(quote "$bench_backend_body_bytes_override") CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL=$(quote "$bench_system_sample_interval_override") CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL=$(quote "$bench_detailed_memory_interval_override") CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT=$(quote "$bench_detailed_memory_timeout_override") CACHE_TAG_BENCH_OBJECTS=$(quote "$bench_objects_override") CACHE_TAG_BENCH_BUCKETS=$(quote "$bench_buckets_override") CACHE_TAG_BENCH_STORAGE=$(quote "$bench_storage_override") CACHE_TAG_BENCH_HTTP_TIMEOUT=$(quote "$bench_http_timeout_override") CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS=$(quote "$bench_residency_validate_objects_override") CACHE_TAG_REMOTE_CLEAN_STALE=$(quote "$clean_stale"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; export REMOTE_DIR VINYL_DOCKER_IMAGE REMOTE_DOCKER REMOTE_DOCKER_RUN_ARGS CACHE_TAG_BENCH_CLIENTS CACHE_TAG_VINYL_THREAD_POOL_MAX CACHE_TAG_PRESSURE_READERS CACHE_TAG_PRESSURE_WRITERS CACHE_TAG_PRESSURE_PURGERS CACHE_TAG_PRESSURE_TARGET_RPS CACHE_TAG_PRESSURE_PURGE_RATE CACHE_TAG_SKIP_BUILD CACHE_TAG_ALLOW_STALE_BUILD CACHE_TAG_BENCH_PERF_RECORD CACHE_TAG_BENCH_PERF_RECORD_SCOPE CACHE_TAG_BENCH_PERF_RECORD_PHASE CACHE_TAG_BENCH_PERF_RECORD_TARGET CACHE_TAG_BENCH_PERF_RECORD_RUNS CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD CACHE_TAG_BENCH_WORKLOAD_FILTER CACHE_TAG_BENCH_VALIDATE_RESIDENCY CACHE_TAG_BENCH_WARM_SECONDS CACHE_TAG_BENCH_SKIP_PURGE CACHE_TAG_BENCH_RESTART_TAG_PROFILE CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT CACHE_TAG_VTC_QUIET CACHE_TAG_BENCH_PROFILE CACHE_TAG_TAGS_PER_OBJECT CACHE_TAG_TAG_LENGTH_CLASS CACHE_TAG_VALIDATE_TAG_SHAPE CACHE_TAG_BENCH_TTL CACHE_TAG_CHURN_COMPACT_EACH_CYCLE CACHE_TAG_BENCH_STORAGE_KIND CACHE_TAG_BUDDY_SIZE CACHE_TAG_BUDDY_RESERVE_CHUNKS CACHE_TAG_FELLOW_SIZE CACHE_TAG_FELLOW_SEGMENT_SIZE CACHE_TAG_FELLOW_BLOCK_SIZE CACHE_TAG_CACHE_TAG_PERSIST CACHE_TAG_WAL_FSYNC CACHE_TAG_SWEEP_BATCH_OBJECTS CACHE_TAG_SWEEP_BATCH_HOLD CACHE_TAG_SWEEP_BATCH_YIELD CACHE_TAG_SHUTDOWN_DRAIN_SECONDS CACHE_TAG_RUN_NOINDEX CACHE_TAG_RUN_XKEY CACHE_TAG_ALLOW_STALE_AFTER_PURGE CACHE_TAG_ALLOW_LRU_NUKED CACHE_TAG_PURGE_SETTLE_MS CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS CACHE_TAG_PURGE_WINDOW_CONCURRENCY CACHE_TAG_BENCH_PERF_FREQ CACHE_TAG_RUNS_OVERRIDE CACHE_TAG_INSTRUMENT_OBJ_MTX CACHE_TAG_MALLOC_CONF CACHE_TAG_MALLOC_ARENA_MAX CACHE_TAG_MALLOC_TRIM_THRESHOLD CACHE_TAG_BENCHMARK_CONTRACT CACHE_TAG_BENCH_CPUSET_CPUS CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS CACHE_TAG_BENCH_VINYL_CPUSET_CPUS CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS CACHE_TAG_BENCH_DRIVER_GOMAXPROCS CACHE_TAG_BENCH_BACKEND_GOMAXPROCS CACHE_TAG_BENCH_DRIVER_GOGC CACHE_TAG_BENCH_BACKEND_GOGC CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT CACHE_TAG_BENCH_BACKEND_BODY_BYTES CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT CACHE_TAG_BENCH_OBJECTS CACHE_TAG_BENCH_BUCKETS CACHE_TAG_BENCH_STORAGE CACHE_TAG_BENCH_HTTP_TIMEOUT CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS CACHE_TAG_REMOTE_CLEAN_STALE; sh -s"
}

install_remote() {
	remote_sh <<'EOF'
set -eu

if [ -r /etc/os-release ]; then
	. /etc/os-release
	case "${ID:-}:${ID_LIKE:-}" in
		debian:*|ubuntu:*|*:debian*) ;;
		*) echo "remote host must be Debian/Ubuntu-like" >&2; exit 1 ;;
	esac
fi

sudo_cmd=
if [ "$(id -u)" != 0 ]; then
	if command -v sudo >/dev/null 2>&1; then
		sudo_cmd="sudo -n"
	else
		echo "remote setup needs root or passwordless sudo" >&2
		exit 1
	fi
fi

$sudo_cmd apt-get update
DEBIAN_FRONTEND=noninteractive $sudo_cmd apt-get install -y \
	ca-certificates \
	docker.io \
	git \
	openssh-client \
	procps \
	rsync \
	sysstat \
	tar \
	time

if command -v systemctl >/dev/null 2>&1; then
	$sudo_cmd systemctl enable --now docker >/dev/null 2>&1 || true
fi
if [ -w /proc/sys/kernel/perf_event_paranoid ] || [ -n "$sudo_cmd" ]; then
	$sudo_cmd sysctl -w kernel.perf_event_paranoid=1 >/dev/null 2>&1 || true
fi

mkdir -p "$REMOTE_DIR"
EOF

	scp "$repo_dir/docker/vinyl-cache-ubuntu-build.Dockerfile" \
		"$target:$remote_dir/Dockerfile.vinyl-cache-ubuntu-build"

	remote_sh <<'EOF'
set -eu

docker_cmd=$REMOTE_DOCKER
if [ "$docker_cmd" = auto ]; then
	if docker ps >/dev/null 2>&1; then
		docker_cmd=docker
	elif command -v sudo >/dev/null 2>&1 && sudo -n docker ps >/dev/null 2>&1; then
		docker_cmd="sudo -n docker"
	else
		echo "docker is installed, but this SSH user cannot run docker directly or with passwordless sudo" >&2
		exit 1
	fi
fi

$docker_cmd build -t "$VINYL_DOCKER_IMAGE" -f "$REMOTE_DIR/Dockerfile.vinyl-cache-ubuntu-build" "$REMOTE_DIR"
$docker_cmd ps >/dev/null
EOF
}

sync_checkout() {
	ssh "$target" "REMOTE_DIR=$(quote "$remote_dir"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; mkdir -p \"\$REMOTE_DIR\"; printf '%s\n' \"\$REMOTE_DIR\""
	resolved_remote_dir=$(ssh "$target" "REMOTE_DIR=$(quote "$remote_dir"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; printf '%s\n' \"\$REMOTE_DIR\"")
	rsync -a --delete \
		--exclude='.codex' \
		--exclude='.agents' \
		--exclude='devdocs' \
		--exclude='.libs' \
		--exclude='.deps' \
		--exclude='autom4te.cache' \
		--exclude='build-aux' \
		--exclude='m4' \
		--exclude='Makefile' \
		--exclude='Makefile.in' \
		--exclude='config.log' \
		--exclude='config.status' \
		--exclude='configure' \
		--exclude='docs' \
		--exclude='benchmarks/results' \
		--exclude='benchmarks/remote-results' \
		--exclude='libvmod-cachetag-*.tar.gz' \
		"$repo_dir/" "$target:$resolved_remote_dir/libvmod-cachetag/"
	rsync -a --delete \
		--exclude='devdocs' \
		"$workspace_dir/vinyl-cache/" "$target:$resolved_remote_dir/vinyl-cache/"
	if [ -d "$workspace_dir/varnish-modules" ]; then
		rsync -a --delete \
			--exclude='devdocs' \
			"$workspace_dir/varnish-modules/" "$target:$resolved_remote_dir/varnish-modules/"
	fi
	if [ -d "$workspace_dir/slash" ]; then
		rsync -a --delete \
			--exclude='devdocs' \
			--exclude='.libs' \
			--exclude='.deps' \
			--exclude='autom4te.cache' \
			--exclude='build-aux' \
			--exclude='m4' \
			--exclude='Makefile' \
			--exclude='Makefile.in' \
			--exclude='config.log' \
			--exclude='config.status' \
			--exclude='configure' \
			"$workspace_dir/slash/" "$target:$resolved_remote_dir/slash/"
	fi
}

run_matrix() {
	if [ -z "$matrix" ]; then
		echo "missing MATRIX" >&2
		exit 2
	fi
	remote_sh <<EOF
set -eu
matrix=$(quote "$matrix")
churn_cycles_override=$(quote "$churn_cycles_override")
remote_dir=\$REMOTE_DIR
mkdir -p "\$REMOTE_DIR/fetch"
rm -f "\$REMOTE_DIR/fetch/last-result-dir"

docker_cmd=\$REMOTE_DOCKER
if [ "\$docker_cmd" = auto ]; then
	if docker ps >/dev/null 2>&1; then
		docker_cmd=docker
	elif command -v sudo >/dev/null 2>&1 && sudo -n docker ps >/dev/null 2>&1; then
		docker_cmd="sudo -n docker"
	else
		echo "docker is installed, but this SSH user cannot run docker directly or with passwordless sudo" >&2
		exit 1
	fi
fi

stale_containers=\$(\$docker_cmd ps -aq --filter label=org.cachetag.benchmark=1)
if [ -n "\$stale_containers" ]; then
	if [ "\$CACHE_TAG_REMOTE_CLEAN_STALE" = 1 ]; then
		\$docker_cmd rm -f \$stale_containers >/dev/null
	else
		echo "refusing \$matrix: stale cachetag benchmark Docker container(s) exist:" >&2
		\$docker_cmd ps -a --filter label=org.cachetag.benchmark=1 --format '  {{.ID}} {{.Status}} {{.Label "org.cachetag.benchmark.matrix"}} {{.Label "org.cachetag.benchmark.result_id"}}' >&2
		echo "set CACHE_TAG_REMOTE_CLEAN_STALE=1 to remove them before running" >&2
		exit 1
	fi
fi

cores=\$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN)
physical_cores=\$cores
if command -v lscpu >/dev/null 2>&1; then
	detected_physical=\$(lscpu -p=Core,Socket 2>/dev/null | awk -F, '!/^#/ { print \$1 "," \$2 }' | sort -u | wc -l)
	if [ "\$detected_physical" -gt 0 ]; then
		physical_cores=\$detected_physical
	fi
fi
if [ -n "\$CACHE_TAG_BENCH_CLIENTS" ]; then
	bench_clients=\$CACHE_TAG_BENCH_CLIENTS
elif [ "\$cores" -gt 16 ]; then
	bench_clients=8
elif [ "\$cores" -gt 8 ]; then
	bench_clients=\$((cores / 2))
elif [ "\$cores" -gt 2 ]; then
	bench_clients=\$((cores - 1))
else
	bench_clients=1
fi
if [ -n "\$CACHE_TAG_VINYL_THREAD_POOL_MAX" ]; then
	bench_vinyl_thread_pool_max=\$CACHE_TAG_VINYL_THREAD_POOL_MAX
else
	bench_vinyl_thread_pool_max=\$cores
	if [ "\$bench_vinyl_thread_pool_max" -lt 1 ]; then
		bench_vinyl_thread_pool_max=1
	fi
fi

pressure_readers=\$((cores - 4))
if [ "\$pressure_readers" -lt "\$bench_clients" ]; then
	pressure_readers=\$bench_clients
fi
pressure_writers=\$((physical_cores / 4))
if [ "\$pressure_writers" -lt 1 ]; then
	pressure_writers=1
fi
pressure_purgers=\$((physical_cores / 12))
if [ "\$pressure_purgers" -lt 1 ]; then
	pressure_purgers=1
fi
pressure_rps=\$((cores * 250))
if [ "\$pressure_rps" -lt 1000 ]; then
	pressure_rps=1000
fi
if [ -n "\$CACHE_TAG_PRESSURE_READERS" ]; then
	pressure_readers=\$CACHE_TAG_PRESSURE_READERS
fi
if [ -n "\$CACHE_TAG_PRESSURE_WRITERS" ]; then
	pressure_writers=\$CACHE_TAG_PRESSURE_WRITERS
fi
if [ -n "\$CACHE_TAG_PRESSURE_PURGERS" ]; then
	pressure_purgers=\$CACHE_TAG_PRESSURE_PURGERS
fi
if [ -n "\$CACHE_TAG_PRESSURE_TARGET_RPS" ]; then
	pressure_rps=\$CACHE_TAG_PRESSURE_TARGET_RPS
fi
pressure_purge_rate=\$((pressure_purgers * 10))
if [ -n "\$CACHE_TAG_PRESSURE_PURGE_RATE" ]; then
	pressure_purge_rate=\$CACHE_TAG_PRESSURE_PURGE_RATE
fi

mem_total_kb=\$(awk '/MemTotal:/ { print \$2 }' /proc/meminfo)
mem_total_gb=\$((mem_total_kb / 1024 / 1024))

check_storage_headroom() {
	storage_gb=\$1
	max_percent=\${2:-110}
	if [ "\$mem_total_gb" -gt 0 ] && [ "\$storage_gb" -gt \$((mem_total_gb * max_percent / 100)) ]; then
		echo "refusing \$matrix: BENCH_STORAGE=\${storage_gb}g exceeds \${max_percent}% of RAM (\${mem_total_gb}g). Use a larger server or edit the matrix deliberately." >&2
		exit 1
	fi
}

check_disk_headroom() {
	storage_gb=\$1
	avail_gb=\$(df -BG "\$remote_dir" | awk 'NR == 2 { gsub(/G/, "", \$4); print \$4 }')
	if [ -n "\$avail_gb" ] && [ "\$avail_gb" -gt 0 ] && [ "\$storage_gb" -gt \$((avail_gb * 75 / 100)) ]; then
		echo "refusing \$matrix: BENCH_FELLOW_SIZE=\${storage_gb}g exceeds 75% of available disk under \$remote_dir (\${avail_gb}g)." >&2
		exit 1
	fi
}

cutover_tag_count() {
	cutover_tags=\${matrix##*-t}
	case "\$cutover_tags" in
		1|5|6|10|20) ;;
		*)
			echo "unknown cutover tag count in matrix: \$matrix" >&2
			exit 2
			;;
	esac
}

cutover_selected_tag_count() {
	cutover_tag_count
	case "\$cutover_tags" in
		5|6|20) ;;
		*)
			echo "matrix \$matrix is limited to 5, 6, or 20 tags/object" >&2
			exit 2
			;;
	esac
}

case "\$matrix" in
	smoke|sanity-smoke|correctness-smoke)
		envs="BENCH_PROFILE=all OBJECTS=1000 BENCH_BUCKETS=64 CHURN_CYCLES=2 BENCH_PURGE_REQUESTS=10 BENCH_PURGE_KEYS_PER_REQUEST=4 BENCH_CONCURRENT_SECONDS=5 RUNS=1 RUN_XKEY=1 PERF_MODE=auto BENCH_STORAGE=1g VTC_TIMEOUT=900"
		;;
	fellow-smoke)
		check_storage_headroom 1
		check_disk_headroom 1
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_PROFILE=low-fanout-unique OBJECTS=1000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=auto BENCH_STORAGE=1g BENCH_FELLOW_SIZE=1g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=900"
		;;
	fellow-local-cost-100k)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_PROFILE=low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=3600"
		;;
	fellow-local-cost-1m)
		check_storage_headroom 16
		check_disk_headroom 16
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=16g BENCH_FELLOW_SIZE=16g BENCH_HTTP_TIMEOUT=240 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=14400"
		;;
	fellow-shutdown-5m)
		# Shutdown scale probe: intentionally over-resident to grow the
		# Fellow log without requiring every object to remain cached.
		check_storage_headroom 32
		check_disk_headroom 32
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=32g BENCH_FELLOW_SIZE=32g BENCH_HTTP_TIMEOUT=300 BENCH_VALIDATE_RESIDENCY=0 BENCH_ALLOW_LRU_NUKED=1 BENCH_SKIP_PURGE=1 BENCH_WARM_SECONDS=0 VTC_TIMEOUT=43200"
		;;
	fellow-shutdown-10m)
		# Longer shutdown scale probe. Keep this separate from 5M so
		# results and artifacts are named by object count.
		check_storage_headroom 32
		check_disk_headroom 64
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=10000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=32g BENCH_FELLOW_SIZE=64g BENCH_HTTP_TIMEOUT=360 BENCH_VALIDATE_RESIDENCY=0 BENCH_ALLOW_LRU_NUKED=1 BENCH_SKIP_PURGE=1 BENCH_WARM_SECONDS=0 VTC_TIMEOUT=86400"
		;;
	fellow-storage-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=1 BENCH_WORKLOAD_FILTER=noindex_load PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-volatile-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-persistent-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-fanout-storage-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=extreme-high-fanout OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=1 BENCH_WORKLOAD_FILTER=noindex_load PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-fanout-volatile-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=extreme-high-fanout OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-fanout-persistent-attach-100k)
		check_storage_headroom 2
		check_disk_headroom 2
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=extreme-high-fanout OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_FELLOW_SIZE=2g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-storage-attach-1m)
		check_storage_headroom 16
		check_disk_headroom 16
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=1 BENCH_WORKLOAD_FILTER=noindex_load PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=16g BENCH_FELLOW_SIZE=16g BENCH_HTTP_TIMEOUT=240 VTC_TIMEOUT=14400"
		;;
	fellow-volatile-attach-1m)
		check_storage_headroom 16
		check_disk_headroom 16
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=16g BENCH_FELLOW_SIZE=16g BENCH_HTTP_TIMEOUT=240 VTC_TIMEOUT=14400"
		;;
	fellow-persistent-attach-1m)
		check_storage_headroom 16
		check_disk_headroom 16
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=16g BENCH_FELLOW_SIZE=16g BENCH_HTTP_TIMEOUT=240 VTC_TIMEOUT=14400"
		;;
	fellow-storage-attach-5m)
		check_storage_headroom 44
		check_disk_headroom 44
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=1 BENCH_WORKLOAD_FILTER=noindex_load PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_SKIP_PURGE=1 BENCH_STORAGE=44g BENCH_FELLOW_SIZE=44g BENCH_HTTP_TIMEOUT=300 VTC_TIMEOUT=43200"
		;;
	fellow-volatile-attach-5m)
		check_storage_headroom 44
		check_disk_headroom 44
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=0 BENCH_PROFILE=low-fanout-unique OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_SKIP_PURGE=1 BENCH_STORAGE=44g BENCH_FELLOW_SIZE=44g BENCH_HTTP_TIMEOUT=300 VTC_TIMEOUT=43200"
		;;
	fellow-persistent-attach-5m)
		check_storage_headroom 44
		check_disk_headroom 44
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=low-fanout-unique OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_SKIP_PURGE=1 BENCH_STORAGE=44g BENCH_FELLOW_SIZE=44g BENCH_HTTP_TIMEOUT=300 VTC_TIMEOUT=43200"
		;;
	fellow-restart-idle-memory)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=fellow-restart-idle-memory BENCH_RESTART_TAG_PROFILE=low-fanout-unique BENCH_RESTART_TOUCH_PERCENT=10 OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_FELLOW_BLOCK_SIZE=8KB BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-restart-idle-memory-1m)
		check_storage_headroom 16
		check_disk_headroom 16
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=fellow-restart-idle-memory BENCH_RESTART_TAG_PROFILE=low-fanout-unique BENCH_RESTART_TOUCH_PERCENT=10 OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=16g BENCH_FELLOW_SIZE=16g BENCH_FELLOW_BLOCK_SIZE=8KB BENCH_HTTP_TIMEOUT=240 VTC_TIMEOUT=14400"
		;;
	fellow-restart-first-touch)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=fellow-restart-first-touch BENCH_RESTART_TAG_PROFILE=low-fanout-unique BENCH_RESTART_TOUCH_PERCENT=10 OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_FELLOW_BLOCK_SIZE=8KB BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-restart-cold-purge)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=fellow-restart-cold-purge BENCH_RESTART_TAG_PROFILE=low-fanout-unique BENCH_RESTART_TOUCH_PERCENT=10 OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_FELLOW_BLOCK_SIZE=8KB BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	fellow-restart-hot-purge)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=grouped BENCH_PROFILE=fellow-restart-hot-purge BENCH_RESTART_TAG_PROFILE=low-fanout-unique BENCH_RESTART_TOUCH_PERCENT=10 BENCH_CACHE_TAG_SWEEP_INTERVAL=0s OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_FELLOW_BLOCK_SIZE=8KB BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=1800"
		;;
	stream1-boundary-control-125k)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=0 BENCH_STREAM1_EXPECT_CHECKPOINT=initial-only BENCH_PROFILE=populated-map-warm BENCH_POPULATED_MAP_ENTRIES=125002 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=14400"
		;;
	stream1-boundary-capped-125k)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=100000 BENCH_STREAM1_EXPECT_CHECKPOINT=retained BENCH_PROFILE=populated-map-warm BENCH_POPULATED_MAP_ENTRIES=125002 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=14400"
		;;
	stream1-default-control-1500k)
		check_storage_headroom 8
		check_disk_headroom 8
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=0 BENCH_STREAM1_EXPECT_CHECKPOINT=retained BENCH_PROFILE=populated-map-warm BENCH_POPULATED_MAP_ENTRIES=1500003 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_FELLOW_SIZE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=43200"
		;;
	stream1-default-capped-1500k)
		check_storage_headroom 8
		check_disk_headroom 8
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=1000000 BENCH_STREAM1_EXPECT_CHECKPOINT=retained BENCH_PROFILE=populated-map-warm BENCH_POPULATED_MAP_ENTRIES=1500003 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_FELLOW_SIZE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=43200"
		;;
	stream1-overlap-control-100k)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=0 BENCH_STREAM1_EXPECT_CHECKPOINT=initial-only BENCH_PROFILE=stream1-checkpoint-overlap BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES=100000 BENCH_STREAM1_OVERLAP_READS=50000 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 BENCH_CONCURRENT_READERS=8 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=14400"
		;;
	stream1-overlap-capped-100k)
		check_storage_headroom 4
		check_disk_headroom 4
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=100000 BENCH_STREAM1_EXPECT_CHECKPOINT=retained BENCH_PROFILE=stream1-checkpoint-overlap BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES=100000 BENCH_STREAM1_OVERLAP_READS=50000 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 BENCH_CONCURRENT_READERS=8 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_FELLOW_SIZE=4g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=14400"
		;;
	stream1-overlap-control-1m)
		check_storage_headroom 8
		check_disk_headroom 8
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=0 BENCH_STREAM1_EXPECT_CHECKPOINT=initial-only BENCH_PROFILE=stream1-checkpoint-overlap BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES=1000000 BENCH_STREAM1_OVERLAP_READS=50000 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 BENCH_CONCURRENT_READERS=8 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_FELLOW_SIZE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=43200"
		;;
	stream1-overlap-capped-1m)
		check_storage_headroom 8
		check_disk_headroom 8
		envs="BENCH_STORAGE_KIND=fellow BENCH_CACHE_TAG_PERSIST=1 BENCH_CACHE_TAG_WAL_FSYNC=strict BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=1000000 BENCH_STREAM1_EXPECT_CHECKPOINT=retained BENCH_PROFILE=stream1-checkpoint-overlap BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES=1000000 BENCH_STREAM1_OVERLAP_READS=50000 OBJECTS=1000 TAGS_PER_OBJECT=1 BENCH_BUCKETS=64 BENCH_CONCURRENT_READERS=8 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_FELLOW_SIZE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=0 BENCH_SYSTEM_SAMPLE_INTERVAL=0.1 VTC_TIMEOUT=43200"
		;;
	buddy-smoke)
		check_storage_headroom 1
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=low-fanout-unique OBJECTS=1000 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=auto BENCH_STORAGE=1g BENCH_BUDDY_SIZE=1g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=1000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=900"
		;;
	buddy-local-cost-attach-100k)
		check_storage_headroom 2
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_BUDDY_SIZE=2g BENCH_HTTP_TIMEOUT=120 VTC_TIMEOUT=1800"
		;;
	buddy-local-cost-warm-100k)
		check_storage_headroom 2
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=required BENCH_STORAGE=2g BENCH_BUDDY_SIZE=2g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=1800"
		;;
	buddy-local-cost-pressure-100k)
		check_storage_headroom 4
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=concurrent OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_BUDDY_SIZE=4g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=2000 BENCH_CONCURRENT_SECONDS=60 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=1800"
		;;
	buddy-local-cost-pressure-paired-100k)
		check_storage_headroom 4
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=concurrent OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=required BENCH_STORAGE=4g BENCH_BUDDY_SIZE=4g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=2000 BENCH_CONCURRENT_SECONDS=60 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=1800"
		;;
	phase4-sweep-default-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=phase4-sweep-latency OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=5 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 BENCH_PURGE_VALIDATE_OBJECTS=4000 BENCH_PURGE_SETTLE_MS=0 BENCH_WARM_SECONDS=5 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=0 BENCH_CONCURRENT_PURGERS=0 BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_PHASE4_PRE_SECONDS=10 BENCH_PHASE4_SWEEP_SECONDS=30 BENCH_PHASE4_POST_SECONDS=10 BENCH_CACHE_TAG_SWEEP_INTERVAL=0s VTC_TIMEOUT=14400"
		;;
	phase4-sweep-buddy-1m)
		check_storage_headroom 8
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=phase4-sweep-latency OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=5 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_BUDDY_SIZE=8g BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 BENCH_PURGE_VALIDATE_OBJECTS=4000 BENCH_PURGE_SETTLE_MS=0 BENCH_WARM_SECONDS=5 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=0 BENCH_CONCURRENT_PURGERS=0 BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_PHASE4_PRE_SECONDS=10 BENCH_PHASE4_SWEEP_SECONDS=30 BENCH_PHASE4_POST_SECONDS=10 BENCH_CACHE_TAG_SWEEP_INTERVAL=0s VTC_TIMEOUT=14400"
		;;
	phase6-fill-drain-default-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=phase6-fill-drain OBJECTS=1000000 TAGS_PER_OBJECT=4 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_HTTP_TIMEOUT=240 BENCH_PHASE6_PRESSURE_BODY_BYTES=4096 BENCH_PHASE6_QUIET_SECONDS=6 VTC_TIMEOUT=14400"
		;;
	phase6-fill-drain-buddy-1m)
		check_storage_headroom 8
		envs="BENCH_STORAGE_KIND=buddy BENCH_PROFILE=phase6-fill-drain OBJECTS=1000000 TAGS_PER_OBJECT=4 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=4g BENCH_BUDDY_SIZE=4g BENCH_HTTP_TIMEOUT=240 BENCH_PHASE6_PRESSURE_BODY_BYTES=4096 BENCH_PHASE6_QUIET_SECONDS=6 VTC_TIMEOUT=14400"
		;;
	phase6-fill-drain-noindex-default-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=phase6-fill-drain BENCH_WORKLOAD_FILTER=noindex_phase6_fill_drain OBJECTS=1000000 TAGS_PER_OBJECT=4 BENCH_BUCKETS=64 RUNS=1 RUN_XKEY=0 RUN_NOINDEX=1 PERF_MODE=required BENCH_STORAGE=4g BENCH_HTTP_TIMEOUT=240 BENCH_PHASE6_PRESSURE_BODY_BYTES=4096 BENCH_PHASE6_QUIET_SECONDS=6 VTC_TIMEOUT=14400"
		;;
	fanout-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=2g VTC_TIMEOUT=1800"
		;;
	all-10k|sanity-10k|correctness-10k)
		check_storage_headroom 1
		envs="BENCH_PROFILE=all OBJECTS=10000 BENCH_BUCKETS=64 CHURN_CYCLES=3 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=1g VTC_TIMEOUT=900"
		;;
	all-100k|pressure-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=uniform-tags,zipfian-tags,cms-entity-list,extreme-high-fanout,low-fanout-unique,bulk-purge-bursts,concurrent OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 BENCH_PURGE_VALIDATE_OBJECTS=2000 BENCH_CONCURRENT_SECONDS=60 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=1800"
		;;
	local-cost-attach-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 VTC_TIMEOUT=1800"
		;;
	local-cost-warm-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=1800"
		;;
	local-cost-pressure-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=concurrent OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=2000 BENCH_CONCURRENT_SECONDS=60 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=1800"
		;;
	local-cost-pressure-paired-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=concurrent OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=2000 BENCH_CONCURRENT_SECONDS=60 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=1800"
		;;
	purgemap-fanout-attach-10m)
		check_storage_headroom 64
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=10000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=0 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=64g BENCH_HTTP_TIMEOUT=180 VTC_TIMEOUT=86400"
		;;
	all-1m|pressure-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=uniform-tags,zipfian-tags,cms-entity-list,extreme-high-fanout,low-fanout-unique,bulk-purge-bursts,concurrent OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 BENCH_PURGE_VALIDATE_OBJECTS=4000 BENCH_CONCURRENT_SECONDS=90 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=14400"
		;;
	local-cost-attach-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_VALIDATE_RESIDENCY=0 BENCH_WARM_SECONDS=0 BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=120 VTC_TIMEOUT=14400"
		;;
	local-cost-warm-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=extreme-high-fanout,low-fanout-unique OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=14400"
		;;
	local-cost-pressure-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=concurrent OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=4000 BENCH_CONCURRENT_SECONDS=90 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=14400"
		;;
	local-cost-pressure-paired-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=concurrent OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 RUN_NOINDEX=1 PERF_MODE=required BENCH_STORAGE=8g BENCH_HTTP_TIMEOUT=120 BENCH_PURGE_VALIDATE_OBJECTS=4000 BENCH_CONCURRENT_SECONDS=90 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=14400"
		;;
	lowfanout-10m)
		if [ "\${CACHE_TAG_BENCH_STORAGE_KIND:-default}" = fellow ]; then
			check_disk_headroom 128
			lowfanout_controls="RUN_XKEY=0 RUN_NOINDEX=0"
		elif [ "\${CACHE_TAG_BENCH_STORAGE_KIND:-default}" = buddy ]; then
			check_storage_headroom 32
			lowfanout_controls="RUN_XKEY=0 RUN_NOINDEX=0"
		else
			check_storage_headroom 32
			lowfanout_controls="RUN_XKEY=1"
		fi
		envs="BENCH_PROFILE=low-fanout-unique OBJECTS=10000000 BENCH_BUCKETS=64 RUNS=3 \$lowfanout_controls PERF_MODE=required BENCH_STORAGE=32g BENCH_FELLOW_SIZE=128g BENCH_FELLOW_BLOCK_SIZE=4KB BENCH_HTTP_TIMEOUT=180 BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 BENCH_WARM_SECONDS=5 VTC_TIMEOUT=86400"
		;;
	all-5m|pressure-5m)
		check_storage_headroom 32
		envs="BENCH_PROFILE=uniform-tags,zipfian-tags,cms-entity-list,extreme-high-fanout,low-fanout-unique,bulk-purge-bursts,concurrent OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=32g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 BENCH_PURGE_VALIDATE_OBJECTS=8000 BENCH_CONCURRENT_SECONDS=120 BENCH_CONCURRENT_READERS=\$pressure_readers BENCH_CONCURRENT_WRITERS=\$pressure_writers BENCH_CONCURRENT_PURGERS=\$pressure_purgers BENCH_CONCURRENT_TARGET_RPS=\$pressure_rps BENCH_CONCURRENT_PURGE_RATE=\$pressure_purge_rate VTC_TIMEOUT=43200"
		;;
	eviction-100k)
		check_storage_headroom 1
		envs="BENCH_PROFILE=eviction OBJECTS=100000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=0 PERF_MODE=required BENCH_STORAGE=1g BENCH_EVICTION_STORAGE=64m BENCH_EVICTION_VALIDATE_OBJECTS=5000 VTC_TIMEOUT=1800"
		;;
	fanout-1m)
		check_storage_headroom 8
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=1000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=8g BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 VTC_TIMEOUT=14400"
		;;
	fanout-5m)
		check_storage_headroom 32
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=5000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=32g BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 VTC_TIMEOUT=43200"
		;;
	fanout-10m)
		check_storage_headroom 64
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=10000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=64g BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 VTC_TIMEOUT=86400"
		;;
	fanout-20m)
		check_storage_headroom 96
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=20000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=96g BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 VTC_TIMEOUT=129600"
		;;
	fanout-30m)
		check_storage_headroom 128
		envs="BENCH_PROFILE=extreme-high-fanout OBJECTS=30000000 BENCH_BUCKETS=64 RUNS=3 RUN_XKEY=1 PERF_MODE=required BENCH_STORAGE=128g BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 VTC_TIMEOUT=172800"
		;;
	churn-deterministic-full-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=rotating-tag-churn-deterministic-full OBJECTS=100000 TAGS_PER_OBJECT=6 BENCH_BUCKETS=64 CHURN_CYCLES=5 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 VTC_TIMEOUT=1800"
		;;
	churn-deterministic-incremental-100k)
		check_storage_headroom 2
		envs="BENCH_PROFILE=rotating-tag-churn-deterministic-incremental OBJECTS=100000 TAGS_PER_OBJECT=6 BENCH_BUCKETS=64 CHURN_CYCLES=5 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=2g BENCH_HTTP_TIMEOUT=120 BENCH_RESIDENCY_VALIDATE_OBJECTS=10000 CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=1 CACHE_TAG_BENCH_TTL=5s VTC_TIMEOUT=1800"
		;;
	churn-deterministic-incremental-1m)
		check_storage_headroom 16
		envs="BENCH_PROFILE=rotating-tag-churn-deterministic-incremental OBJECTS=1000000 TAGS_PER_OBJECT=6 BENCH_BUCKETS=64 CHURN_CYCLES=5 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=16g BENCH_HTTP_TIMEOUT=240 BENCH_RESIDENCY_VALIDATE_OBJECTS=20000 CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=1 CACHE_TAG_BENCH_TTL=5s VTC_TIMEOUT=14400"
		;;
	churn-deterministic-incremental-5m)
		check_storage_headroom 80
		envs="BENCH_PROFILE=rotating-tag-churn-deterministic-incremental OBJECTS=5000000 TAGS_PER_OBJECT=6 BENCH_BUCKETS=64 CHURN_CYCLES=5 RUNS=1 RUN_XKEY=1 RUN_NOINDEX=0 PERF_MODE=required BENCH_STORAGE=80g BENCH_HTTP_TIMEOUT=300 BENCH_RESIDENCY_VALIDATE_OBJECTS=50000 CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=1 CACHE_TAG_BENCH_TTL=5s VTC_TIMEOUT=43200"
		;;
	*)
		echo "unknown matrix: \$matrix" >&2
		exit 2
		;;
esac
envs="SKIP_BUILD=\$CACHE_TAG_SKIP_BUILD \$envs"
envs="BENCH_VINYL_THREAD_POOL_MAX=\$bench_vinyl_thread_pool_max BENCH_VINYL_THREAD_POOLS=2 \$envs"
if [ -n "\$CACHE_TAG_RUNS_OVERRIDE" ]; then
	envs="\$envs RUNS=\$CACHE_TAG_RUNS_OVERRIDE"
fi
if [ -n "\$CACHE_TAG_INSTRUMENT_OBJ_MTX" ]; then
	envs="\$envs BENCH_INSTRUMENT_OBJ_MTX=\$CACHE_TAG_INSTRUMENT_OBJ_MTX"
fi
if [ -n "\$CACHE_TAG_MALLOC_CONF" ]; then
	envs="\$envs BENCH_MALLOC_CONF=\$CACHE_TAG_MALLOC_CONF"
fi
if [ -n "\$CACHE_TAG_MALLOC_ARENA_MAX" ]; then
	envs="\$envs BENCH_MALLOC_ARENA_MAX=\$CACHE_TAG_MALLOC_ARENA_MAX"
fi
if [ -n "\$CACHE_TAG_MALLOC_TRIM_THRESHOLD" ]; then
	envs="\$envs BENCH_MALLOC_TRIM_THRESHOLD_=\$CACHE_TAG_MALLOC_TRIM_THRESHOLD"
fi
if [ -n "\$CACHE_TAG_BENCH_PROFILE" ]; then
	envs="\$envs BENCH_PROFILE=\$CACHE_TAG_BENCH_PROFILE"
fi
if [ -n "\$CACHE_TAG_TAGS_PER_OBJECT" ]; then
	envs="\$envs TAGS_PER_OBJECT=\$CACHE_TAG_TAGS_PER_OBJECT"
fi
if [ -n "\$CACHE_TAG_TAG_LENGTH_CLASS" ]; then
	envs="\$envs BENCH_TAG_LENGTH_CLASS=\$CACHE_TAG_TAG_LENGTH_CLASS"
fi
if [ -n "\$CACHE_TAG_VALIDATE_TAG_SHAPE" ]; then
	envs="\$envs BENCH_VALIDATE_TAG_SHAPE=\$CACHE_TAG_VALIDATE_TAG_SHAPE"
fi
if [ -n "\$CACHE_TAG_BENCH_TTL" ]; then
	envs="\$envs CACHE_TAG_BENCH_TTL=\$CACHE_TAG_BENCH_TTL"
fi
if [ -n "\$CACHE_TAG_CHURN_COMPACT_EACH_CYCLE" ]; then
	envs="\$envs CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=\$CACHE_TAG_CHURN_COMPACT_EACH_CYCLE"
fi
if [ -n "\$churn_cycles_override" ]; then
	envs="\$envs CHURN_CYCLES=\$churn_cycles_override"
fi
if [ -n "\$CACHE_TAG_BENCH_STORAGE_KIND" ]; then
	envs="\$envs BENCH_STORAGE_KIND=\$CACHE_TAG_BENCH_STORAGE_KIND"
fi
if [ -n "\$CACHE_TAG_BUDDY_SIZE" ]; then
	envs="\$envs BENCH_BUDDY_SIZE=\$CACHE_TAG_BUDDY_SIZE"
fi
if [ -n "\$CACHE_TAG_BUDDY_RESERVE_CHUNKS" ]; then
	envs="\$envs BENCH_BUDDY_RESERVE_CHUNKS=\$CACHE_TAG_BUDDY_RESERVE_CHUNKS"
fi
if [ -n "\$CACHE_TAG_FELLOW_SIZE" ]; then
	envs="\$envs BENCH_FELLOW_SIZE=\$CACHE_TAG_FELLOW_SIZE"
fi
if [ -n "\$CACHE_TAG_FELLOW_SEGMENT_SIZE" ]; then
	envs="\$envs BENCH_FELLOW_SEGMENT_SIZE=\$CACHE_TAG_FELLOW_SEGMENT_SIZE"
fi
if [ -n "\$CACHE_TAG_FELLOW_BLOCK_SIZE" ]; then
	envs="\$envs BENCH_FELLOW_BLOCK_SIZE=\$CACHE_TAG_FELLOW_BLOCK_SIZE"
fi
if [ -n "\$CACHE_TAG_CACHE_TAG_PERSIST" ]; then
	envs="\$envs BENCH_CACHE_TAG_PERSIST=\$CACHE_TAG_CACHE_TAG_PERSIST"
fi
if [ -n "\$CACHE_TAG_WAL_FSYNC" ]; then
	envs="\$envs BENCH_CACHE_TAG_WAL_FSYNC=\$CACHE_TAG_WAL_FSYNC"
fi
if [ -n "\$CACHE_TAG_SWEEP_BATCH_OBJECTS" ]; then
	envs="\$envs BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS=\$CACHE_TAG_SWEEP_BATCH_OBJECTS"
fi
if [ -n "\$CACHE_TAG_SWEEP_BATCH_HOLD" ]; then
	envs="\$envs BENCH_CACHE_TAG_SWEEP_BATCH_HOLD=\$CACHE_TAG_SWEEP_BATCH_HOLD"
fi
if [ -n "\$CACHE_TAG_SWEEP_BATCH_YIELD" ]; then
	envs="\$envs BENCH_CACHE_TAG_SWEEP_BATCH_YIELD=\$CACHE_TAG_SWEEP_BATCH_YIELD"
fi
if [ -n "\$CACHE_TAG_SHUTDOWN_DRAIN_SECONDS" ]; then
	envs="\$envs BENCH_SHUTDOWN_DRAIN_SECONDS=\$CACHE_TAG_SHUTDOWN_DRAIN_SECONDS"
fi
if [ -n "\$CACHE_TAG_RUN_NOINDEX" ]; then
	envs="\$envs RUN_NOINDEX=\$CACHE_TAG_RUN_NOINDEX"
fi
if [ -n "\$CACHE_TAG_RUN_XKEY" ]; then
	envs="\$envs RUN_XKEY=\$CACHE_TAG_RUN_XKEY"
fi
if [ -n "\$CACHE_TAG_ALLOW_STALE_AFTER_PURGE" ]; then
	envs="\$envs BENCH_ALLOW_STALE_AFTER_PURGE=\$CACHE_TAG_ALLOW_STALE_AFTER_PURGE"
fi
if [ -n "\$CACHE_TAG_ALLOW_LRU_NUKED" ]; then
	envs="\$envs BENCH_ALLOW_LRU_NUKED=\$CACHE_TAG_ALLOW_LRU_NUKED"
fi
if [ -n "\$CACHE_TAG_PURGE_SETTLE_MS" ]; then
	envs="\$envs BENCH_PURGE_SETTLE_MS=\$CACHE_TAG_PURGE_SETTLE_MS"
fi
if [ -n "\$CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS" ]; then
	envs="\$envs BENCH_PURGE_WINDOW_TIMEOUT_MS=\$CACHE_TAG_PURGE_WINDOW_TIMEOUT_MS"
fi
if [ -n "\$CACHE_TAG_PURGE_WINDOW_CONCURRENCY" ]; then
	envs="\$envs BENCH_PURGE_WINDOW_CONCURRENCY=\$CACHE_TAG_PURGE_WINDOW_CONCURRENCY"
fi
if [ -n "\$CACHE_TAG_BENCH_VALIDATE_RESIDENCY" ]; then
	envs="\$envs BENCH_VALIDATE_RESIDENCY=\$CACHE_TAG_BENCH_VALIDATE_RESIDENCY"
fi
if [ -n "\$CACHE_TAG_BENCH_WARM_SECONDS" ]; then
	envs="\$envs BENCH_WARM_SECONDS=\$CACHE_TAG_BENCH_WARM_SECONDS"
fi
if [ -n "\$CACHE_TAG_BENCH_SKIP_PURGE" ]; then
	envs="\$envs BENCH_SKIP_PURGE=\$CACHE_TAG_BENCH_SKIP_PURGE"
fi
if [ -n "\$CACHE_TAG_BENCH_RESTART_TAG_PROFILE" ]; then
	envs="\$envs BENCH_RESTART_TAG_PROFILE=\$CACHE_TAG_BENCH_RESTART_TAG_PROFILE"
fi
if [ -n "\$CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT" ]; then
	envs="\$envs BENCH_RESTART_TOUCH_PERCENT=\$CACHE_TAG_BENCH_RESTART_TOUCH_PERCENT"
fi
if [ -n "\$CACHE_TAG_VTC_QUIET" ]; then
	envs="\$envs VTC_QUIET=\$CACHE_TAG_VTC_QUIET"
fi
if [ -n "\$CACHE_TAG_BENCHMARK_CONTRACT" ]; then
	envs="\$envs BENCHMARK_CONTRACT=\$CACHE_TAG_BENCHMARK_CONTRACT"
fi
if [ -n "\$CACHE_TAG_BENCH_CPUSET_CPUS" ]; then
	envs="\$envs BENCH_CPUSET_CPUS=\$CACHE_TAG_BENCH_CPUSET_CPUS"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS" ]; then
	envs="\$envs BENCH_DRIVER_CPUSET_CPUS=\$CACHE_TAG_BENCH_DRIVER_CPUSET_CPUS"
fi
if [ -n "\$CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS" ]; then
	envs="\$envs BENCH_BACKEND_CPUSET_CPUS=\$CACHE_TAG_BENCH_BACKEND_CPUSET_CPUS"
fi
if [ -n "\$CACHE_TAG_BENCH_VINYL_CPUSET_CPUS" ]; then
	envs="\$envs BENCH_VINYL_CPUSET_CPUS=\$CACHE_TAG_BENCH_VINYL_CPUSET_CPUS"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED" ]; then
	envs="\$envs BENCH_DRIVER_HEADROOM_REQUIRED=\$CACHE_TAG_BENCH_DRIVER_HEADROOM_REQUIRED"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS" ]; then
	envs="\$envs BENCH_DRIVER_HEADROOM_TARGET_RPS=\$CACHE_TAG_BENCH_DRIVER_HEADROOM_TARGET_RPS"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS" ]; then
	envs="\$envs BENCH_DRIVER_HEADROOM_SECONDS=\$CACHE_TAG_BENCH_DRIVER_HEADROOM_SECONDS"
fi
if [ -n "\$CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS" ]; then
	envs="\$envs BENCH_CONCURRENT_TARGET_RPS=\$CACHE_TAG_BENCH_CONCURRENT_TARGET_RPS"
fi
if [ -n "\$CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS" ]; then
	envs="\$envs BENCH_COMPARISON_MEMORY_ENDPOINTS=\$CACHE_TAG_BENCH_COMPARISON_MEMORY_ENDPOINTS"
fi
if [ -n "\$CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS" ]; then
	envs="\$envs BENCH_MEMORY_POST_LOAD_QUIET_SECONDS=\$CACHE_TAG_BENCH_MEMORY_POST_LOAD_QUIET_SECONDS"
fi
if [ -n "\$CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS" ]; then
	envs="\$envs BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS=\$CACHE_TAG_BENCH_MEMORY_CONFIRMATION_QUIET_SECONDS"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_GOMAXPROCS" ]; then
	envs="\$envs BENCH_DRIVER_GOMAXPROCS=\$CACHE_TAG_BENCH_DRIVER_GOMAXPROCS"
fi
if [ -n "\$CACHE_TAG_BENCH_BACKEND_GOMAXPROCS" ]; then
	envs="\$envs BENCH_BACKEND_GOMAXPROCS=\$CACHE_TAG_BENCH_BACKEND_GOMAXPROCS"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_GOGC" ]; then
	envs="\$envs BENCH_DRIVER_GOGC=\$CACHE_TAG_BENCH_DRIVER_GOGC"
fi
if [ -n "\$CACHE_TAG_BENCH_BACKEND_GOGC" ]; then
	envs="\$envs BENCH_BACKEND_GOGC=\$CACHE_TAG_BENCH_BACKEND_GOGC"
fi
if [ -n "\$CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT" ]; then
	envs="\$envs BENCH_DRIVER_GOMEMLIMIT=\$CACHE_TAG_BENCH_DRIVER_GOMEMLIMIT"
fi
if [ -n "\$CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT" ]; then
	envs="\$envs BENCH_BACKEND_GOMEMLIMIT=\$CACHE_TAG_BENCH_BACKEND_GOMEMLIMIT"
fi
if [ -n "\$CACHE_TAG_BENCH_BACKEND_BODY_BYTES" ]; then
	envs="\$envs BENCH_BACKEND_BODY_BYTES=\$CACHE_TAG_BENCH_BACKEND_BODY_BYTES"
fi
if [ -n "\$CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL" ]; then
	envs="\$envs BENCH_SYSTEM_SAMPLE_INTERVAL=\$CACHE_TAG_BENCH_SYSTEM_SAMPLE_INTERVAL"
fi
if [ -n "\$CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL" ]; then
	envs="\$envs BENCH_DETAILED_MEMORY_INTERVAL=\$CACHE_TAG_BENCH_DETAILED_MEMORY_INTERVAL"
fi
if [ -n "\$CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT" ]; then
	envs="\$envs BENCH_DETAILED_MEMORY_TIMEOUT=\$CACHE_TAG_BENCH_DETAILED_MEMORY_TIMEOUT"
fi
if [ -n "\$CACHE_TAG_BENCH_OBJECTS" ]; then
	envs="\$envs OBJECTS=\$CACHE_TAG_BENCH_OBJECTS"
fi
if [ -n "\$CACHE_TAG_BENCH_BUCKETS" ]; then
	envs="\$envs BENCH_BUCKETS=\$CACHE_TAG_BENCH_BUCKETS"
fi
if [ -n "\$CACHE_TAG_BENCH_STORAGE" ]; then
	envs="\$envs BENCH_STORAGE=\$CACHE_TAG_BENCH_STORAGE"
fi
if [ -n "\$CACHE_TAG_BENCH_HTTP_TIMEOUT" ]; then
	envs="\$envs BENCH_HTTP_TIMEOUT=\$CACHE_TAG_BENCH_HTTP_TIMEOUT"
fi
if [ -n "\$CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS" ]; then
	envs="\$envs BENCH_RESIDENCY_VALIDATE_OBJECTS=\$CACHE_TAG_BENCH_RESIDENCY_VALIDATE_OBJECTS"
fi

# Matrix defaults are deliberately overrideable, but retain only the final
# value for each variable in the reconstructable command record.
envs=\$(
	printf '%s\n' \$envs |
		awk -F= '{ final[\$1] = \$0 } END { for (key in final) print final[key] }' |
		sort |
		paste -sd ' ' -
)

timestamp=\$(date -u +%Y%m%dT%H%M%SZ)
result_id="remote-\$timestamp-\$matrix"
result_dir="\$remote_dir/libvmod-cachetag/benchmarks/results/\$result_id"
mkdir -p "\$result_dir"
printf '%s\n' "\$result_dir" > "\$remote_dir/fetch/last-result-dir"
"\$remote_dir/libvmod-cachetag/benchmarks/capture_system_metadata.sh" "\$result_dir/host-system.env"
{
	printf 'matrix=%s\n' "\$matrix"
	printf 'result_id=%s\n' "\$result_id"
	printf 'bench_clients_auto=%s\n' "\$bench_clients"
	printf 'bench_vinyl_thread_pool_max=%s\n' "\$bench_vinyl_thread_pool_max"
	printf 'bench_vinyl_thread_pools=%s\n' 2
	printf 'bench_vinyl_configured_worker_ceiling=%s\n' "\$((bench_vinyl_thread_pool_max * 2))"
	printf 'logical_cpus=%s\n' "\$cores"
	printf 'physical_cores=%s\n' "\$physical_cores"
	printf 'VTC_QUIET=%s\n' "\${CACHE_TAG_VTC_QUIET:-}"
	printf 'remote_dir=%s\n' "\$remote_dir"
	printf 'docker_command=%s\n' "\$docker_cmd"
	printf 'docker_run_args=%s\n' "\$REMOTE_DOCKER_RUN_ARGS"
	printf 'runs_override=%s\n' "\$CACHE_TAG_RUNS_OVERRIDE"
	printf 'command_env=%s\n' "\$envs"
} > "\$result_dir/remote-run.env"

cd "\$remote_dir/libvmod-cachetag"
export RESULTS_DIR="\$result_dir"
export VINYL_DOCKER_IMAGE
export BENCH_CLIENTS="\$bench_clients"
export DOCKER="\$docker_cmd"
export DOCKER_RUN_ARGS="\$REMOTE_DOCKER_RUN_ARGS"
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD" ]; then
	export BENCH_PERF_RECORD="\$CACHE_TAG_BENCH_PERF_RECORD"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD_SCOPE" ]; then
	export BENCH_PERF_RECORD_SCOPE="\$CACHE_TAG_BENCH_PERF_RECORD_SCOPE"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD_PHASE" ]; then
	export BENCH_PERF_RECORD_PHASE="\$CACHE_TAG_BENCH_PERF_RECORD_PHASE"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD_TARGET" ]; then
	export BENCH_PERF_RECORD_TARGET="\$CACHE_TAG_BENCH_PERF_RECORD_TARGET"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD_RUNS" ]; then
	export BENCH_PERF_RECORD_RUNS="\$CACHE_TAG_BENCH_PERF_RECORD_RUNS"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD" ]; then
	export BENCH_PERF_RECORD_WORKLOAD="\$CACHE_TAG_BENCH_PERF_RECORD_WORKLOAD"
fi
if [ -n "\$CACHE_TAG_BENCH_WORKLOAD_FILTER" ]; then
	export BENCH_WORKLOAD_FILTER="\$CACHE_TAG_BENCH_WORKLOAD_FILTER"
fi
if [ -n "\$CACHE_TAG_VTC_QUIET" ]; then
	export VTC_QUIET="\$CACHE_TAG_VTC_QUIET"
fi
if [ -n "\$CACHE_TAG_BENCH_PERF_FREQ" ]; then
	export BENCH_PERF_FREQ="\$CACHE_TAG_BENCH_PERF_FREQ"
fi
eval "BENCH_MATRIX=\$matrix BENCH_RESULT_ID=\$result_id \$envs scripts/benchmark-cachetag-vmod.sh ../vinyl-cache"
printf '%s\n' "\$result_dir"
EOF
}

matrix_group() {
	case "$1" in
		local-cost-100k)
			printf '%s\n' \
				local-cost-attach-100k \
				local-cost-warm-100k \
				local-cost-pressure-paired-100k
			;;
		buddy-local-cost-100k)
			printf '%s\n' \
				buddy-local-cost-attach-100k \
				buddy-local-cost-warm-100k \
				buddy-local-cost-pressure-paired-100k
			;;
		local-cost-1m)
			printf '%s\n' \
				local-cost-attach-1m \
				local-cost-warm-1m \
				local-cost-pressure-paired-1m
			;;
		backend-local-cost-100k)
			printf '%s\n' \
				local-cost-attach-100k \
				local-cost-warm-100k \
				local-cost-pressure-paired-100k \
				buddy-local-cost-attach-100k \
				buddy-local-cost-warm-100k \
				buddy-local-cost-pressure-paired-100k \
				fellow-local-cost-100k
			;;
		fellow-memory-paired-100k)
			printf '%s\n' \
				fellow-storage-attach-100k \
				fellow-volatile-attach-100k \
				fellow-persistent-attach-100k
			;;
		fellow-memory-fanout-paired-100k)
			printf '%s\n' \
				fellow-fanout-storage-attach-100k \
				fellow-fanout-volatile-attach-100k \
				fellow-fanout-persistent-attach-100k
			;;
		fellow-memory-paired-1m)
			printf '%s\n' \
				fellow-storage-attach-1m \
				fellow-volatile-attach-1m \
				fellow-persistent-attach-1m
			;;
		fellow-memory-paired-5m)
			printf '%s\n' \
				fellow-storage-attach-5m \
				fellow-volatile-attach-5m \
				fellow-persistent-attach-5m
			;;
		regression)
			printf '%s\n' \
				sanity-smoke \
				local-cost-attach-1m \
				local-cost-warm-1m \
				local-cost-pressure-1m \
				churn-deterministic-incremental-100k \
				phase4-sweep-default-1m \
				phase6-fill-drain-default-1m \
				phase6-fill-drain-buddy-1m
			;;
		full)
			matrix_group regression
			printf '%s\n' \
				pressure-100k \
				pressure-1m \
				pressure-5m \
				eviction-100k \
				fellow-restart-idle-memory
			;;
		*)
			printf '%s\n' "$1"
			;;
	esac
}

is_matrix_group() {
	case "$1" in
		regression|full|local-cost-100k|local-cost-1m|buddy-local-cost-100k|backend-local-cost-100k|fellow-memory-paired-100k|fellow-memory-fanout-paired-100k|fellow-memory-paired-1m|fellow-memory-paired-5m) return 0 ;;
		*) return 1 ;;
	esac
}

run_and_fetch() {
	if [ -z "$matrix" ]; then
		echo "missing MATRIX" >&2
		exit 2
	fi
	base_matrix=$matrix
	base_dest=${local_dest:-$default_fetch_dir}
	if [ "$remote_sync" = 1 ]; then
		sync_checkout
	fi
	for child_matrix in $(matrix_group "$base_matrix"); do
		matrix=$child_matrix
		if is_matrix_group "$base_matrix"; then
			local_dest=$base_dest/$child_matrix
		else
			local_dest=$base_dest
		fi
		run_status=0
		fetch_status=0
		run_matrix || run_status=$?
		fetch_latest_result || fetch_status=$?
		if [ "$run_status" -ne 0 ]; then
			exit "$run_status"
		fi
		if [ "$fetch_status" -ne 0 ]; then
			exit "$fetch_status"
		fi
	done
}

fetch_results() {
	if [ -z "$local_dest" ]; then
		local_dest=$default_fetch_dir
	fi
	mkdir -p "$local_dest"
	remote_sh <<'EOF'
set -eu
cd "$REMOTE_DIR/libvmod-cachetag"
mkdir -p "$REMOTE_DIR/fetch"
rm -f "$REMOTE_DIR/fetch"/cachetag-benchmark-results-*.tgz "$REMOTE_DIR/fetch"/cachetag-benchmark-results-*.tgz.sha256
stamp=$(date -u +%Y%m%dT%H%M%SZ)
artifact="$REMOTE_DIR/fetch/cachetag-benchmark-results-all-$stamp.tgz"
tar -czf "$artifact" benchmarks/results
sha256sum "$artifact" > "$artifact.sha256"
EOF
	resolved_remote_dir=$(ssh "$target" "REMOTE_DIR=$(quote "$remote_dir"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; printf '%s\n' \"\$REMOTE_DIR\"")
	rsync -a --checksum "$target:$resolved_remote_dir/fetch/" "$local_dest/"
}

fetch_latest_result() {
	if [ -z "$local_dest" ]; then
		local_dest=$default_fetch_dir
	fi
	mkdir -p "$local_dest"
	remote_sh <<'EOF'
set -eu
cd "$REMOTE_DIR/libvmod-cachetag"
if [ ! -r "$REMOTE_DIR/fetch/last-result-dir" ]; then
	echo "missing last result marker; run a matrix before fetching the latest result" >&2
	exit 1
fi
latest=$(cat "$REMOTE_DIR/fetch/last-result-dir")
case "$latest" in
	"$REMOTE_DIR"/libvmod-cachetag/benchmarks/results/*) ;;
	*) echo "refusing unexpected result path: $latest" >&2; exit 1 ;;
esac
relative=${latest#"$REMOTE_DIR/libvmod-cachetag/"}
mkdir -p "$REMOTE_DIR/fetch"
rm -f "$REMOTE_DIR/fetch"/cachetag-benchmark-results-*.tgz "$REMOTE_DIR/fetch"/cachetag-benchmark-results-*.tgz.sha256
name=$(basename "$latest")
artifact="$REMOTE_DIR/fetch/cachetag-benchmark-results-$name.tgz"
tar -czf "$artifact" "$relative"
sha256sum "$artifact" > "$artifact.sha256"
EOF
	resolved_remote_dir=$(ssh "$target" "REMOTE_DIR=$(quote "$remote_dir"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; printf '%s\n' \"\$REMOTE_DIR\"")
	rsync -a --checksum "$target:$resolved_remote_dir/fetch/" "$local_dest/"
}

case "$cmd" in
	setup)
		install_remote
		sync_checkout
		;;
	run)
		run_and_fetch
		;;
	fetch)
		local_dest=$matrix
		fetch_results
		;;
	all)
		if [ -z "$matrix" ] || [ -z "$local_dest" ]; then
			usage >&2
			exit 2
		fi
		install_remote
		sync_checkout
		remote_sync=0
		run_and_fetch
		;;
	*)
		usage >&2
		exit 2
		;;
esac
