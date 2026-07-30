#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage: scripts/benchmark-cachetag-vmod.sh [VINYL_CACHE_SRC]

Build Vinyl Cache and this repository as a standalone VMOD in Docker, generate
VTC benchmark workloads, and record process metrics plus VMOD/xkey VSC counters.
This script does not copy files into the Vinyl source tree.

Environment:
  VINYL_CACHE_SRC       Vinyl Cache source checkout (default: ../vinyl-cache)
  BUILD_DIR             Host build cache directory (default:
                        /private/tmp/libvmod-cachetag-bench, or /tmp equivalent)
  RESULTS_DIR           Host result directory (default:
                        benchmarks/results/<timestamp>)
  VINYL_DOCKER_IMAGE    Docker image with Vinyl build dependencies
                        (default: vinyl-cache-ubuntu-build)
  DOCKER                Docker command to run the benchmark container
                        (default: docker)
  DOCKER_RUN_ARGS       Extra arguments for docker run, for example
                        '--cap-add PERFMON' for hardware perf counters
                        (default: empty)
  BENCH_MATRIX          Logical matrix name for labels/metadata
                        (default: ad-hoc)
  BENCH_RESULT_ID       Unique result id for labels/metadata
                        (default: result directory name)
  XKEY_SRC              varnish-modules checkout for xkey baseline
                        (default: ../varnish-modules when present)
  RUN_XKEY              1 to build and run xkey baseline, 0 to skip,
                        auto to run when XKEY_SRC exists (default: auto)
  RUN_NOINDEX           1 to run no-index load baseline, 0 to skip
                        (default: 1)
  OBJECTS               Objects to insert per workload (default: 1000)
  TAGS_PER_OBJECT       Tags attached in cachetag workload (default: 4)
  BENCH_PROFILE         explicit-purge, uniform-tags, zipfian-tags,
                        cms-entity-list, extreme-high-fanout,
                        low-fanout-unique, single-shared-tag,
                        single-unique-tag, ten-unique-tags,
                        five-unique-five-shared, cutover-mostly-unique,
                        cutover-mostly-shared, cutover-mixed,
                        short-ttl-high-churn,
                        bulk-purge-bursts, concurrent, purge-storm,
                        purged-cold-residency, populated-map-warm,
                        stream1-checkpoint-overlap,
                        phase6-fill-drain, eviction,
                        all, or a comma-separated profile list
                        (default: explicit-purge)
  BENCH_BUCKETS         Bucket cardinality for shared bucket tags
                        (default: 1024)
  BENCH_CLIENTS         Concurrent HTTP clients for Go driver load phases
                        (default: 1)
  BENCH_WARM_SECONDS    Timed post-load warm-hit phase duration for long-TTL
                        load profiles, 0 to disable (default: 5)
  BENCH_WARM_VALIDATE_HIT
                        Fail warm phase if any warm request is not a hit
                        (default: 1)
  BENCH_RESIDENCY_VALIDATE_OBJECTS
                        Maximum post-load residency probes, 0 means all
                        objects (default: 0)
  BENCH_HTTP_TIMEOUT    Per-request driver timeout in seconds (default: 30)
  BENCH_TAG_UNIVERSE    Tag universe for uniform/Zipfian profiles
                        (default: 10000)
  BENCH_TAG_LENGTH_CLASS
                        Tag text length class for cutover profiles: short,
                        default, or long (default: default)
  BENCH_VALIDATE_TAG_SHAPE
                        1 to fail the Go driver if sampled generated tags do
                        not match the requested tag count and cutover tag-length
                        class (default: 0)
  BENCH_PURGE_REQUESTS  Bulk purge requests per burst (default: 100)
  BENCH_SKIP_PURGE      Generate only load plus shutdown for phased-purge
                        profiles; intended for shutdown probes (default: 0)
  BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT
                        Optional exact persistent Fellow singleton attribute
                        byte expectation for the Stream 6 load-only tripwire
  BENCH_PURGE_KEYS_PER_REQUEST
                        Keys per bulk purge request (default: 10)
  BENCH_PURGE_VALIDATE_OBJECTS
                        Object probes after an accepted cachetag purge
                        (default: 1000)
  BENCH_PURGE_SETTLE_MS
                        Optional delay before post-publication sanity probes
                        (default: 1000)
  BENCH_PURGE_VALIDATION_DELAY_MS
                        Legacy alias for BENCH_PURGE_SETTLE_MS when
                        BENCH_PURGE_SETTLE_MS is unset
  BENCH_PURGE_HIT_RECHECK_DELAY_MS
                        Delay before rechecking first post-settle stale-hit
                        probe
                        (default: 0)
  BENCH_ALLOW_STALE_AFTER_PURGE
                        Record stale-after-purge delivery windows instead of
                        failing the driver on stale hits (default: 0)
  BENCH_ALLOW_LRU_NUKED
                        Do not assert n_lru_nuked == 0 in generated VTCs for
                        over-resident shutdown/log-growth probes (default: 0)
  BENCH_PURGE_WINDOW_TIMEOUT_MS
                        Per-object timeout for post-purge fresh-generation
                        observation (default: 5000)
  BENCH_PURGE_WINDOW_CONCURRENCY
                        Worker concurrency for post-purge fresh-generation
                        probes, 0 for auto (default: 0)
  BENCH_CONCURRENT_SECONDS
                        Concurrent profile duration (default: 30)
  BENCH_CONCURRENT_READERS
                        Concurrent profile reader goroutines (default:
                        BENCH_CLIENTS)
  BENCH_CONCURRENT_WRITERS
                        Concurrent profile writer goroutines (default:
                        derived from BENCH_CLIENTS and insert ratio)
  BENCH_CONCURRENT_PURGERS
                        Concurrent profile purger goroutines (default: 1)
  BENCH_CONCURRENT_TARGET_RPS
                        Aggregate read/write offered RPS, 0 for unbounded
                        (default: 0)
  BENCH_CONCURRENT_PURGE_RATE
                        Concurrent profile purge requests/second (default: 5)
  BENCH_CONCURRENT_INSERT_EVERY
                        Insert every Nth concurrent request (default: 5)
  BENCH_PURGE_STORM_RATE
                        Purge-storm purge requests/second (default:
                        BENCH_CONCURRENT_PURGE_RATE)
  BENCH_PURGE_STORM_DISTINCT
                        Distinct purge keys used by purge-storm
                        (default: 100000)
  BENCH_PURGE_STORM_UNKNOWN_PERCENT
                        Percent of purge-storm keys absent from loaded objects
                        (default: 100)
  BENCH_PURGE_STORM_SOFT_PERCENT
                        Percent of purge-storm requests sent as soft purges
                        (default: 0)
  BENCH_POPULATED_MAP_ENTRIES
                        Unknown-tag purges preseeded before populated-map-warm
                        (default: 1000)
  BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES
                        Exact distinct purges before the overlap trigger
                        (default: 100000)
  BENCH_STREAM1_OVERLAP_READS
                        Exact resident-hit reads released with the trigger
                        purge (default: 50000)
  BENCH_RESIDENCY_SWEEP_SECONDS
                        Seconds to sample object count after cold purge
                        (default: BENCH_CONCURRENT_SECONDS)
  BENCH_RESIDENCY_SAMPLE_MS
                        Object-count sample interval for cold residency
                        (default: 1000)
  BENCH_PHASE4_PRE_SECONDS
                        Read-hit window before the Phase 4 sweep (default: 5)
  BENCH_PHASE4_SWEEP_SECONDS
                        Read-hit window overlapping the Phase 4 purge/sweep
                        (default: 5)
  BENCH_PHASE4_POST_SECONDS
                        Read-hit window after the Phase 4 sweep (default: 5)
  BENCH_PHASE5_HOLD_MS  Phase 5 publication-window duration
                        (profile default)
  BENCH_PHASE5_CAP_PURGES
                        Distinct cap-pressure keys for phase5-held-cap
                        (profile default)
  BENCH_PHASE6_PRESSURE_BODY_BYTES
                        Body size used by the Phase 6 LRU-pressure cycle
                        (default: 4096)
  BENCH_PHASE6_QUIET_SECONDS
                        Quiet observation period after each Phase 6 cycle
                        (default: 6; minimum: 5)
  BENCH_MALLOC_CONF     jemalloc settings appended to vinyltest's existing
                        abort:true,junk:true defaults for benchmarked vinyld
  BENCH_MALLOC_ARENA_MAX
                        Optional glibc MALLOC_ARENA_MAX diagnostic fallback
  BENCH_MALLOC_TRIM_THRESHOLD
                        Optional glibc MALLOC_TRIM_THRESHOLD_ fallback
  BENCH_VALIDATE_RESIDENCY
                        Validate post-load cache residency with hit probes
                        (default: 1)
  BENCH_RESTART_TAG_PROFILE
                        Tag profile used by Fellow restart/demand-load shapes
                        (default: low-fanout-unique)
  BENCH_RESTART_TOUCH_PERCENT
                        Percent of object set first-touched after restart for
                        Fellow restart/demand-load shapes (default: 10)
  BENCH_EVICTION_STORAGE
                        Storage size for eviction profile VTCs (default: 1m)
  BENCH_COLD_RESIDENCY_STORAGE
                        Storage size for purged-cold-residency VTCs
                        (default: BENCH_STORAGE)
  BENCH_BACKEND_BODY_BYTES
                        Origin response body size for non-eviction profiles
                        (default: 2)
  BENCH_EVICTION_BODY_BYTES
                        Origin response body size for eviction profile
                        (default: 4096)
  BENCH_COLD_RESIDENCY_BODY_BYTES
                        Origin response body size for purged-cold-residency,
                        0 to use BENCH_BACKEND_BODY_BYTES (default: 0)
  BENCH_EVICTION_VALIDATE_OBJECTS
                        Eviction validation probe count (default: 1000)
  BENCH_SYSTEM_SAMPLE_INTERVAL
                        Seconds between host utilisation samples in .time
                        files, 0 to disable (default: 1.0)
  BENCH_DETAILED_MEMORY_INTERVAL
                        Seconds between isolated cache-process smaps/maps reads
                        (default: 1.0)
  BENCH_DETAILED_MEMORY_TIMEOUT
                        Seconds before killing a blocked process-detail helper
                        (default: 0.5)
  BENCH_STORAGE         Vinyl storage argument for generated workloads
                        (default: 256m)
  CACHE_TAG_BENCH_TTL   Override generated benchmark VCL TTL for selected
                        profiles (default: profile-specific)
  CACHE_TAG_CHURN_COMPACT_EACH_CYCLE
                        1 to compact after each rotating churn cycle
                        (default: enabled by deterministic incremental lane)
  BENCH_STORAGE_KIND    default for the normal benchmark storage path, fellow
                        for patched Slash/Fellow, or buddy for Slash/Buddy
                        (default: default)
  BENCH_BUDDY_SIZE      Buddy storage size for BENCH_STORAGE_KIND=buddy
                        (default: BENCH_STORAGE)
  BENCH_BUDDY_RESERVE_CHUNKS
                        Buddy reserve_chunks tune value (default: 0)
  SLASH_SRC             Slash source checkout used with Slash-backed storage
                        (default: ../slash)
  BENCH_FELLOW_SIZE     Fellow storage file size (default: BENCH_STORAGE)
  BENCH_FELLOW_SEGMENT_SIZE
                        Fellow segment size (default: 1MB)
  BENCH_FELLOW_BLOCK_SIZE
                        Fellow block size (default: 64KB)
  BENCH_CACHE_TAG_PERSIST
                        1 to persist cachetag metadata in generated cachetag
                        workloads, auto enables it for Fellow (default: auto)
  BENCH_CACHE_TAG_WAL_FSYNC
                        cachetag WAL fsync policy: strict or grouped
                        (default: strict)
  BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES
                        Override purge_history_max_entries in
                        generated cachetag namespaces (default: unset)
  BENCH_CACHE_TAG_SWEEP_INTERVAL
                        Override purge-map sweep_interval in generated
                        cachetag namespaces, for example 0s or 10s
                        (default: unset)
  BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS
                        Override sweep_batch_objects in generated cachetag
                        namespaces (default: VMOD default)
  BENCH_CACHE_TAG_SWEEP_BATCH_HOLD
                        Override sweep_batch_hold in generated cachetag
                        namespaces, for example 2ms or 0.002s
  BENCH_CACHE_TAG_SWEEP_BATCH_YIELD
                        Override sweep_batch_yield in generated cachetag
                        namespaces, for example 1ms or 0.001s
  BENCH_PURGEMAP_EXPECT_REBUILD
                        1 to assert purge-storm rebuilt the same-size purge map
                        rebuild and recovered empty slots (default: unset)
  BENCH_VINYL_THREADS   Vinyl worker thread cap for generated workloads
                        (default: 16)
  CHURN_CYCLES          Load/sleep cycles for short-ttl-high-churn (default: 3)
  RUNS                  Repetitions per workload (default: 3)
  BENCH_WORKLOAD_FILTER Optional exact workload basename to run after generation;
                        a nonempty filter which matches nothing is an error
  SKIP_BUILD            1 to reuse BUILD_DIR after a successful build (default: 0).
                        Reuse is provenance-checked: the run fails if the cached
                        build's recorded source hashes no longer match the
                        mounted sources (benchmarks/rules/BR-016)
  CACHE_TAG_ALLOW_STALE_BUILD
                        1 to downgrade a SKIP_BUILD provenance mismatch to a
                        warning for a deliberate stale-build reuse (default: 0)
  VTC_LOG_BYTES         vinyltest internal log buffer size (default: 20M)
  VTC_TIMEOUT           vinyltest timeout in seconds (default: 300)
  VTC_QUIET             1 to pass -q to vinyltest, 0 to pass -v for full VTC logs
                        (default: 1)
  PERF_MODE             auto, off, or required for hardware counters
                        (default: auto)
  BENCH_PERF_RECORD     1/on/true to wrap selected vinyltest runs in
                        perf record, required to fail if perf is unavailable,
                        off/0 to disable (default: off)
  BENCH_PERF_RECORD_SCOPE
                        command to profile vinyltest and children, or system
                        to add perf record -a (default: command)
  BENCH_PERF_RECORD_PHASE
                        command to profile the full vinyltest command, or load
                        / warm / concurrent to profile only that driver phase
                        (default: command)
  BENCH_PERF_RECORD_TARGET
                        vinyld to profile only the cache process during a
                        phase profile, or descendants to profile the whole
                        vinyltest process tree (default: vinyld)
  BENCH_PERF_RECORD_RUNS
                        Number of runs per workload to record, or all
                        (default: 1)
  BENCH_PERF_RECORD_WORKLOAD
                        Optional workload basename to profile while still
                        running the rest normally (default: empty)
  BENCH_PERF_FREQ       perf record frequency (default: 99)
EOF
}

case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
default_src="$repo_dir/../vinyl-cache"
vinyl_src=${1:-${VINYL_CACHE_SRC:-$default_src}}
vinyl_src=$(CDPATH= cd -- "$vinyl_src" && pwd)

image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}
docker_run_args=${DOCKER_RUN_ARGS:-}
objects=${OBJECTS:-1000}
tags_per_object=${TAGS_PER_OBJECT:-4}
bench_profile=${BENCH_PROFILE:-explicit-purge}
bench_matrix=${BENCH_MATRIX:-ad-hoc}
bench_buckets=${BENCH_BUCKETS:-1024}
bench_clients=${BENCH_CLIENTS:-1}
bench_warm_seconds=${BENCH_WARM_SECONDS:-5}
bench_warm_validate_hit=${BENCH_WARM_VALIDATE_HIT:-1}
bench_residency_validate_objects=${BENCH_RESIDENCY_VALIDATE_OBJECTS:-0}
bench_http_timeout=${BENCH_HTTP_TIMEOUT:-30}
bench_tag_universe=${BENCH_TAG_UNIVERSE:-10000}
bench_tag_length_class=${BENCH_TAG_LENGTH_CLASS:-default}
bench_validate_tag_shape=${BENCH_VALIDATE_TAG_SHAPE:-0}
bench_purge_requests=${BENCH_PURGE_REQUESTS:-100}
bench_skip_purge=${BENCH_SKIP_PURGE:-0}
bench_expect_fellow_attr_bytes_per_object=${BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT:-}
bench_purge_keys_per_request=${BENCH_PURGE_KEYS_PER_REQUEST:-10}
bench_purge_validate_objects=${BENCH_PURGE_VALIDATE_OBJECTS:-1000}
bench_purge_settle_ms=${BENCH_PURGE_SETTLE_MS:-${BENCH_PURGE_VALIDATION_DELAY_MS:-1000}}
bench_purge_validation_delay_ms=${BENCH_PURGE_VALIDATION_DELAY_MS:-}
bench_purge_hit_recheck_delay_ms=${BENCH_PURGE_HIT_RECHECK_DELAY_MS:-0}
bench_allow_stale_after_purge=${BENCH_ALLOW_STALE_AFTER_PURGE:-0}
bench_allow_lru_nuked=${BENCH_ALLOW_LRU_NUKED:-0}
bench_purge_window_timeout_ms=${BENCH_PURGE_WINDOW_TIMEOUT_MS:-5000}
bench_purge_window_concurrency=${BENCH_PURGE_WINDOW_CONCURRENCY:-0}
bench_concurrent_seconds=${BENCH_CONCURRENT_SECONDS:-30}
bench_concurrent_readers=${BENCH_CONCURRENT_READERS:-$bench_clients}
bench_concurrent_writers=${BENCH_CONCURRENT_WRITERS:-}
bench_concurrent_purgers=${BENCH_CONCURRENT_PURGERS:-1}
bench_concurrent_target_rps=${BENCH_CONCURRENT_TARGET_RPS:-0}
bench_concurrent_purge_rate=${BENCH_CONCURRENT_PURGE_RATE:-5}
bench_concurrent_insert_every=${BENCH_CONCURRENT_INSERT_EVERY:-5}
bench_purge_storm_rate=${BENCH_PURGE_STORM_RATE:-$bench_concurrent_purge_rate}
bench_purge_storm_distinct=${BENCH_PURGE_STORM_DISTINCT:-100000}
bench_purge_storm_unknown_percent=${BENCH_PURGE_STORM_UNKNOWN_PERCENT:-100}
bench_purge_storm_soft_percent=${BENCH_PURGE_STORM_SOFT_PERCENT:-0}
bench_populated_map_entries=${BENCH_POPULATED_MAP_ENTRIES:-1000}
bench_stream1_overlap_preseed_entries=${BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES:-100000}
bench_stream1_overlap_reads=${BENCH_STREAM1_OVERLAP_READS:-50000}
bench_residency_sweep_seconds=${BENCH_RESIDENCY_SWEEP_SECONDS:-$bench_concurrent_seconds}
bench_residency_sample_ms=${BENCH_RESIDENCY_SAMPLE_MS:-1000}
bench_phase4_pre_seconds=${BENCH_PHASE4_PRE_SECONDS:-5}
bench_phase4_sweep_seconds=${BENCH_PHASE4_SWEEP_SECONDS:-5}
bench_phase4_post_seconds=${BENCH_PHASE4_POST_SECONDS:-5}
bench_phase4_attribution_guard_ms=${BENCH_PHASE4_ATTRIBUTION_GUARD_MS:-10}
bench_phase5_hold_ms=${BENCH_PHASE5_HOLD_MS:-}
bench_phase5_cap_purges=${BENCH_PHASE5_CAP_PURGES:-}
bench_phase6_pressure_body_bytes=${BENCH_PHASE6_PRESSURE_BODY_BYTES:-4096}
bench_phase6_quiet_seconds=${BENCH_PHASE6_QUIET_SECONDS:-6}
bench_malloc_conf=${BENCH_MALLOC_CONF:-}
bench_malloc_arena_max=${BENCH_MALLOC_ARENA_MAX:-}
bench_malloc_trim_threshold=${BENCH_MALLOC_TRIM_THRESHOLD_:-${BENCH_MALLOC_TRIM_THRESHOLD:-}}
bench_instrument_obj_mtx=${BENCH_INSTRUMENT_OBJ_MTX:-0}
bench_validate_residency=${BENCH_VALIDATE_RESIDENCY:-1}
bench_restart_tag_profile=${BENCH_RESTART_TAG_PROFILE:-low-fanout-unique}
bench_restart_touch_percent=${BENCH_RESTART_TOUCH_PERCENT:-10}
bench_eviction_storage=${BENCH_EVICTION_STORAGE:-1m}
bench_backend_body_bytes=${BENCH_BACKEND_BODY_BYTES:-2}
bench_eviction_body_bytes=${BENCH_EVICTION_BODY_BYTES:-4096}
bench_cold_residency_body_bytes=${BENCH_COLD_RESIDENCY_BODY_BYTES:-0}
bench_eviction_validate_objects=${BENCH_EVICTION_VALIDATE_OBJECTS:-1000}
bench_http_disable_keepalives=${BENCH_HTTP_DISABLE_KEEPALIVES:-}
bench_system_sample_interval=${BENCH_SYSTEM_SAMPLE_INTERVAL:-1.0}
bench_detailed_memory_interval=${BENCH_DETAILED_MEMORY_INTERVAL:-1.0}
bench_detailed_memory_timeout=${BENCH_DETAILED_MEMORY_TIMEOUT:-0.5}
bench_storage=${BENCH_STORAGE:-256m}
bench_cold_residency_storage=${BENCH_COLD_RESIDENCY_STORAGE:-$bench_storage}
cache_tag_bench_ttl=${CACHE_TAG_BENCH_TTL:-}
cache_tag_churn_compact_each_cycle=${CACHE_TAG_CHURN_COMPACT_EACH_CYCLE:-0}
bench_storage_kind=${BENCH_STORAGE_KIND:-default}
bench_fellow_size=${BENCH_FELLOW_SIZE:-$bench_storage}
bench_fellow_segment_size=${BENCH_FELLOW_SEGMENT_SIZE:-1MB}
bench_fellow_block_size=${BENCH_FELLOW_BLOCK_SIZE:-64KB}
bench_buddy_size=${BENCH_BUDDY_SIZE:-$bench_storage}
bench_buddy_reserve_chunks=${BENCH_BUDDY_RESERVE_CHUNKS:-0}
bench_timeout_idle=${BENCH_TIMEOUT_IDLE:-}
bench_backend_idle_timeout=${BENCH_BACKEND_IDLE_TIMEOUT:-}
bench_cache_tag_persist=${BENCH_CACHE_TAG_PERSIST:-auto}
bench_cache_tag_wal_fsync=${BENCH_CACHE_TAG_WAL_FSYNC:-strict}
bench_cache_tag_purge_history_max_entries=${BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES:-}
bench_stream1_expect_checkpoint=${BENCH_STREAM1_EXPECT_CHECKPOINT:-}
bench_cache_tag_sweep_interval=${BENCH_CACHE_TAG_SWEEP_INTERVAL:-}
bench_cache_tag_sweep_batch_objects=${BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS:-}
bench_cache_tag_sweep_batch_hold=${BENCH_CACHE_TAG_SWEEP_BATCH_HOLD:-}
bench_cache_tag_sweep_batch_yield=${BENCH_CACHE_TAG_SWEEP_BATCH_YIELD:-}
if [ -z "$bench_cache_tag_sweep_interval" ] &&
	case ",$bench_profile," in
		*,fellow-restart-hot-purge,*) true ;;
		*) false ;;
	esac
then
	bench_cache_tag_sweep_interval=0s
fi
bench_purgemap_expect_rebuild=${BENCH_PURGEMAP_EXPECT_REBUILD:-}
bench_shutdown_drain_seconds=${BENCH_SHUTDOWN_DRAIN_SECONDS:-}
bench_vinyl_threads=${BENCH_VINYL_THREADS:-16}
churn_cycles_default=3
case ",$bench_profile," in
	*,phase6-fill-drain,*) churn_cycles_default=10 ;;
esac
churn_cycles=${CHURN_CYCLES:-$churn_cycles_default}
runs=${RUNS:-3}
if [ -n "${RUN_NOINDEX+x}" ]; then
	run_noindex=$RUN_NOINDEX
else
	case ",$bench_profile," in
		*,phase6-fill-drain,*) run_noindex=0 ;;
		*) run_noindex=1 ;;
	esac
fi
bench_workload_filter=${BENCH_WORKLOAD_FILTER:-}
skip_build=${SKIP_BUILD:-0}
allow_stale_build=${CACHE_TAG_ALLOW_STALE_BUILD:-0}
vtc_log_bytes=${VTC_LOG_BYTES:-20M}
vtc_timeout=${VTC_TIMEOUT:-300}
vtc_quiet=${VTC_QUIET:-1}
perf_mode=${PERF_MODE:-auto}
bench_perf_record=${BENCH_PERF_RECORD:-off}
bench_perf_record_scope=${BENCH_PERF_RECORD_SCOPE:-command}
bench_perf_record_phase=${BENCH_PERF_RECORD_PHASE:-command}
bench_perf_record_target=${BENCH_PERF_RECORD_TARGET:-vinyld}
bench_perf_record_runs=${BENCH_PERF_RECORD_RUNS:-1}
bench_perf_record_workload=${BENCH_PERF_RECORD_WORKLOAD:-}
bench_perf_freq=${BENCH_PERF_FREQ:-99}
xkey_src=${XKEY_SRC:-"$repo_dir/../varnish-modules"}
run_xkey=${RUN_XKEY:-auto}
if [ "$run_xkey" = auto ]; then
	if [ -d "$xkey_src/src" ]; then
		run_xkey=1
	else
		run_xkey=0
	fi
fi
if [ "$run_xkey" = 1 ]; then
	xkey_src=$(CDPATH= cd -- "$xkey_src" && pwd)
else
	xkey_src=$repo_dir
fi
case "$run_noindex" in
	0|1) ;;
	*)
		echo "RUN_NOINDEX must be 0 or 1" >&2
		exit 2
		;;
esac
if [ -z "$bench_shutdown_drain_seconds" ]; then
	if [ "$bench_storage_kind" = fellow ]; then
		bench_shutdown_drain_seconds=2
	else
		bench_shutdown_drain_seconds=0
	fi
fi
if [ -z "$bench_timeout_idle" ]; then
	if [ "$bench_storage_kind" = fellow ]; then
		bench_timeout_idle=1
	else
		bench_timeout_idle=
	fi
fi
if [ -z "$bench_backend_idle_timeout" ]; then
	if [ "$bench_storage_kind" = fellow ]; then
		bench_backend_idle_timeout=1
	else
		bench_backend_idle_timeout=
	fi
fi
if [ -z "$bench_http_disable_keepalives" ]; then
	bench_http_disable_keepalives=0
fi

case "$bench_storage_kind" in
	default|fellow|buddy) ;;
	*)
		echo "BENCH_STORAGE_KIND must be default, fellow, or buddy" >&2
		exit 2
		;;
esac
case "$bench_malloc_conf" in
	*[!A-Za-z0-9_:,.-]*)
		echo "BENCH_MALLOC_CONF contains unsupported characters" >&2
		exit 2
		;;
esac
case "$bench_malloc_arena_max:$bench_malloc_trim_threshold" in
	*[!0-9:-]*)
		echo "allocator fallback knobs must be integers" >&2
		exit 2
		;;
esac
case "$bench_buddy_reserve_chunks" in
	""|*[!0-9]*)
		echo "BENCH_BUDDY_RESERVE_CHUNKS must be a non-negative integer" >&2
		exit 2
		;;
esac
case "$vtc_quiet" in
	0|1) ;;
	*)
		echo "VTC_QUIET must be 0 or 1" >&2
		exit 2
		;;
esac
case "$bench_cache_tag_wal_fsync" in
	strict|grouped) ;;
	*)
		echo "BENCH_CACHE_TAG_WAL_FSYNC must be strict or grouped" >&2
		exit 2
		;;
esac
case "$bench_tag_length_class" in
	short|default|long) ;;
	*)
		echo "BENCH_TAG_LENGTH_CLASS must be short, default, or long" >&2
		exit 2
		;;
esac
case "$bench_validate_tag_shape" in
	0|1|true|false|yes|no|on|off) ;;
	*)
		echo "BENCH_VALIDATE_TAG_SHAPE must be boolean" >&2
		exit 2
		;;
esac
case "$bench_expect_fellow_attr_bytes_per_object" in
	""|*[!0-9]*)
		if [ -n "$bench_expect_fellow_attr_bytes_per_object" ]; then
			echo "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT must be a positive integer" >&2
			exit 2
		fi
		;;
	0)
		echo "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT must be a positive integer" >&2
		exit 2
		;;
esac
case "$bench_cache_tag_purge_history_max_entries" in
	""|*[!0-9]*)
		if [ -n "$bench_cache_tag_purge_history_max_entries" ]; then
			echo "BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES must be a non-negative integer" >&2
			exit 2
		fi
		;;
esac
case "$bench_stream1_expect_checkpoint" in
	""|initial-only|retained) ;;
	*)
		echo "BENCH_STREAM1_EXPECT_CHECKPOINT must be initial-only, retained, or unset" >&2
		exit 2
		;;
esac
case "$bench_stream1_overlap_preseed_entries:$bench_stream1_overlap_reads" in
	*[!0-9:]*|0:*|*:0)
		echo "BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES and BENCH_STREAM1_OVERLAP_READS must be positive integers" >&2
		exit 2
		;;
esac
case "$bench_cache_tag_sweep_batch_objects" in
	""|*[!0-9]*)
		if [ -n "$bench_cache_tag_sweep_batch_objects" ]; then
			echo "BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS must be a positive integer" >&2
			exit 2
		fi
		;;
	0)
		echo "BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS must be a positive integer" >&2
		exit 2
		;;
esac
case "$bench_purgemap_expect_rebuild" in
	""|0|1|true|false|yes|no|on|off) ;;
	*)
		echo "BENCH_PURGEMAP_EXPECT_REBUILD must be boolean-like" >&2
		exit 2
		;;
esac
case "$cache_tag_churn_compact_each_cycle" in
	0|1) ;;
	*)
		echo "CACHE_TAG_CHURN_COMPACT_EACH_CYCLE must be 0 or 1" >&2
		exit 2
		;;
esac
if [ "$bench_cache_tag_persist" = auto ]; then
	if [ "$bench_storage_kind" = fellow ]; then
		bench_cache_tag_persist=1
	else
		bench_cache_tag_persist=0
	fi
fi
case "$bench_cache_tag_persist" in
	0|1) ;;
	*)
		echo "BENCH_CACHE_TAG_PERSIST must be auto, 0, or 1" >&2
		exit 2
		;;
esac
slash_mount_args=
if [ "$bench_storage_kind" = fellow ] || [ "$bench_storage_kind" = buddy ]; then
	default_slash_src="$repo_dir/../slash"
	slash_src=${SLASH_SRC:-$default_slash_src}
	slash_src=$(CDPATH= cd -- "$slash_src" && pwd)
	case "$slash_src" in
		*[[:space:]]*)
			echo "SLASH_SRC path must not contain whitespace: $slash_src" >&2
			exit 2
			;;
	esac
	slash_mount_args="-v $slash_src:/slash-host:ro"
else
	slash_src=
fi

build_dir=${BUILD_DIR:-}
if [ -z "$build_dir" ]; then
	if [ -d /private/tmp ]; then
		build_dir=/private/tmp/libvmod-cachetag-bench
	else
		build_dir=/tmp/libvmod-cachetag-bench
	fi
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
results_dir=${RESULTS_DIR:-"$repo_dir/benchmarks/results/$timestamp"}
mkdir -p "$build_dir" "$results_dir"
bench_result_id=${BENCH_RESULT_ID:-$(basename "$results_dir")}
bench_branch=$(git -C "$repo_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || printf unknown)
docker_cidfile="$results_dir/docker.cid"
rm -f "$docker_cidfile"

cleanup_container() {
	if [ -r "$docker_cidfile" ]; then
		cid=$(cat "$docker_cidfile" 2>/dev/null || true)
		if [ -n "$cid" ]; then
			$docker_cmd stop "$cid" >/dev/null 2>&1 || true
		fi
	fi
}
trap cleanup_container INT TERM HUP EXIT

$docker_cmd run $docker_run_args --rm \
	--cidfile "$docker_cidfile" \
	--label org.cachetag.benchmark=1 \
	--label "org.cachetag.benchmark.matrix=$bench_matrix" \
	--label "org.cachetag.benchmark.result_id=$bench_result_id" \
	--label "org.cachetag.benchmark.branch=$bench_branch" \
	-v "$vinyl_src:/vinyl-src:ro" \
	$slash_mount_args \
	-v "$repo_dir:/cachetag-host:ro" \
	-v "$xkey_src:/xkey-src:ro" \
	-v "$build_dir:/work" \
	-v "$results_dir:/results" \
	-e "OBJECTS=$objects" \
	-e "TAGS_PER_OBJECT=$tags_per_object" \
	-e "BENCH_PROFILE=$bench_profile" \
	-e "BENCH_MATRIX=$bench_matrix" \
	-e "BENCH_RESULT_ID=$bench_result_id" \
	-e "BENCH_BUCKETS=$bench_buckets" \
	-e "BENCH_CLIENTS=$bench_clients" \
	-e "BENCH_WARM_SECONDS=$bench_warm_seconds" \
	-e "BENCH_WARM_VALIDATE_HIT=$bench_warm_validate_hit" \
	-e "BENCH_RESIDENCY_VALIDATE_OBJECTS=$bench_residency_validate_objects" \
	-e "BENCH_HTTP_TIMEOUT=$bench_http_timeout" \
	-e "BENCH_TAG_UNIVERSE=$bench_tag_universe" \
	-e "BENCH_TAG_LENGTH_CLASS=$bench_tag_length_class" \
	-e "BENCH_VALIDATE_TAG_SHAPE=$bench_validate_tag_shape" \
	-e "BENCH_PURGE_REQUESTS=$bench_purge_requests" \
	-e "BENCH_SKIP_PURGE=$bench_skip_purge" \
	-e "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT=$bench_expect_fellow_attr_bytes_per_object" \
	-e "BENCH_PURGE_KEYS_PER_REQUEST=$bench_purge_keys_per_request" \
	-e "BENCH_PURGE_VALIDATE_OBJECTS=$bench_purge_validate_objects" \
	-e "BENCH_PURGE_SETTLE_MS=$bench_purge_settle_ms" \
	-e "BENCH_PURGE_VALIDATION_DELAY_MS=$bench_purge_validation_delay_ms" \
	-e "BENCH_PURGE_HIT_RECHECK_DELAY_MS=$bench_purge_hit_recheck_delay_ms" \
	-e "BENCH_ALLOW_STALE_AFTER_PURGE=$bench_allow_stale_after_purge" \
	-e "BENCH_ALLOW_LRU_NUKED=$bench_allow_lru_nuked" \
	-e "BENCH_PURGE_WINDOW_TIMEOUT_MS=$bench_purge_window_timeout_ms" \
	-e "BENCH_PURGE_WINDOW_CONCURRENCY=$bench_purge_window_concurrency" \
	-e "BENCH_CONCURRENT_SECONDS=$bench_concurrent_seconds" \
	-e "BENCH_CONCURRENT_READERS=$bench_concurrent_readers" \
	-e "BENCH_CONCURRENT_WRITERS=$bench_concurrent_writers" \
	-e "BENCH_CONCURRENT_PURGERS=$bench_concurrent_purgers" \
	-e "BENCH_CONCURRENT_TARGET_RPS=$bench_concurrent_target_rps" \
	-e "BENCH_CONCURRENT_PURGE_RATE=$bench_concurrent_purge_rate" \
	-e "BENCH_CONCURRENT_INSERT_EVERY=$bench_concurrent_insert_every" \
	-e "BENCH_PURGE_STORM_RATE=$bench_purge_storm_rate" \
	-e "BENCH_PURGE_STORM_DISTINCT=$bench_purge_storm_distinct" \
	-e "BENCH_PURGE_STORM_UNKNOWN_PERCENT=$bench_purge_storm_unknown_percent" \
	-e "BENCH_PURGE_STORM_SOFT_PERCENT=$bench_purge_storm_soft_percent" \
	-e "BENCH_POPULATED_MAP_ENTRIES=$bench_populated_map_entries" \
	-e "BENCH_RESIDENCY_SWEEP_SECONDS=$bench_residency_sweep_seconds" \
	-e "BENCH_RESIDENCY_SAMPLE_MS=$bench_residency_sample_ms" \
	-e "BENCH_PHASE4_PRE_SECONDS=$bench_phase4_pre_seconds" \
	-e "BENCH_PHASE4_SWEEP_SECONDS=$bench_phase4_sweep_seconds" \
	-e "BENCH_PHASE4_POST_SECONDS=$bench_phase4_post_seconds" \
	-e "BENCH_PHASE4_ATTRIBUTION_GUARD_MS=$bench_phase4_attribution_guard_ms" \
	-e "BENCH_PHASE5_HOLD_MS=$bench_phase5_hold_ms" \
	-e "BENCH_PHASE5_CAP_PURGES=$bench_phase5_cap_purges" \
	-e "BENCH_PHASE6_PRESSURE_BODY_BYTES=$bench_phase6_pressure_body_bytes" \
	-e "BENCH_PHASE6_QUIET_SECONDS=$bench_phase6_quiet_seconds" \
	-e "BENCH_MALLOC_CONF=$bench_malloc_conf" \
	-e "BENCH_MALLOC_ARENA_MAX=$bench_malloc_arena_max" \
	-e "BENCH_MALLOC_TRIM_THRESHOLD_=$bench_malloc_trim_threshold" \
        -e "BENCH_INSTRUMENT_OBJ_MTX=$bench_instrument_obj_mtx" \
        -e "CACHE_TAG_BENCH_INSTRUMENT_OBJ_MTX=$bench_instrument_obj_mtx" \
	-e "BENCH_VALIDATE_RESIDENCY=$bench_validate_residency" \
	-e "BENCH_RESTART_TAG_PROFILE=$bench_restart_tag_profile" \
	-e "BENCH_RESTART_TOUCH_PERCENT=$bench_restart_touch_percent" \
	-e "BENCH_EVICTION_STORAGE=$bench_eviction_storage" \
	-e "BENCH_COLD_RESIDENCY_STORAGE=$bench_cold_residency_storage" \
	-e "BENCH_BACKEND_BODY_BYTES=$bench_backend_body_bytes" \
	-e "BENCH_EVICTION_BODY_BYTES=$bench_eviction_body_bytes" \
	-e "BENCH_COLD_RESIDENCY_BODY_BYTES=$bench_cold_residency_body_bytes" \
	-e "BENCH_EVICTION_VALIDATE_OBJECTS=$bench_eviction_validate_objects" \
	-e "BENCH_HTTP_DISABLE_KEEPALIVES=$bench_http_disable_keepalives" \
	-e "BENCH_SYSTEM_SAMPLE_INTERVAL=$bench_system_sample_interval" \
	-e "BENCH_DETAILED_MEMORY_INTERVAL=$bench_detailed_memory_interval" \
	-e "BENCH_DETAILED_MEMORY_TIMEOUT=$bench_detailed_memory_timeout" \
	-e "BENCH_STORAGE=$bench_storage" \
	-e "CACHE_TAG_BENCH_TTL=$cache_tag_bench_ttl" \
	-e "CACHE_TAG_CHURN_COMPACT_EACH_CYCLE=$cache_tag_churn_compact_each_cycle" \
	-e "BENCH_STORAGE_KIND=$bench_storage_kind" \
		-e "BENCH_FELLOW_SIZE=$bench_fellow_size" \
		-e "BENCH_FELLOW_SEGMENT_SIZE=$bench_fellow_segment_size" \
		-e "BENCH_FELLOW_BLOCK_SIZE=$bench_fellow_block_size" \
		-e "BENCH_BUDDY_SIZE=$bench_buddy_size" \
		-e "BENCH_BUDDY_RESERVE_CHUNKS=$bench_buddy_reserve_chunks" \
		-e "BENCH_TIMEOUT_IDLE=$bench_timeout_idle" \
	-e "BENCH_BACKEND_IDLE_TIMEOUT=$bench_backend_idle_timeout" \
	-e "BENCH_CACHE_TAG_PERSIST=$bench_cache_tag_persist" \
		-e "BENCH_CACHE_TAG_WAL_FSYNC=$bench_cache_tag_wal_fsync" \
		-e "BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES=$bench_cache_tag_purge_history_max_entries" \
	-e "BENCH_STREAM1_EXPECT_CHECKPOINT=$bench_stream1_expect_checkpoint" \
	-e "BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES=$bench_stream1_overlap_preseed_entries" \
	-e "BENCH_STREAM1_OVERLAP_READS=$bench_stream1_overlap_reads" \
		-e "BENCH_CACHE_TAG_SWEEP_INTERVAL=$bench_cache_tag_sweep_interval" \
		-e "BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS=$bench_cache_tag_sweep_batch_objects" \
		-e "BENCH_CACHE_TAG_SWEEP_BATCH_HOLD=$bench_cache_tag_sweep_batch_hold" \
		-e "BENCH_CACHE_TAG_SWEEP_BATCH_YIELD=$bench_cache_tag_sweep_batch_yield" \
		-e "BENCH_PURGEMAP_EXPECT_REBUILD=$bench_purgemap_expect_rebuild" \
	-e "BENCH_SHUTDOWN_DRAIN_SECONDS=$bench_shutdown_drain_seconds" \
	-e "BENCH_VINYL_THREADS=$bench_vinyl_threads" \
	-e "CHURN_CYCLES=$churn_cycles" \
	-e "RUNS=$runs" \
	-e "BENCH_WORKLOAD_FILTER=$bench_workload_filter" \
	-e "SKIP_BUILD=$skip_build" \
	-e "CACHE_TAG_ALLOW_STALE_BUILD=$allow_stale_build" \
	-e "RUN_XKEY=$run_xkey" \
	-e "RUN_NOINDEX=$run_noindex" \
	-e "VTC_LOG_BYTES=$vtc_log_bytes" \
	-e "VTC_TIMEOUT=$vtc_timeout" \
	-e "VTC_QUIET=$vtc_quiet" \
	-e "PERF_MODE=$perf_mode" \
	-e "BENCH_PERF_RECORD=$bench_perf_record" \
	-e "BENCH_PERF_RECORD_SCOPE=$bench_perf_record_scope" \
	-e "BENCH_PERF_RECORD_PHASE=$bench_perf_record_phase" \
	-e "BENCH_PERF_RECORD_TARGET=$bench_perf_record_target" \
	-e "BENCH_PERF_RECORD_RUNS=$bench_perf_record_runs" \
	-e "BENCH_PERF_RECORD_WORKLOAD=$bench_perf_record_workload" \
	-e "BENCH_PERF_FREQ=$bench_perf_freq" \
	"$image" \
bash -lc '
set -euo pipefail
trap "chmod -R a+rX /results 2>/dev/null || true" EXIT

prefix=/work/prefix
vinyl_build=/work/vinyl-build
vinyl_src_copy=/work/vinyl-src-copy
slash_src=/work/slash-src
cachetag_src=/work/cachetag-src

if [ "${SKIP_BUILD}" != 1 ]; then
	rm -rf "$prefix" "$vinyl_build" "$vinyl_src_copy" "$slash_src" "$cachetag_src"
	mkdir -p "$prefix" "$vinyl_build" "$vinyl_src_copy" "$slash_src" "$cachetag_src"

	tar -C /vinyl-src -cf - . | tar -C "$vinyl_src_copy" -xf -

	cd "$vinyl_build"
	(
		cd "$vinyl_src_copy"
		sh ./autogen.sh
	)
	"$vinyl_src_copy"/configure --prefix="$prefix" --with-unwind \
		--enable-developer-warnings --enable-debugging-symbols \
		--disable-stack-protector --with-persistent-storage
	make -j"$(nproc)"
	make install

	export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
	export PATH="$prefix/sbin:$prefix/bin:$PATH"
	export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"

	if [ "${BENCH_STORAGE_KIND}" = fellow ] || [ "${BENCH_STORAGE_KIND}" = buddy ]; then
		tar -C /slash-host --exclude=.git -cf - . | tar -C "$slash_src" -xf -
		cd "$slash_src"
		apply_fellow_patch_stack() {
			local patch_dir=$1
			local patch tmp status
			local -a patches reverse_patches

			shopt -s nullglob
			patches=("$patch_dir"/*.patch)
			shopt -u nullglob
			if [ "${#patches[@]}" -eq 0 ]; then
				return 0
			fi

			reverse_patches=()
			for ((i = ${#patches[@]} - 1; i >= 0; i--)); do
				reverse_patches+=("${patches[$i]}")
			done

			tmp=$(mktemp -d)
			status=0
			tar -C . -cf - . | tar -C "$tmp" -xf -
			(
				cd "$tmp"
				for patch in "${reverse_patches[@]}"; do
					git apply --reverse --check -C0 "$patch" >/dev/null 2>&1 || exit 1
					git apply --reverse -C0 "$patch" >/dev/null 2>&1 || exit 1
				done
				for patch in "${patches[@]}"; do
					git apply --check "$patch" >/dev/null 2>&1 || exit 1
					git apply "$patch" >/dev/null 2>&1 || exit 1
				done
			) || status=$?
			rm -rf "$tmp"
			if [ "$status" -eq 0 ]; then
				printf "slash patch stack already applied: %s (%u patches)\n" "$patch_dir" "${#patches[@]}"
				return 0
			fi

			for patch in "${patches[@]}"; do
				if git apply --check "$patch" >/dev/null 2>&1; then
					git apply "$patch"
				elif git apply --reverse --check -C0 "$patch" >/dev/null 2>&1; then
					printf "slash patch already applied: %s\n" "$(basename "$patch")"
				else
					git apply "$patch"
				fi
			done
		}
		apply_fellow_patch_stack /cachetag-host/patches/fellow
		mkdir -p m4
		cp "$vinyl_src_copy"/m4/ax_*.m4 m4/
		cat > m4/ax_execinfo.m4 <<'"'"'M4EOF'"'"'
AC_DEFUN([AX_EXECINFO], [
	AC_CHECK_HEADERS([execinfo.h])
	AC_SEARCH_LIBS([backtrace], [execinfo], [$1], [$2])
])
M4EOF
		slash_build_cflags="-I$vinyl_build/include -I$vinyl_build/lib/libvsc"
		CPPFLAGS="${CPPFLAGS:-} $slash_build_cflags" \
		CFLAGS="${CFLAGS:-} $slash_build_cflags" \
		VINYLSRC="$vinyl_src_copy" ./bootstrap --prefix="$prefix"
		make -j"$(nproc)"
	fi

	tar -C /cachetag-host \
		--exclude=.git \
		--exclude=Makefile \
		--exclude=Makefile.in \
		--exclude=aclocal.m4 \
		--exclude=autom4te.cache \
		--exclude=build-aux \
		--exclude=config.h \
		--exclude=config.h.in \
		--exclude=config.log \
		--exclude=config.status \
		--exclude=configure \
		--exclude="configure~" \
		--exclude=.deps \
		--exclude=.libs \
		--exclude=benchmarks/results \
		--exclude=benchmarks/remote-results \
		--exclude="_build" \
		--exclude=libtool \
		--exclude="libvmod-cachetag-*" \
		--exclude=m4 \
		--exclude=tests \
		--exclude="*.la" \
		--exclude="*.lo" \
		--exclude="*.o" \
		--exclude="*.tar.gz" \
		-cf - . | tar -C "$cachetag_src" -xf -

	cd "$cachetag_src"
	./bootstrap --prefix="$prefix"
	make -j"$(nproc)"

	provenance_slash=none
	if [ "${BENCH_STORAGE_KIND}" = fellow ] || [ "${BENCH_STORAGE_KIND}" = buddy ]; then
		provenance_slash=/slash-host
	fi
	sh /cachetag-host/benchmarks/build_provenance.sh record \
		/cachetag-host /vinyl-src "$provenance_slash" "$BENCH_STORAGE_KIND" \
		/work/build-provenance.env
else
	test -x "$prefix/bin/vinyltest"
	test -x "$prefix/sbin/vinyld"
	test -f "$cachetag_src/src/.libs/libvmod_cachetag.so"
	if [ "${BENCH_STORAGE_KIND}" = fellow ] || [ "${BENCH_STORAGE_KIND}" = buddy ]; then
		test -f "$slash_src/src/.libs/libvmod_slash.so"
	fi
	provenance_slash=none
	if [ "${BENCH_STORAGE_KIND}" = fellow ] || [ "${BENCH_STORAGE_KIND}" = buddy ]; then
		provenance_slash=/slash-host
	fi
	ALLOW_STALE_BUILD="${CACHE_TAG_ALLOW_STALE_BUILD:-0}" \
		sh /cachetag-host/benchmarks/build_provenance.sh verify \
		/cachetag-host /vinyl-src "$provenance_slash" "$BENCH_STORAGE_KIND" \
		/work/build-provenance.env
fi
if [ -f /work/build-provenance.env ]; then
	cp /work/build-provenance.env /results/build-provenance.env
fi

export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export PATH="$prefix/sbin:$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"

mkdir -p /results/workloads

if ! command -v go >/dev/null 2>&1; then
	echo "the benchmark harness requires Go inside the benchmark container; run scripts/remote-benchmark.sh setup to build the current image" >&2
	exit 1
fi
export GOCACHE=/work/go-cache
mkdir -p "$GOCACHE"
go build -o /work/cachetag-http-workload-driver \
	/cachetag-host/benchmarks/http_workload_driver.go
go build -o /work/cachetag-benchmark-backend \
	/cachetag-host/benchmarks/http_backend.go
driver_command=/work/cachetag-http-workload-driver
backend_command=/work/cachetag-benchmark-backend

if [ "${RUN_XKEY}" = 1 ]; then
	rm -rf /results/xkey-build
	mkdir -p /results/xkey-build/cache
	cd /results/xkey-build
	# Vinyl renamed cache/cache_vinyld.h to cache/cache_int.h upstream
	# (6d36364cc1); accept either so the shim works on 9.0.1 and trunk.
	printf '%s\n' \
		'#if defined(__has_include) && __has_include(<cache/cache_int.h>)' \
		'#  include <cache/cache_int.h>' \
		'#else' \
		'#  include <cache/cache_vinyld.h>' \
		'#endif' > cache/cache_varnishd.h
	python3 /vinyl-src/lib/libvcc/vmodtool.py --strict --boilerplate \
		-o vcc_xkey_if /xkey-src/src/vmod_xkey.vcc
	python3 /vinyl-src/lib/libvsc/vsctool.py -c /xkey-src/src/xkey.vsc
	python3 /vinyl-src/lib/libvsc/vsctool.py -h /xkey-src/src/xkey.vsc
	xkey_cflags="-fPIC -DPIC -DHAVE_CONFIG_H"
	xkey_cflags="$xkey_cflags -I. -I/xkey-src/src -I$vinyl_build"
	xkey_cflags="$xkey_cflags -I$vinyl_build/include -I/vinyl-src/include"
	xkey_cflags="$xkey_cflags -I/vinyl-src/bin/vinyld -I/vinyl-src/lib/libvgz"
	xkey_cflags="$xkey_cflags -I/vinyl-src/lib/libvsc -I/vinyl-src/lib/libvinyl"
	xkey_cflags="$xkey_cflags -I/vinyl-src/lib/libvinylapi"
	xkey_cflags="$xkey_cflags -Wall -Werror -Wno-format-y2k"
	xkey_cflags="$xkey_cflags -Wstrict-prototypes -Wmissing-prototypes"
	xkey_cflags="$xkey_cflags -Werror=missing-field-initializers"
	xkey_cflags="$xkey_cflags -Wpointer-arith -Wreturn-type -Wwrite-strings"
	xkey_cflags="$xkey_cflags -Wcast-qual -Wswitch -Wshadow"
	xkey_cflags="$xkey_cflags -Wunused-parameter -Wcast-align"
	xkey_cflags="$xkey_cflags -Wchar-subscripts -Wnested-externs"
	xkey_cflags="$xkey_cflags -Wextra -Wno-sign-compare"
	cc $xkey_cflags -c /xkey-src/src/vmod_xkey.c -o vmod_xkey.o
	cc $xkey_cflags -c VSC_xkey.c -o VSC_xkey.o
	cc $xkey_cflags -c vcc_xkey_if.c -o vcc_xkey_if.o
	cc -shared -o libvmod_xkey.so vmod_xkey.o VSC_xkey.o vcc_xkey_if.o
	xkey_flag=--include-xkey
else
	xkey_flag=
fi

noindex_flag=
if [ "${RUN_NOINDEX}" = 0 ]; then
	noindex_flag=--skip-noindex
fi

slash_vmod_path=
if [ "$BENCH_STORAGE_KIND" = fellow ] || [ "$BENCH_STORAGE_KIND" = buddy ]; then
	slash_vmod_path="$slash_src/src/.libs/libvmod_slash.so"
fi
cachetag_persist_flag=
if [ "$BENCH_CACHE_TAG_PERSIST" = 1 ]; then
	cachetag_persist_flag=--cachetag-persist
fi

python3 /cachetag-host/benchmarks/generate_cachetag_benchmark_vtc.py \
	--out-dir /results/workloads \
	--objects "$OBJECTS" \
	--tags-per-object "$TAGS_PER_OBJECT" \
	--storage "$BENCH_STORAGE" \
	--eviction-storage "$BENCH_EVICTION_STORAGE" \
	--cold-residency-storage "$BENCH_COLD_RESIDENCY_STORAGE" \
	--vinyl-threads "$BENCH_VINYL_THREADS" \
	--driver-command "$driver_command" \
	--backend-command "$backend_command" \
	--backend-body-bytes "$BENCH_BACKEND_BODY_BYTES" \
	--eviction-body-bytes "$BENCH_EVICTION_BODY_BYTES" \
	--cold-residency-body-bytes "$BENCH_COLD_RESIDENCY_BODY_BYTES" \
	--storage-kind "$BENCH_STORAGE_KIND" \
	--fellow-size "$BENCH_FELLOW_SIZE" \
	--fellow-segment-size "$BENCH_FELLOW_SEGMENT_SIZE" \
	--fellow-block-size "$BENCH_FELLOW_BLOCK_SIZE" \
	--buddy-size "$BENCH_BUDDY_SIZE" \
	--buddy-reserve-chunks "$BENCH_BUDDY_RESERVE_CHUNKS" \
	--slash-vmod-path "$slash_vmod_path" \
	--timeout-idle "$BENCH_TIMEOUT_IDLE" \
	--backend-idle-timeout "$BENCH_BACKEND_IDLE_TIMEOUT" \
	--cachetag-wal-fsync "$BENCH_CACHE_TAG_WAL_FSYNC" \
	--shutdown-drain-seconds "$BENCH_SHUTDOWN_DRAIN_SECONDS" \
	--profile "$BENCH_PROFILE" \
	$cachetag_persist_flag \
	$noindex_flag \
	$xkey_flag

/cachetag-host/benchmarks/capture_system_metadata.sh /results/system.env
{
	printf "vinyld=%s\n" "$prefix/sbin/vinyld"
	ldd "$prefix/sbin/vinyld" 2>&1 || true
} > /results/vinyld-allocator-linkage.txt

{
	printf "date_utc=%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf "vinyl_revision=%s\n" "$(git -C /vinyl-src rev-parse --short HEAD 2>/dev/null || true)"
	printf "cachetag_revision=%s\n" "$(git -C /cachetag-host rev-parse --short HEAD 2>/dev/null || true)"
	if [ "$BENCH_STORAGE_KIND" = fellow ] || [ "$BENCH_STORAGE_KIND" = buddy ]; then
		printf "slash_revision=%s\n" "$(git -C /slash-host rev-parse --short HEAD 2>/dev/null || true)"
	else
		printf "slash_revision=\n"
	fi
	if [ "$RUN_XKEY" = 1 ]; then
		printf "xkey_revision=%s\n" "$(git -C /xkey-src rev-parse --short HEAD 2>/dev/null || true)"
	else
		printf "xkey_revision=\n"
	fi
	printf "objects=%s\n" "$OBJECTS"
	printf "tags_per_object=%s\n" "$TAGS_PER_OBJECT"
	printf "bench_profile=%s\n" "$BENCH_PROFILE"
	printf "bench_matrix=%s\n" "$BENCH_MATRIX"
	printf "bench_result_id=%s\n" "$BENCH_RESULT_ID"
	printf "bench_buckets=%s\n" "$BENCH_BUCKETS"
	printf "bench_clients=%s\n" "$BENCH_CLIENTS"
	printf "bench_warm_seconds=%s\n" "$BENCH_WARM_SECONDS"
	printf "bench_warm_validate_hit=%s\n" "$BENCH_WARM_VALIDATE_HIT"
	printf "bench_residency_validate_objects=%s\n" "$BENCH_RESIDENCY_VALIDATE_OBJECTS"
	printf "bench_http_timeout=%s\n" "$BENCH_HTTP_TIMEOUT"
	printf "bench_tag_universe=%s\n" "$BENCH_TAG_UNIVERSE"
	printf "bench_tag_length_class=%s\n" "$BENCH_TAG_LENGTH_CLASS"
	printf "bench_validate_tag_shape=%s\n" "$BENCH_VALIDATE_TAG_SHAPE"
	printf "bench_purge_requests=%s\n" "$BENCH_PURGE_REQUESTS"
	printf "bench_skip_purge=%s\n" "$BENCH_SKIP_PURGE"
	printf "bench_expect_fellow_attr_bytes_per_object=%s\n" "$BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT"
	printf "bench_purge_keys_per_request=%s\n" "$BENCH_PURGE_KEYS_PER_REQUEST"
	printf "bench_purge_validate_objects=%s\n" "$BENCH_PURGE_VALIDATE_OBJECTS"
	printf "bench_purge_settle_ms=%s\n" "$BENCH_PURGE_SETTLE_MS"
	printf "bench_purge_validation_delay_ms=%s\n" "$BENCH_PURGE_VALIDATION_DELAY_MS"
	printf "bench_purge_hit_recheck_delay_ms=%s\n" "$BENCH_PURGE_HIT_RECHECK_DELAY_MS"
	printf "bench_allow_stale_after_purge=%s\n" "$BENCH_ALLOW_STALE_AFTER_PURGE"
	printf "bench_allow_lru_nuked=%s\n" "$BENCH_ALLOW_LRU_NUKED"
	printf "bench_purge_window_timeout_ms=%s\n" "$BENCH_PURGE_WINDOW_TIMEOUT_MS"
	printf "bench_purge_window_concurrency=%s\n" "$BENCH_PURGE_WINDOW_CONCURRENCY"
	printf "bench_concurrent_seconds=%s\n" "$BENCH_CONCURRENT_SECONDS"
	printf "bench_concurrent_readers=%s\n" "$BENCH_CONCURRENT_READERS"
	printf "bench_concurrent_writers=%s\n" "$BENCH_CONCURRENT_WRITERS"
	printf "bench_concurrent_purgers=%s\n" "$BENCH_CONCURRENT_PURGERS"
	printf "bench_concurrent_target_rps=%s\n" "$BENCH_CONCURRENT_TARGET_RPS"
	printf "bench_concurrent_purge_rate=%s\n" "$BENCH_CONCURRENT_PURGE_RATE"
	printf "bench_concurrent_insert_every=%s\n" "$BENCH_CONCURRENT_INSERT_EVERY"
	printf "bench_purge_storm_rate=%s\n" "$BENCH_PURGE_STORM_RATE"
	printf "bench_purge_storm_distinct=%s\n" "$BENCH_PURGE_STORM_DISTINCT"
	printf "bench_purge_storm_unknown_percent=%s\n" "$BENCH_PURGE_STORM_UNKNOWN_PERCENT"
	printf "bench_purge_storm_soft_percent=%s\n" "$BENCH_PURGE_STORM_SOFT_PERCENT"
	printf "bench_populated_map_entries=%s\n" "$BENCH_POPULATED_MAP_ENTRIES"
	printf "bench_residency_sweep_seconds=%s\n" "$BENCH_RESIDENCY_SWEEP_SECONDS"
	printf "bench_residency_sample_ms=%s\n" "$BENCH_RESIDENCY_SAMPLE_MS"
	printf "bench_phase4_pre_seconds=%s\n" "$BENCH_PHASE4_PRE_SECONDS"
	printf "bench_phase4_sweep_seconds=%s\n" "$BENCH_PHASE4_SWEEP_SECONDS"
	printf "bench_phase4_post_seconds=%s\n" "$BENCH_PHASE4_POST_SECONDS"
	printf "bench_phase4_attribution_guard_ms=%s\n" "$BENCH_PHASE4_ATTRIBUTION_GUARD_MS"
	printf "bench_phase5_hold_ms=%s\n" "$BENCH_PHASE5_HOLD_MS"
	printf "bench_phase5_cap_purges=%s\n" "$BENCH_PHASE5_CAP_PURGES"
	printf "bench_phase6_pressure_body_bytes=%s\n" "$BENCH_PHASE6_PRESSURE_BODY_BYTES"
	printf "bench_phase6_quiet_seconds=%s\n" "$BENCH_PHASE6_QUIET_SECONDS"
	printf "bench_malloc_conf=%s\n" "$BENCH_MALLOC_CONF"
	printf "bench_malloc_arena_max=%s\n" "$BENCH_MALLOC_ARENA_MAX"
	printf "bench_malloc_trim_threshold=%s\n" "$BENCH_MALLOC_TRIM_THRESHOLD_"
	printf "bench_instrument_obj_mtx=%s\n" "$BENCH_INSTRUMENT_OBJ_MTX"
	printf "bench_validate_residency=%s\n" "$BENCH_VALIDATE_RESIDENCY"
	printf "bench_restart_tag_profile=%s\n" "$BENCH_RESTART_TAG_PROFILE"
	printf "bench_restart_touch_percent=%s\n" "$BENCH_RESTART_TOUCH_PERCENT"
	printf "bench_eviction_storage=%s\n" "$BENCH_EVICTION_STORAGE"
	printf "bench_cold_residency_storage=%s\n" "$BENCH_COLD_RESIDENCY_STORAGE"
	printf "bench_backend_body_bytes=%s\n" "$BENCH_BACKEND_BODY_BYTES"
	printf "bench_eviction_body_bytes=%s\n" "$BENCH_EVICTION_BODY_BYTES"
	printf "bench_cold_residency_body_bytes=%s\n" "$BENCH_COLD_RESIDENCY_BODY_BYTES"
	printf "bench_eviction_validate_objects=%s\n" "$BENCH_EVICTION_VALIDATE_OBJECTS"
	printf "bench_system_sample_interval=%s\n" "$BENCH_SYSTEM_SAMPLE_INTERVAL"
	printf "bench_detailed_memory_interval=%s\n" "$BENCH_DETAILED_MEMORY_INTERVAL"
	printf "bench_detailed_memory_timeout=%s\n" "$BENCH_DETAILED_MEMORY_TIMEOUT"
	printf "bench_storage=%s\n" "$BENCH_STORAGE"
	printf "cache_tag_bench_ttl=%s\n" "$CACHE_TAG_BENCH_TTL"
	printf "cache_tag_churn_compact_each_cycle=%s\n" "$CACHE_TAG_CHURN_COMPACT_EACH_CYCLE"
	printf "bench_storage_kind=%s\n" "$BENCH_STORAGE_KIND"
	printf "bench_fellow_size=%s\n" "$BENCH_FELLOW_SIZE"
	printf "bench_fellow_segment_size=%s\n" "$BENCH_FELLOW_SEGMENT_SIZE"
	printf "bench_fellow_block_size=%s\n" "$BENCH_FELLOW_BLOCK_SIZE"
	printf "bench_buddy_size=%s\n" "$BENCH_BUDDY_SIZE"
	printf "bench_buddy_reserve_chunks=%s\n" "$BENCH_BUDDY_RESERVE_CHUNKS"
	printf "bench_cache_tag_persist=%s\n" "$BENCH_CACHE_TAG_PERSIST"
	printf "bench_cache_tag_wal_fsync=%s\n" "$BENCH_CACHE_TAG_WAL_FSYNC"
	printf "bench_cache_tag_purge_history_max_entries=%s\n" "$BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES"
	printf "bench_stream1_expect_checkpoint=%s\n" "$BENCH_STREAM1_EXPECT_CHECKPOINT"
	printf "bench_stream1_overlap_preseed_entries=%s\n" "$BENCH_STREAM1_OVERLAP_PRESEED_ENTRIES"
	printf "bench_stream1_overlap_reads=%s\n" "$BENCH_STREAM1_OVERLAP_READS"
	printf "bench_cache_tag_sweep_interval=%s\n" "$BENCH_CACHE_TAG_SWEEP_INTERVAL"
	printf "bench_cache_tag_sweep_batch_objects=%s\n" "$BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS"
	printf "bench_cache_tag_sweep_batch_hold=%s\n" "$BENCH_CACHE_TAG_SWEEP_BATCH_HOLD"
	printf "bench_cache_tag_sweep_batch_yield=%s\n" "$BENCH_CACHE_TAG_SWEEP_BATCH_YIELD"
	printf "bench_purgemap_expect_rebuild=%s\n" "$BENCH_PURGEMAP_EXPECT_REBUILD"
	printf "bench_vinyl_threads=%s\n" "$BENCH_VINYL_THREADS"
	printf "churn_cycles=%s\n" "$CHURN_CYCLES"
	printf "runs=%s\n" "$RUNS"
	printf "bench_workload_filter=%s\n" "$BENCH_WORKLOAD_FILTER"
	printf "run_xkey=%s\n" "$RUN_XKEY"
	printf "run_noindex=%s\n" "$RUN_NOINDEX"
	printf "vtc_log_bytes=%s\n" "$VTC_LOG_BYTES"
	printf "vtc_timeout=%s\n" "$VTC_TIMEOUT"
	printf "vtc_quiet=%s\n" "$VTC_QUIET"
	printf "perf_mode=%s\n" "$PERF_MODE"
	printf "bench_perf_record=%s\n" "$BENCH_PERF_RECORD"
	printf "bench_perf_record_scope=%s\n" "$BENCH_PERF_RECORD_SCOPE"
	printf "bench_perf_record_phase=%s\n" "$BENCH_PERF_RECORD_PHASE"
	printf "bench_perf_record_target=%s\n" "$BENCH_PERF_RECORD_TARGET"
	printf "bench_perf_record_runs=%s\n" "$BENCH_PERF_RECORD_RUNS"
	printf "bench_perf_record_workload=%s\n" "$BENCH_PERF_RECORD_WORKLOAD"
	printf "bench_perf_freq=%s\n" "$BENCH_PERF_FREQ"
	printf "image=%s\n" "'"$image"'"
	printf "docker_command=%s\n" "'"$docker_cmd"'"
	printf "docker_run_args=%s\n" "'"$docker_run_args"'"
} > /results/metadata.env

cd "$cachetag_src/src"

save_symbol_artifacts() {
	mkdir -p /results/symbols/cachetag-src/src/.libs \
		/results/symbols/work \
		/results/symbols/tmp \
		/results/symbols/prefix/bin \
		/results/symbols/prefix/sbin \
		/results/symbols/prefix/lib/vinyl-cache/vmods
	cp -a "$cachetag_src/src/.libs"/libvmod_cachetag.so* \
		/results/symbols/cachetag-src/src/.libs/ 2>/dev/null || true
	cp -a /work/cachetag-http-workload-driver /work/cachetag-benchmark-backend \
		/results/symbols/work/ 2>/dev/null || true
	find /tmp -path "*/vgc.so" -type f -exec cp -a {} /results/symbols/tmp/ \; \
		2>/dev/null || true
	find /tmp -path "*/vmod_cache/_vmod_cachetag*" -type f \
		-exec cp -a {} /results/symbols/tmp/ \; 2>/dev/null || true
	cp -a "$prefix/bin/vinyltest" /results/symbols/prefix/bin/ 2>/dev/null || true
	cp -a "$prefix/sbin/vinyld" /results/symbols/prefix/sbin/ 2>/dev/null || true
	cp -a "$prefix/lib/vinyl-cache/vmods"/libvmod_*.so* \
		/results/symbols/prefix/lib/vinyl-cache/vmods/ 2>/dev/null || true
	if [ -d /results/xkey-build ]; then
		mkdir -p /results/symbols/xkey-build
		cp -a /results/xkey-build/libvmod_xkey.so \
			/results/symbols/xkey-build/ 2>/dev/null || true
	fi
	if { [ "$BENCH_STORAGE_KIND" = fellow ] || [ "$BENCH_STORAGE_KIND" = buddy ]; } && [ -d "$slash_src/src/.libs" ]; then
		mkdir -p /results/symbols/slash-src/src/.libs
		cp -a "$slash_src/src/.libs"/libvmod_slash.so* \
			/results/symbols/slash-src/src/.libs/ 2>/dev/null || true
	fi
	{
		printf "date_utc=%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		find /results/symbols -type f -print | sort | while read -r path; do
			printf "%s\t" "${path#/results/}"
			file "$path" 2>/dev/null || true
		done
	} > /results/symbols/manifest.txt
}

prime_perf_buildid_cache() {
	if ! command -v perf >/dev/null 2>&1; then
		return
	fi
	find /results/symbols -type f -print | sort | while read -r path; do
		case "$(file -b "$path" 2>/dev/null || true)" in
			ELF*)
				perf buildid-cache -a "$path" >/dev/null 2>&1 || true
				;;
		esac
	done
}

perf_record_enabled() {
	case "$BENCH_PERF_RECORD" in
		1|yes|true|on|required) return 0 ;;
		*) return 1 ;;
	esac
}

should_perf_record_run() {
	case "$BENCH_PERF_RECORD_RUNS" in
		all) ;;
		""|*[!0-9]*)
			return 1
			;;
		*)
			if [ "$run" -gt "$BENCH_PERF_RECORD_RUNS" ]; then
				return 1
			fi
			;;
	esac
	if [ -n "$BENCH_PERF_RECORD_WORKLOAD" ] &&
	    [ "$name" != "$BENCH_PERF_RECORD_WORKLOAD" ]; then
		return 1
	fi
	return 0
}

write_perf_reports() {
	perf_data=$1
	report_prefix=$2

	if [ ! -s "$perf_data" ]; then
		return
	fi
	prime_perf_buildid_cache
	perf buildid-list -i "$perf_data" > "${report_prefix}.perf-buildids.txt" \
		2> "${report_prefix}.perf-buildids.err" || true
	perf report -i "$perf_data" --stdio --no-children \
		--sort comm,dso,symbol > "${report_prefix}.perf-report.txt" \
		2> "${report_prefix}.perf-report.err" || true
	perf report -i "$perf_data" --stdio --children \
		--sort comm,dso,symbol > "${report_prefix}.perf-report-children.txt" \
		2> "${report_prefix}.perf-report-children.err" || true
	perf script -i "$perf_data" 2> "${report_prefix}.perf-script.err" |
		sed -n "1,20000p" > "${report_prefix}.perf-script.txt" || true
}

save_symbol_artifacts

vtc_quiet_flag=
if [ "$VTC_QUIET" = 1 ]; then
	vtc_quiet_flag=-q
else
	vtc_quiet_flag=-v
fi

matched_workloads=0
for workload in /results/workloads/*.vtc; do
	name=$(basename "$workload" .vtc)
	if [ -n "$BENCH_WORKLOAD_FILTER" ] &&
	    [ "$name" != "$BENCH_WORKLOAD_FILTER" ]; then
		continue
	fi
	matched_workloads=$((matched_workloads + 1))
	case "$name" in
		noindex_load)
			artifact_prefix=noindex_load
			;;
		*_purge)
			artifact_prefix=${name%_purge}
			;;
		*)
			artifact_prefix=$name
			;;
	esac
	for run in $(seq 1 "$RUNS"); do
		out="/results/${name}.run-${run}.log"
		timing="/results/${name}.run-${run}.time"
		printf "benchmark %s run %s/%s\n" "$name" "$run" "$RUNS" | tee -a /results/summary.txt
		if perf_record_enabled && should_perf_record_run; then
			if ! command -v perf >/dev/null 2>&1; then
				if [ "$BENCH_PERF_RECORD" = required ]; then
					echo "perf record requested but perf is unavailable" >&2
					exit 1
				fi
				printf "WARNING: perf record requested but perf is unavailable for %s run %s\n" \
					"$name" "$run" | tee -a /results/summary.txt
				python3 /cachetag-host/benchmarks/run_with_metrics.py \
					--metrics "$timing" --perf "$PERF_MODE" -- \
					"$prefix/bin/vinyltest" -t "$VTC_TIMEOUT" \
					-b "$VTC_LOG_BYTES" $vtc_quiet_flag "$workload" > "$out" 2>&1
			else
				perf_data="/results/${name}.run-${run}.perf.data"
				report_prefix="/results/${name}.run-${run}"
				case "$BENCH_PERF_RECORD_SCOPE" in
					system)
						perf_scope=-a
						;;
					command)
						perf_scope=
						;;
					*)
						echo "unknown BENCH_PERF_RECORD_SCOPE=$BENCH_PERF_RECORD_SCOPE" >&2
						exit 1
						;;
				esac
				printf "perf-record %s run %s phase=%s scope=%s target=%s freq=%s\n" \
					"$name" "$run" "$BENCH_PERF_RECORD_PHASE" \
					"$BENCH_PERF_RECORD_SCOPE" "$BENCH_PERF_RECORD_TARGET" \
					"$BENCH_PERF_FREQ" |
					tee -a /results/summary.txt
				case "$BENCH_PERF_RECORD_PHASE" in
					command)
						python3 /cachetag-host/benchmarks/run_with_metrics.py \
							--metrics "$timing" --perf "$PERF_MODE" -- \
							perf record -F "$BENCH_PERF_FREQ" -g $perf_scope \
							-o "$perf_data" -- \
							"$prefix/bin/vinyltest" -t "$VTC_TIMEOUT" \
							-b "$VTC_LOG_BYTES" $vtc_quiet_flag "$workload" > "$out" 2>&1
						;;
					load|warm|concurrent)
						python3 /cachetag-host/benchmarks/run_with_metrics.py \
							--metrics "$timing" --perf "$PERF_MODE" -- \
							python3 /cachetag-host/benchmarks/run_with_phase_perf.py \
							--perf-data "$perf_data" \
							--marker-dir /results/phase-markers \
							--marker-prefix "${name}.run-${run}" \
							--phase "$BENCH_PERF_RECORD_PHASE" \
							--freq "$BENCH_PERF_FREQ" \
							--scope "$BENCH_PERF_RECORD_SCOPE" \
							--target "$BENCH_PERF_RECORD_TARGET" -- \
							"$prefix/bin/vinyltest" -t "$VTC_TIMEOUT" \
							-b "$VTC_LOG_BYTES" $vtc_quiet_flag "$workload" > "$out" 2>&1
						;;
					*)
						echo "unknown BENCH_PERF_RECORD_PHASE=$BENCH_PERF_RECORD_PHASE" >&2
						exit 1
						;;
				esac
				save_symbol_artifacts
				write_perf_reports "$perf_data" "$report_prefix"
			fi
		else
			python3 /cachetag-host/benchmarks/run_with_metrics.py \
				--metrics "$timing" --perf "$PERF_MODE" -- \
				"$prefix/bin/vinyltest" -t "$VTC_TIMEOUT" \
				-b "$VTC_LOG_BYTES" $vtc_quiet_flag "$workload" > "$out" 2>&1
		fi
		if grep -qx "swap_activity=1" "$timing"; then
			printf "WARNING: swap activity detected during %s run %s\n" \
				"$name" "$run" | tee -a /results/summary.txt
			{
				printf "workload=%s\n" "$name"
				printf "run=%s\n" "$run"
				grep -E "^(vmstat_pswpin_delta|vmstat_pswpout_delta|vmstat_pgmajfault_delta|meminfo_swapfree_kb_delta)=" "$timing" || true
			} >> /results/SWAP_DETECTED
		fi
		for artifact in /results/${artifact_prefix}*.driver \
		    /results/${artifact_prefix}*.latency_samples.tsv \
		    /results/${artifact_prefix}*.phase4_requests.tsv \
		    /results/${artifact_prefix}*.phase4_boundaries.tsv \
		    /results/${artifact_prefix}*.phase6_memory \
		    /results/${artifact_prefix}*.phase6_memory.smaps \
		    /results/${artifact_prefix}*.persistence \
		    /results/${artifact_prefix}*.stats; do
			if [ ! -f "$artifact" ]; then
				continue
			fi
			base=$(basename "$artifact")
			case "$base" in
				*.run-*)
					continue
					;;
			esac
			stem=${base%%.*}
			ext=${base#*.}
			cp "$artifact" "/results/${stem}.run-${run}.${ext}"
		done
	done
done
if [ -n "$BENCH_WORKLOAD_FILTER" ] && [ "$matched_workloads" -eq 0 ]; then
	echo "BENCH_WORKLOAD_FILTER=$BENCH_WORKLOAD_FILTER matched no generated workload" >&2
	exit 1
fi
'

rm -f "$docker_cidfile"
trap - INT TERM HUP EXIT

echo "benchmark results: $results_dir"
