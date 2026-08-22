#!/usr/bin/env python3
"""Summarize cachetag benchmark result directories or downloaded tarballs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable


LOW_CPU_BUSY_PERCENT = 25.0
LOW_MEMORY_DROP_PERCENT = 5.0
SWEEP_MIN_REPETITIONS = 2
SWEEP_MIN_SIGNIFICANT_CHANGE_PERCENT = 2.0
SWEEP_NOISE_MULTIPLIER = 3.0
SAMPLER_MIN_CADENCE_RATIO = 0.80
SAMPLER_MAX_GAP_INTERVALS = 5.0
SAMPLER_MAX_GAP_FLOOR_SECONDS = 1.0
SHA256_VALUE_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
COMPARISON_PACING_FIELDS = (
    "scheduled_slots", "executed_slots", "skipped_slots", "late_starts",
    "scheduling_lag_seconds", "scheduling_lag_max_seconds", "offered_rps",
    "achieved_rps", "errors",
)
COMPARISON_COHORT_FIELDS = (
    "cpu_model", "cpu_topology", "cpu_smt_siblings", "cpu_scaling_governors",
    "cpu_frequency_state", "cpu_boost_state", "kernel", "nproc", "mem_total_kb",
)
PERF_STAT_ROW_METRICS = (
    "vinyld_warm_instructions",
    "vinyld_warm_cycles",
    "vinyld_warm_task_clock_seconds",
    "vinyld_warm_instructions_per_hit",
    "vinyld_warm_cycles_per_hit",
    "vinyld_warm_task_clock_seconds_per_hit",
    "vinyld_warm_ipc",
    "vinyld_warm_instructions_running_percent",
    "vinyld_warm_cycles_running_percent",
    "vinyld_warm_task_clock_running_percent",
    "vinyld_warm_perf_stat_running_percent_min",
)
PURGEMAP_FELLOW_DIRECT_COUNTERS = (
    "purgemap_fellow_attr_objects_written",
    "purgemap_fellow_attr_bytes_written",
    "purgemap_fellow_direct_probes",
    "purgemap_fellow_attr_absent",
    "purgemap_fellow_attr_invalid",
    "purgemap_fellow_attr_read_failures",
    "purgemap_fellow_namespace_records_probed",
    "purgemap_fellow_store_invariant_failures",
    "purgemap_volatile_fallback_attaches",
)
SET_INTERNING_COUNTERS = (
    "volatile_interned_sets",
    "volatile_interned_set_refs",
    "volatile_interned_set_hits",
    "volatile_interned_set_misses",
    "volatile_interned_set_bytes",
    "volatile_interned_table_bytes",
)
PURGEMAP_RESTART_PHASES = (
    "post_load",
    "post_restart",
    "post_first_touch",
    "post_cold_purge",
    "post_hot_purge",
)
PURGEMAP_FELLOW_DIRECT_PHASE_FIELDS = tuple(
    f"{phase}_{counter}"
    for phase in PURGEMAP_RESTART_PHASES
    for counter in PURGEMAP_FELLOW_DIRECT_COUNTERS
)
PHASE4_VSC_LEGACY_COUNTERS = (
    *(f"request_{category}_obj_mtx_{suffix}" for category in ("probe", "attach", "invalidate")
      for suffix in ("calls", "wait_usec", "wait_max_usec")),
    *(f"{operation}_{suffix}" for operation in (
        "object_grow", "object_shrink", "side_grow_rehash", "side_shrink_rehash", "zero_container_free"
    ) for suffix in (
        "calls", "usec", "max_usec", "failures", "compact_active_calls"
    )),
    "record_shrink_calls", "record_shrink_obj_mtx_wait_usec", "record_shrink_obj_mtx_wait_max_usec",
    "record_shrink_obj_mtx_hold_usec", "record_shrink_obj_mtx_hold_max_usec",
    "record_shrink_obj_mtx_hold_last_usec",
)
PHASE4_VSC_RESIZE_GAUGES = (
    "object_segments", "object_published_slots", "object_published_bytes",
    "object_emergency_segment_old_capacity_max",
    "side_primary_buckets", "side_primary_bytes", "side_primary_live", "side_primary_tombstones",
    "side_retiring_buckets", "side_retiring_bytes", "side_retiring_live",
    "side_retiring_tombstones", "side_resize_state", "side_resize_reason",
    "side_resize_attach_grow_old_buckets_max",
    "side_migration_buckets_remaining",
    "side_migration_live_remaining",
    "resize_low_water_active", "resize_low_water_elapsed_usec",
    "resize_low_water_observed_live", "resize_low_water_target_objects",
    "resize_low_water_target_side_buckets",
    "resize_active_bytes", "resize_retiring_bytes", "resize_detached_bytes",
    "resize_reconciled_bytes",
)
PHASE4_VSC_RESIZE_CUMULATIVE_COUNTERS = (
    "object_segment_grow_publishes", "object_emergency_segment_allocations",
    "object_segment_detach_batches", "object_segment_alloc_usec",
    "object_segment_alloc_failures", "object_segment_free_usec",
    "side_migration_batches", "side_migration_inspected_buckets",
    "side_migration_moved_entries", "side_migration_completions",
    "side_destination_alloc_usec", "side_destination_alloc_failures",
    "side_retired_free_usec", "side_resize_grow_publishes",
    "side_resize_attach_grow_publishes",
    "side_resize_rebuild_publishes", "side_resize_shrink_publishes",
    "side_resize_shrink_cancellations", "side_resize_shrink_rollbacks",
    "resize_batch_obj_mtx_wait_usec", "resize_batch_obj_mtx_hold_usec",
    "resize_batch_obj_mtx_hold_over_2ms", "resize_batch_obj_mtx_hold_over_5ms",
    "resize_batch_obj_mtx_hold_over_10ms", "resize_low_water_starts",
    "resize_low_water_restarts", "resize_low_water_rearms",
    "resize_low_water_cancellations",
)
PHASE4_VSC_RESIZE_LAST_MAX_FIELDS = (
    "object_segment_alloc_max_usec", "object_segment_alloc_last_usec",
    "object_segment_free_max_usec", "object_segment_free_last_usec",
    "side_destination_alloc_max_usec", "side_destination_alloc_last_usec",
    "side_retired_free_max_usec", "side_retired_free_last_usec",
    "resize_batch_obj_mtx_wait_max_usec", "resize_batch_obj_mtx_wait_last_usec",
    "resize_batch_obj_mtx_hold_max_usec", "resize_batch_obj_mtx_hold_last_usec",
)
PHASE4_VSC_COUNTERS = (
    *PHASE4_VSC_LEGACY_COUNTERS,
    *PHASE4_VSC_RESIZE_GAUGES,
    *PHASE4_VSC_RESIZE_CUMULATIVE_COUNTERS,
    *PHASE4_VSC_RESIZE_LAST_MAX_FIELDS,
)
PHASE4_VSC_CUMULATIVE_COUNTERS = tuple(
    counter for counter in PHASE4_VSC_COUNTERS
    if (
        counter in PHASE4_VSC_RESIZE_CUMULATIVE_COUNTERS
        or (
            counter in PHASE4_VSC_LEGACY_COUNTERS
            and "max" not in counter
            and "last" not in counter
        )
    )
)
PHASE4_DISTRIBUTIONS = (
    "phase4_pre", "phase4_compact_overlap", "phase4_compact_guarded", "phase4_refill", "phase4_post"
)
PHASE4_DISTRIBUTION_SUFFIXES = (
    "samples", "interval_seconds", "requests_scheduled", "requests_started", "requests_completed",
    "skipped_pacing_slots", "late_pacing_starts", "scheduling_lag_max_seconds",
    "offered_requests_per_second", "achieved_requests_per_second", "hits", "misses", "errors",
    "stale_responses", "epoch_mismatches", "latency_p50_seconds", "latency_p95_seconds",
    "latency_p99_seconds", "latency_p999_seconds", "latency_p9999_seconds", "latency_max_seconds",
    "latency_above_5ms", "latency_above_15ms", "latency_above_50ms", "latency_above_100ms",
    "latency_above_500ms", "latency_above_1s",
)
PHASE4_ATTRIBUTION_FIELDS = (
    "phase4_schema_valid", "phase4_compact_present", "phase4_dropped_samples",
    *(f"{phase}_{suffix}" for phase in PHASE4_DISTRIBUTIONS for suffix in PHASE4_DISTRIBUTION_SUFFIXES),
)
PHASE4_DETACHED_FREE_COUNTERS = (
    "object_segment_free",
    "side_retired_free",
    "zero_container_free",
)
PHASE4_VSC_DELTA_PHASES = (
    "phase4_pre",
    "phase4_compact",
    "phase4_refill",
    "phase4_post",
)
PHASE5_VSC_COUNTERS = (
    "sweep_wakeups",
    "sweep_iterations",
)


def parse_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
    except FileNotFoundError:
        pass
    return values


def parse_vsc_stats(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                values[parts[0]] = int(float(parts[1]))
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return values


def parse_perf_stat_csv_details(path: Path) -> dict[str, dict[str, float | str]]:
    """Read counted perf events, units and event running percentages."""
    events: dict[str, dict[str, float | str]] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split(",")
            if len(fields) < 3:
                continue
            event = fields[2].strip().split(":", 1)[0]
            if not event:
                continue
            try:
                count = float(fields[0].strip())
            except ValueError:
                continue
            details: dict[str, float | str] = {
                "count": count,
                "unit": fields[1].strip(),
            }
            if len(fields) > 4:
                try:
                    details["running_percent"] = float(fields[4].strip().rstrip("%"))
                except ValueError:
                    pass
            events[event] = details
    except FileNotFoundError:
        pass
    return events


def parse_perf_stat_csv(path: Path) -> dict[str, int | float]:
    """Read a `perf stat -x ,` CSV into {event: count}.

    Absent counters stay absent. `perf` writes `<not counted>` or
    `<not supported>` in the value column when the PMU is blocked or
    virtualised away, and those rows must not be read as zeros
    (benchmarks/rules/BR-012: local macOS rows have no hardware counters).
    """
    values: dict[str, int | float] = {}
    for event, details in parse_perf_stat_csv_details(path).items():
        count = float(details["count"])
        values[event] = int(count) if count.is_integer() else count
    return values


def perf_stat_seconds(details: dict[str, float | str] | None) -> float | None:
    if details is None:
        return None
    count = float(details["count"])
    unit = str(details.get("unit") or "").lower()
    if unit in {"msec", "ms"}:
        return count / 1000.0
    if unit in {"usec", "us"}:
        return count / 1_000_000.0
    if unit in {"nsec", "ns"}:
        return count / 1_000_000_000.0
    if unit in {"sec", "seconds", "s"}:
        return count
    return None


def perf_stat_running_percent(details: dict[str, float | str] | None) -> float | None:
    if details is None or "running_percent" not in details:
        return None
    return float(details["running_percent"])


def as_float(values: dict[str, str], key: str) -> float | None:
    try:
        return float(values[key])
    except (KeyError, ValueError):
        return None


def as_int(values: dict[str, str], key: str) -> int | None:
    try:
        raw = values[key]
    except KeyError:
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return int(float(raw))
        except ValueError:
            return None


def as_bool_int(values: dict[str, str], key: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return 1
    if normalized in {"0", "false", "no"}:
        return 0
    return None


def sampler_artifact_validity(time_path: Path, values: dict[str, str]) -> dict[str, Any]:
    status = values.get("system_sampler_status", "missing")
    interval = as_float(values, "system_sampler_interval_seconds")
    duration = as_float(values, "system_sampler_active_seconds")
    if duration is None:
        duration = as_float(values, "wall_seconds")
    if status == "disabled":
        return {
            "valid": 0,
            "reason": "sampler_disabled",
            "actual_samples": 0,
            "expected_samples": 0.0,
            "cadence_ratio": 0.0,
            "longest_gap_seconds": duration,
        }
    if interval is None or interval <= 0 or duration is None or duration < 0:
        return {
            "valid": 0,
            "reason": "sampler_metadata_missing",
            "actual_samples": 0,
            "expected_samples": None,
            "cadence_ratio": None,
            "longest_gap_seconds": None,
        }

    sample_path = Path(str(time_path) + ".samples.jsonl")
    timestamps: list[float] = []
    malformed = False
    try:
        for line in sample_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                timestamp = float(row["monotonic_seconds"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                malformed = True
                continue
            timestamps.append(timestamp)
    except FileNotFoundError:
        return {
            "valid": 0,
            "reason": "sampler_timeseries_missing",
            "actual_samples": 0,
            "expected_samples": duration / interval,
            "cadence_ratio": 0.0,
            "longest_gap_seconds": duration,
        }

    expected = duration / interval
    actual = len(timestamps)
    ratio = actual / expected if expected > 0 else 1.0
    nonmonotonic = any(current < previous for previous, current in zip(timestamps, timestamps[1:]))
    if timestamps:
        gaps = [max(0.0, timestamps[0])]
        gaps.extend(current - previous for previous, current in zip(timestamps, timestamps[1:]))
        gaps.append(max(0.0, duration - timestamps[-1]))
        longest_gap = max(gaps)
    else:
        longest_gap = duration
    max_gap = max(SAMPLER_MAX_GAP_INTERVALS * interval, SAMPLER_MAX_GAP_FLOOR_SECONDS)
    reasons: list[str] = []
    if malformed:
        reasons.append("malformed_timeseries")
    if nonmonotonic:
        reasons.append("nonmonotonic_timeseries")
    if expected >= 1.0 and ratio < SAMPLER_MIN_CADENCE_RATIO:
        reasons.append(f"cadence_ratio:{ratio:.6f}<{SAMPLER_MIN_CADENCE_RATIO:.6f}")
    if duration >= interval and longest_gap > max_gap:
        reasons.append(f"longest_gap:{longest_gap:.6f}>{max_gap:.6f}")
    reported_samples = as_int(values, "system_sampler_samples")
    if reported_samples is not None and reported_samples != actual:
        reasons.append(f"sample_count_mismatch:{reported_samples}!={actual}")
    if status in {"error", "stalled", "under_sampled"}:
        reasons.append(f"wrapper_status:{status}")
    return {
        "valid": int(not reasons),
        "reason": "ok" if not reasons else ",".join(reasons),
        "actual_samples": actual,
        "expected_samples": expected,
        "cadence_ratio": ratio,
        "longest_gap_seconds": longest_gap,
    }


def system_memory_artifact_validity(
    values: dict[str, str], sampling_valid: int
) -> tuple[int, str]:
    if sampling_valid != 1:
        return 0, "sampler_invalid"
    if values.get("system_tracked_cache_process_status") != "ok":
        return 0, "cache_process_provenance_missing"
    if values.get("system_tracked_cache_process_comm") != "cache-main":
        return 0, "cache_process_comm_invalid"
    if Path(values.get("system_tracked_cache_process_exe", "")).name != "vinyld":
        return 0, "cache_process_exe_invalid"
    if as_int(values, "system_tracked_cache_process_pid") is None:
        return 0, "cache_process_pid_missing"
    if as_float(values, "system_tracked_cache_process_smaps_pss_kb_max") is None:
        return 0, "cache_process_pss_missing"
    explicit = as_bool_int(values, "system_memory_valid")
    if explicit == 0:
        return 0, values.get("system_memory_validity_reason", "wrapper_memory_invalid")
    return 1, "ok"


def stream1_raw_latency_validity(
    result_dir: Path,
    workload: str,
    run: int,
    driver: dict[str, str],
) -> tuple[int | None, str]:
    expected = as_int(driver, "driver_stream1_overlap_reads")
    if expected is None:
        return None, "not_applicable"
    sample_path = result_dir / f"{workload}.run-{run}.stream1_overlap_reads.latency_samples.tsv"
    if not sample_path.exists():
        return 0, "raw_latency_file_missing"
    purge_start = as_int(driver, "driver_stream1_overlap_purge_start_unix_ns")
    purge_end = as_int(driver, "driver_stream1_overlap_purge_end_unix_ns")
    if purge_start is None or purge_end is None or purge_end < purge_start:
        return 0, "raw_latency_boundaries_invalid"
    seen: set[int] = set()
    overlap = 0
    over_15ms = 0
    overlap_over_15ms = 0
    try:
        with sample_path.open(newline="", encoding="utf-8") as handle:
            for row_number, row in enumerate(csv.DictReader(handle, delimiter="\t"), 2):
                try:
                    index = int(row["request_index"])
                    start = int(row["start_unix_ns"])
                    end = int(row["end_unix_ns"])
                    seconds = float(row["seconds"])
                except (KeyError, TypeError, ValueError):
                    return 0, f"raw_latency_malformed_row:{row_number}"
                # Go records wall timestamps and monotonic duration with
                # separate clock calls, so they are not equality-checkable.
                if index in seen or end < start or seconds < 0 or not math.isfinite(seconds):
                    return 0, f"raw_latency_inconsistent_row:{row_number}"
                if row.get("cache_state") != "hit":
                    return 0, f"raw_latency_non_hit:{row_number}"
                seen.add(index)
                is_overlap = end >= purge_start and start <= purge_end
                if is_overlap:
                    overlap += 1
                if seconds > 0.015:
                    over_15ms += 1
                    if is_overlap:
                        overlap_over_15ms += 1
    except OSError as exc:
        return 0, f"raw_latency_read_error:{exc.errno}"
    if seen != set(range(1, expected + 1)):
        return 0, f"raw_latency_count_or_index_invalid:{len(seen)}!={expected}"
    checks = {
        "driver_stream1_overlap_reads_during_purge": overlap,
        "driver_stream1_overlap_reads_over_15ms": over_15ms,
        "driver_stream1_overlap_reads_during_purge_over_15ms": overlap_over_15ms,
    }
    for key, actual in checks.items():
        if as_int(driver, key) != actual:
            return 0, f"raw_latency_metric_mismatch:{key}"
    if (as_int(driver, "driver_errors") or 0) != 0:
        return 0, "driver_errors"
    return 1, "ok"


def percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    pos = pct * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    weight = pos - lo
    return values[lo] * (1 - weight) + values[hi] * weight


def phase4_distribution(
    name: str,
    selected: list[dict[str, Any]],
    all_samples: list[dict[str, Any]],
    interval_start_ns: int,
    interval_end_ns: int,
) -> dict[str, Any]:
    if interval_end_ns < interval_start_ns:
        raise ValueError(f"{name}: interval ends before it starts")
    durations = sorted(float(sample["duration_ns"]) / 1e9 for sample in selected)
    interval_seconds = float(interval_end_ns - interval_start_ns) / 1e9
    scheduled_samples = [
        sample for sample in all_samples
        if sample["scheduled_start_ns"] >= 0
        and interval_start_ns <= sample["scheduled_start_ns"] <= interval_end_ns
    ]
    started = sum(
        1 for sample in all_samples
        if interval_start_ns <= sample["request_start_ns"] <= interval_end_ns
    )
    completed = sum(
        1 for sample in all_samples
        if interval_start_ns <= sample["request_end_ns"] <= interval_end_ns
    )
    scheduled = sum(1 + sample["skipped_slots_before"] for sample in scheduled_samples)
    skipped = sum(sample["skipped_slots_before"] for sample in scheduled_samples)
    prefix = name + "_"
    result: dict[str, Any] = {
        prefix + "samples": len(selected),
        prefix + "interval_seconds": interval_seconds,
        prefix + "requests_scheduled": scheduled,
        prefix + "requests_started": started,
        prefix + "requests_completed": completed,
        prefix + "skipped_pacing_slots": skipped,
        prefix + "late_pacing_starts": sum(1 for sample in selected if sample["scheduling_lag_ns"] > 1_000_000),
        prefix + "scheduling_lag_max_seconds": (
            max((sample["scheduling_lag_ns"] for sample in selected), default=0) / 1e9
        ),
        prefix + "offered_requests_per_second": (
            scheduled / interval_seconds if interval_seconds > 0 else None
        ),
        prefix + "achieved_requests_per_second": (
            completed / interval_seconds if interval_seconds > 0 else None
        ),
        prefix + "hits": sum(1 for sample in selected if sample["cache_state"] == "hit"),
        prefix + "misses": sum(1 for sample in selected if sample["cache_state"] != "hit" and sample["error_class"] == "ok"),
        prefix + "errors": sum(1 for sample in selected if sample["error_class"] != "ok"),
        prefix + "stale_responses": sum(
            1 for sample in selected
            if sample["error_class"] == "ok" and sample["returned_epoch"] < sample["requested_epoch"]
        ),
        prefix + "epoch_mismatches": sum(
            1 for sample in selected
            if sample["error_class"] == "ok" and sample["returned_epoch"] != sample["requested_epoch"]
        ),
    }
    if durations:
        result[prefix + "latency_p50_seconds"] = percentile(durations, 0.50)
        result[prefix + "latency_p95_seconds"] = percentile(durations, 0.95)
        result[prefix + "latency_p99_seconds"] = percentile(durations, 0.99)
        result[prefix + "latency_p999_seconds"] = percentile(durations, 0.999) if len(durations) >= 1000 else None
        result[prefix + "latency_p9999_seconds"] = percentile(durations, 0.9999) if len(durations) >= 10000 else None
        result[prefix + "latency_max_seconds"] = durations[-1]
    else:
        for suffix in ("p50", "p95", "p99", "p999", "p9999", "max"):
            result[prefix + f"latency_{suffix}_seconds"] = None
    for label, threshold in (("5ms", 0.005), ("15ms", 0.015), ("50ms", 0.05),
                             ("100ms", 0.1), ("500ms", 0.5), ("1s", 1.0)):
        result[prefix + "latency_above_" + label] = sum(1 for duration in durations if duration > threshold)
    return result


def phase4_attribution(result_dir: Path, workload: str, run: int, driver: dict[str, str]) -> dict[str, Any]:
    schema = driver.get("driver_phase4_sample_schema")
    if schema is None:
        return {}
    if schema != "phase4-request-v1":
        raise ValueError(f"{workload} run {run}: unsupported Phase 4 sample schema {schema!r}")
    sample_path = result_dir / f"{workload}.run-{run}.phase4_requests.tsv"
    boundary_path = result_dir / f"{workload}.run-{run}.phase4_boundaries.tsv"
    if not sample_path.is_file() or not boundary_path.is_file():
        raise ValueError(f"{workload} run {run}: missing rich Phase 4 sample or boundary artifact")
    boundaries: dict[str, str] = {}
    for line in boundary_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        parts = line.split("\t", 1)
        if len(parts) == 2:
            boundaries[parts[0]] = parts[1]
    required_boundaries = (
        "schema", "reader_window_start_ns", "purge_request_start_ns", "purge_response_end_ns",
        "accepted_epoch_transition_ns", "compact_present", "compact_request_start_ns",
        "compact_response_end_ns", "reader_window_end_ns", "pre_start_ns", "pre_end_ns",
        "post_start_ns", "post_end_ns", "attribution_guard_ns",
    )
    missing = [key for key in required_boundaries if key not in boundaries]
    if missing:
        raise ValueError(f"{workload} run {run}: missing Phase 4 boundaries: {', '.join(missing)}")
    if boundaries["schema"] != "phase4-boundaries-v1":
        raise ValueError(f"{workload} run {run}: unsupported boundary schema {boundaries['schema']!r}")
    numeric = {key: int(boundaries[key]) for key in required_boundaries if key != "schema"}
    if numeric["reader_window_end_ns"] < numeric["reader_window_start_ns"]:
        raise ValueError(f"{workload} run {run}: reader window ends before it starts")
    compact_present = numeric["compact_present"] == 1
    if compact_present and numeric["compact_response_end_ns"] < numeric["compact_request_start_ns"]:
        raise ValueError(f"{workload} run {run}: compact ends before it starts")
    if not compact_present and (numeric["compact_request_start_ns"] != -1 or numeric["compact_response_end_ns"] != -1):
        raise ValueError(f"{workload} run {run}: control has a compact boundary")

    int_fields = (
        "sequence", "object", "scheduled_start_ns", "request_start_ns", "request_end_ns",
        "duration_ns", "scheduling_lag_ns", "skipped_slots_before", "requested_epoch",
        "returned_epoch", "began_after_epoch_boundary",
    )
    samples: list[dict[str, Any]] = []
    seen: set[int] = set()
    with sample_path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle, delimiter="\t"), 2):
            if row.get("schema") != "phase4-request-v1":
                raise ValueError(f"{sample_path.name}:{row_number}: invalid schema")
            try:
                sample: dict[str, Any] = {field: int(row[field]) for field in int_fields}
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{sample_path.name}:{row_number}: malformed integer field") from exc
            sample.update({key: row.get(key, "") for key in ("phase_hint", "cache_state", "error_class")})
            if sample["sequence"] in seen:
                raise ValueError(f"{sample_path.name}:{row_number}: duplicate request sequence {sample['sequence']}")
            seen.add(sample["sequence"])
            if sample["request_start_ns"] < 0 or sample["request_end_ns"] < sample["request_start_ns"]:
                raise ValueError(f"{sample_path.name}:{row_number}: impossible request timestamps")
            if sample["duration_ns"] != sample["request_end_ns"] - sample["request_start_ns"]:
                raise ValueError(f"{sample_path.name}:{row_number}: duration does not match timestamps")
            expected_after = int(sample["request_start_ns"] >= numeric["accepted_epoch_transition_ns"])
            if sample["began_after_epoch_boundary"] != expected_after:
                raise ValueError(f"{sample_path.name}:{row_number}: inconsistent epoch-boundary classification")
            if sample["requested_epoch"] < 1:
                raise ValueError(f"{sample_path.name}:{row_number}: non-positive requested epoch")
            if sample["error_class"] == "ok" and sample["returned_epoch"] < 1:
                raise ValueError(f"{sample_path.name}:{row_number}: successful response has no returned epoch")
            samples.append(sample)
    dropped = as_int(driver, "driver_phase4_dropped_samples")
    if dropped is None or dropped != 0:
        raise ValueError(f"{workload} run {run}: Phase 4 dropped-sample count is {dropped!r}")

    pre = [sample for sample in samples if sample["phase_hint"] == "pre"]
    post = [sample for sample in samples if sample["phase_hint"] == "post"]
    measurement = [sample for sample in samples if sample["phase_hint"] == "measurement"]
    guard = numeric["attribution_guard_ns"]
    output: dict[str, Any] = {
        "phase4_schema_valid": 1,
        "phase4_compact_present": int(compact_present),
        "phase4_dropped_samples": dropped,
    }
    output.update(phase4_distribution("phase4_pre", pre, samples, numeric["pre_start_ns"], numeric["pre_end_ns"]))
    output.update(phase4_distribution("phase4_post", post, samples, numeric["post_start_ns"], numeric["post_end_ns"]))
    if compact_present:
        compact_start = numeric["compact_request_start_ns"]
        compact_end = numeric["compact_response_end_ns"]
        overlap = [s for s in measurement if s["request_start_ns"] <= compact_end and s["request_end_ns"] >= compact_start]
        guarded_start = max(0, compact_start - guard)
        guarded_end = compact_end + guard
        refill_start = guarded_end
        guarded = [s for s in measurement if s["request_start_ns"] <= guarded_end and s["request_end_ns"] >= guarded_start]
    else:
        compact_start = numeric["accepted_epoch_transition_ns"]
        compact_end = compact_start
        overlap = []
        guarded_start = compact_start
        guarded_end = compact_end
        refill_start = compact_end + guard
        guarded = []
    refill = [
        sample for sample in measurement
        if sample["request_start_ns"] > refill_start
        and sample["request_start_ns"] <= numeric["reader_window_end_ns"]
    ]
    output.update(phase4_distribution("phase4_compact_overlap", overlap, samples, compact_start, compact_end))
    output.update(phase4_distribution("phase4_compact_guarded", guarded, samples, guarded_start, guarded_end))
    output.update(phase4_distribution("phase4_refill", refill, samples, refill_start, numeric["reader_window_end_ns"]))
    return output


def fmt_float(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}{suffix}"


def fmt_whole_or_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    rounded = round(value)
    if abs(value - rounded) < 0.005:
        return str(int(rounded))
    return fmt_float(value)


def fmt_concurrent_read_rps(row: dict[str, Any]) -> str:
    read_rps = row.get("driver_concurrent_read_requests_per_second_median")
    rendered = fmt_float(read_rps)
    target_rps = row.get("driver_concurrent_target_rps_median")
    if target_rps is None or target_rps <= 0:
        return rendered
    note = f"cap {fmt_whole_or_float(target_rps)} total"
    if read_rps is not None:
        note += f"; read share {(read_rps / target_rps) * 100:.1f}%"
    return f"{rendered} ({note})"


def fmt_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = float(value)
    unit = units[0]
    for unit in units:
        if scaled < 1024 or unit == units[-1]:
            break
        scaled /= 1024
    return f"{scaled:.2f} {unit}"


def fmt_rate(value: float | None, suffix: str = "/s") -> str:
    if value is None:
        return "n/a"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = float(value)
    unit = units[0]
    for unit in units:
        if scaled < 1024 or unit == units[-1]:
            break
        scaled /= 1024
    return f"{scaled:.2f} {unit}{suffix}"


def fmt_on_off(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "on" if value >= 0.5 else "off"


def fmt_vcl_shape(value: str | None) -> str:
    """Render BENCH_STALE_DELIVER as the VCL shape it selects."""
    if not value:
        return "unrecorded"
    if value in {"1", "on", "yes", "true"}:
        return "1 (two-call: vcl_hit + vcl_deliver)"
    return "0 (one-call: vcl_hit)"


def fmt_ok_fail(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "ok" if value >= 0.5 else "fail"


def has_detached_free_metrics(
    row: dict[str, Any], usec_prefix: str, max_prefix: str | None = None
) -> bool:
    max_prefix = usec_prefix if max_prefix is None else max_prefix
    return any(
        row.get(f"{usec_prefix}cachetag_{counter}_usec_median") is not None
        or row.get(f"{max_prefix}cachetag_{counter}_max_usec_median") is not None
        for counter in PHASE4_DETACHED_FREE_COUNTERS
    )


def fmt_detached_free_metrics(
    row: dict[str, Any], usec_prefix: str, max_prefix: str | None = None
) -> str:
    max_prefix = usec_prefix if max_prefix is None else max_prefix
    parts = []
    for counter in PHASE4_DETACHED_FREE_COUNTERS:
        parts.append(
            f"{counter}_usec={fmt_float(row.get(f'{usec_prefix}cachetag_{counter}_usec_median'), 'us')}"
        )
        parts.append(
            f"{counter}_max_usec={fmt_float(row.get(f'{max_prefix}cachetag_{counter}_max_usec_median'), 'us')}"
        )
    return " ".join(parts)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return percentile(sorted(values), 0.50)


def safe_members(tar: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in tar.getmembers():
        target = Path(member.name)
        if target.is_absolute() or ".." in target.parts:
            raise SystemExit(f"refusing unsafe tar member: {member.name}")
        yield member


def expand_inputs(paths: list[Path]) -> tuple[list[Path], tempfile.TemporaryDirectory[str] | None]:
    result_paths: list[Path] = []
    tempdir: tempfile.TemporaryDirectory[str] | None = None
    for path in paths:
        if path.is_dir():
            result_paths.append(path)
            continue
        if not tarfile.is_tarfile(path):
            raise SystemExit(f"not a directory or tarball: {path}")
        if tempdir is None:
            tempdir = tempfile.TemporaryDirectory()
        with tarfile.open(path) as tar:
            tar.extractall(tempdir.name, members=safe_members(tar))
        result_paths.append(Path(tempdir.name))
    return result_paths, tempdir


def find_result_dirs(root: Path) -> list[Path]:
    candidates = []
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        if (path / "metadata.env").exists() or (path / "remote-run.env").exists() or list(path.glob("*.run-*.time")):
            candidates.append(path)
    if (root / "metadata.env").exists() or (root / "remote-run.env").exists() or list(root.glob("*.run-*.time")):
        candidates.append(root)
    return sorted(set(candidates))


def hardware_fingerprint(result_dir: Path) -> str:
    system = parse_kv(result_dir / "host-system.env")
    if not system:
        system = parse_kv(result_dir / "system.env")
    cpu = system.get("cpu_model", "unknown-cpu")
    nproc = system.get("nproc", "unknown-nproc")
    mem = system.get("mem_total_kb", "unknown-mem")
    kernel = system.get("kernel", "unknown-kernel")
    metadata = parse_kv(result_dir / "metadata.env")
    remote = parse_kv(result_dir / "remote-run.env")
    cohort = metadata.get("benchmark_cohort_fingerprint") or remote.get(
        "benchmark_cohort_fingerprint", "unknown-cohort"
    )
    return f"{cpu} | nproc={nproc} | mem_kb={mem} | kernel={kernel} | cohort={cohort}"


def workload_from_time(path: Path) -> tuple[str, int] | None:
    stem = path.name
    marker = ".run-"
    if marker not in stem or not stem.endswith(".time"):
        return None
    workload, rest = stem.split(marker, 1)
    try:
        run = int(rest.removesuffix(".time"))
    except ValueError:
        return None
    return workload, run


# Historical artifact parser: keep these retired workload spellings readable for
# archived comparison bundles. The generator no longer emits them.
def workload_implementation(workload: str) -> str:
    if workload.startswith("cachetag_purgemap_nostale_"):
        return "cachetag-purgemap-nostale"
    if workload.startswith("cachetag_epoch_nostale_"):
        return "cachetag-epoch-nostale"
    if workload.startswith("cachetag_purgemap_"):
        return "cachetag-purgemap"
    if workload.startswith("cachetag_epoch_"):
        return "cachetag-epoch"
    if workload.startswith("cachetag_nostale_"):
        return "cachetag-nostale"
    if workload.startswith("cachetag_"):
        return "cachetag"
    if workload.startswith("xkey_"):
        return "xkey"
    if workload.startswith("noindex_"):
        return "noindex"
    return "unknown"


def workload_profile(workload: str) -> str:
    for prefix in (
        "cachetag_purgemap_nostale_",
        "cachetag_epoch_nostale_",
        "cachetag_purgemap_",
        "cachetag_epoch_",
        "cachetag_nostale_",
        "cachetag_",
        "xkey_",
    ):
        if workload.startswith(prefix):
            return workload.removeprefix(prefix)
    if workload == "noindex_load":
        return "noindex"
    if workload == "noindex_concurrent":
        return "concurrent"
    return workload


# Historical artifact parser block. These retired workload spellings are read
# only so archived comparison bundles remain intelligible; fresh generation
# never emits them.
def historical_cachetag_backend_for_arm(row: dict[str, Any]) -> str | None:
    implementation = str(row.get("implementation", ""))
    if implementation in {"cachetag-epoch", "cachetag-epoch-nostale"}:
        return "epoch"
    if implementation in {"cachetag-purgemap", "cachetag-purgemap-nostale"}:
        return "purgemap"
    return None


def arm_workload_keys(row: dict[str, Any], explicit_backends_by_profile: dict[str, set[str]]) -> list[str]:
    implementation = str(row.get("implementation", ""))
    workload = str(row["workload"])
    profile = str(row.get("profile") or workload_profile(workload))
    backend = historical_cachetag_backend_for_arm(row)
    if backend is not None:
        return [workload]
    if implementation in {"cachetag", "cachetag-nostale", "xkey"} and profile in {
        "mostly_unique_bound",
        "mostly_shared_bound",
        "ordinary_body_4k",
    }:
        return [profile]
    if implementation in {"cachetag", "cachetag-nostale"}:
        explicit_backends = explicit_backends_by_profile.get(profile, set())
        if explicit_backends:
            middle = "nostale_" if implementation == "cachetag-nostale" else ""
            return [f"cachetag_{backend}_{middle}{profile}" for backend in sorted(explicit_backends)]
    return [workload]


def workload_stats_files(result_dir: Path, workload: str, run: int) -> list[Path]:
    candidates = sorted(result_dir.glob(f"{workload}*.run-{run}.stats"))
    if not candidates and run == 1:
        candidates = [
            path
            for path in sorted(result_dir.glob(f"{workload}*.stats"))
            if ".run-" not in path.name
        ]
    priority = {
        f"{workload}_pre_purge.run-{run}.stats": 0,
        f"{workload}_post.run-{run}.stats": 1,
        f"{workload}.run-{run}.stats": 2,
        f"{workload}_post_purge.run-{run}.stats": 3,
    }
    return sorted(candidates, key=lambda path: (priority.get(path.name, 50), path.name))


def workload_driver_values(result_dir: Path, workload: str, run: int) -> dict[str, str]:
    values: dict[str, str] = {}
    error_count = 0
    saw_error_count = False
    error_messages: list[str] = []
    paths = sorted(result_dir.glob(f"{workload}*.run-{run}.driver"))
    if not paths and run == 1:
        paths = [
            path
            for path in sorted(result_dir.glob(f"{workload}*.driver"))
            if ".run-" not in path.name
        ]
    for path in paths:
        parsed = parse_kv(path)
        if not parsed:
            continue
        for key, value in parsed.items():
            if key == "driver_errors":
                try:
                    error_count += int(float(value))
                    saw_error_count = True
                except ValueError:
                    pass
            elif key == "driver_error":
                error_messages.append(f"{path.name}: {value}")
            else:
                values[key] = value
    if saw_error_count:
        values["driver_errors"] = str(error_count)
    if error_messages:
        values["driver_error"] = " | ".join(error_messages)
    return values


def tracked_memory_bytes(stats: dict[str, int], implementation: str) -> int | None:
    if implementation.startswith("cachetag"):
        if historical_cachetag_implementation(implementation):
            historical = historical_cachetag_memory_metrics(stats)
            tracked = historical.get("component_bytes")
            return int(tracked) if tracked is not None else stat_suffix(stats, "index_memory_bytes")
        return stat_suffix(stats, "index_memory_bytes")
    if implementation == "xkey":
        for key, value in stats.items():
            if key.endswith(".g_bytes"):
                return value
    return None


def stat_suffix(stats: dict[str, int], suffix: str) -> int | None:
    for key, value in stats.items():
        if key.endswith("." + suffix):
            return value
    return None


def cachetag_counter(stats: dict[str, int], suffix: str) -> int | None:
    return stat_suffix(stats, suffix)


def buddy_counter(stats: dict[str, int], suffix: str) -> int | None:
    return stat_suffix(stats, suffix)


def fellow_counter(stats: dict[str, int], suffix: str) -> int | None:
    values = [
        value
        for key, value in stats.items()
        if key.startswith("FELLOW.") and key.endswith("." + suffix)
    ]
    return sum(values) if values else None


def historical_cachetag_implementation(implementation: str) -> bool:
    """Identify retired workload names accepted only for archive parsing."""
    return implementation in {
        "cachetag-epoch",
        "cachetag-epoch-nostale",
        "cachetag-purgemap",
        "cachetag-purgemap-nostale",
    }


def historical_cachetag_memory_metrics(stats: dict[str, int]) -> dict[str, int | float | None]:
    """Parse retired index counters for archived benchmark bundles only."""
    mem_key_bytes = cachetag_counter(stats, "mem_key_bytes")
    mem_key_id_table_bytes = cachetag_counter(stats, "mem_key_id_table_bytes")
    mem_key_metadata_total_bytes = (
        mem_key_bytes + (mem_key_id_table_bytes or 0)
        if mem_key_bytes is not None
        else None
    )
    mem_object_table_bytes = cachetag_counter(stats, "mem_object_table_bytes")
    mem_posting_bytes = cachetag_counter(stats, "mem_posting_bytes")
    mem_reverse_bytes = cachetag_counter(stats, "mem_reverse_bytes")
    component_bytes = sum(
        value
        for value in (
            mem_key_metadata_total_bytes,
            mem_object_table_bytes,
            mem_posting_bytes,
            mem_reverse_bytes,
        )
        if value is not None
    )
    component_seen = any(
        value is not None
        for value in (
            mem_key_metadata_total_bytes,
            mem_object_table_bytes,
            mem_posting_bytes,
            mem_reverse_bytes,
        )
    )
    return {
        "mem_index_base_bytes": cachetag_counter(stats, "mem_index_base_bytes"),
        "mem_side_table_bytes": cachetag_counter(stats, "mem_side_table_bytes"),
        "mem_shard_table_bytes": cachetag_counter(stats, "mem_shard_table_bytes"),
        "mem_epoch_slot_bytes": cachetag_counter(stats, "mem_epoch_slot_bytes"),
        "mem_epoch_slot_used_slots": cachetag_counter(stats, "mem_epoch_slot_used_slots"),
        "mem_epoch_slot_capacity_slots": cachetag_counter(stats, "mem_epoch_slot_capacity_slots"),
        "mem_epoch_slot_slack_slots": cachetag_counter(stats, "mem_epoch_slot_slack_slots"),
        "mem_key_bytes": mem_key_bytes,
        "mem_key_id_table_bytes": mem_key_id_table_bytes,
        "mem_key_metadata_total_bytes": mem_key_metadata_total_bytes,
        "mem_object_table_bytes": mem_object_table_bytes,
        "mem_posting_bytes": mem_posting_bytes,
        "mem_reverse_bytes": mem_reverse_bytes,
        "component_bytes": component_bytes if component_seen else None,
        "index_non_key_bytes": subtract(
            stat_suffix(stats, "index_memory_bytes"), mem_key_metadata_total_bytes
        ),
        "mem_keys": cachetag_counter(stats, "mem_keys"),
        "keys_total": cachetag_counter(stats, "keys_total"),
        "mem_object_table_capacity": cachetag_counter(stats, "mem_object_table_capacity"),
        "mem_object_table_high_water_slots": cachetag_counter(stats, "mem_object_table_high_water_slots"),
        "mem_object_table_slack_slots": cachetag_counter(stats, "mem_object_table_slack_slots"),
        "mem_posting_segment_used_slots": cachetag_counter(stats, "mem_posting_segment_used_slots"),
        "mem_posting_segment_capacity_slots": cachetag_counter(stats, "mem_posting_segment_capacity_slots"),
        "mem_posting_segment_slack_slots": cachetag_counter(stats, "mem_posting_segment_slack_slots"),
        "mem_key_id_table_used_slots": cachetag_counter(stats, "mem_key_id_table_used_slots"),
        "mem_key_id_table_capacity_slots": cachetag_counter(stats, "mem_key_id_table_capacity_slots"),
        "mem_key_id_table_slack_slots": cachetag_counter(stats, "mem_key_id_table_slack_slots"),
        "mem_compact_full_calls": cachetag_counter(stats, "mem_compact_full_calls"),
        "mem_compact_incremental_calls": cachetag_counter(stats, "mem_compact_incremental_calls"),
        "mem_compaction_handles_scanned": cachetag_counter(stats, "mem_compaction_handles_scanned"),
        "mem_compaction_handles_validated": cachetag_counter(stats, "mem_compaction_handles_validated"),
        "mem_compaction_handles_kept": cachetag_counter(stats, "mem_compaction_handles_kept"),
        "mem_key_gc_pin_release": cachetag_counter(stats, "mem_key_gc_pin_release"),
        "mem_key_gc_incremental": cachetag_counter(stats, "mem_key_gc_incremental"),
        "mem_key_gc_full_compact": cachetag_counter(stats, "mem_key_gc_full_compact"),
        "mem_validation_obj_lock_acquisitions": cachetag_counter(
            stats, "mem_validation_obj_lock_acquisitions"
        ),
        "stale_fast_epoch_slot_hits": cachetag_counter(stats, "stale_fast_epoch_slot_hits"),
        "stale_epoch_slot_fallbacks": cachetag_counter(stats, "stale_epoch_slot_fallbacks"),
    }


def phase_stats_files(result_dir: Path, workload: str, run: int) -> dict[str, Path]:
    phases = {
        "post_load": f"{workload}_post_load.run-{run}.stats",
        "post_restart": f"{workload}_post_restart.run-{run}.stats",
        "post_first_touch": f"{workload}_post_first_touch.run-{run}.stats",
        "post_cold_purge": f"{workload}_post_cold_purge.run-{run}.stats",
        "post_hot_purge": f"{workload}_post_hot_purge.run-{run}.stats",
        "phase4_start": f"{workload}_phase4_start.run-{run}.stats",
        "phase4_pre": f"{workload}_phase4_pre.run-{run}.stats",
        "phase4_compact": f"{workload}_phase4_compact.run-{run}.stats",
        "phase4_refill": f"{workload}_phase4_refill.run-{run}.stats",
        "phase4_post": f"{workload}_phase4_post.run-{run}.stats",
        "phase5_hold_fetch_start": f"{workload}_phase5_hold_fetch_start.run-{run}.stats",
        "phase5_hold_active": f"{workload}_phase5_hold_active.run-{run}.stats",
        "phase5_held_load_start": f"{workload}_phase5_held_load_start.run-{run}.stats",
        "phase5_held_load_end": f"{workload}_phase5_held_load_end.run-{run}.stats",
        "phase5_shutdown_ready": f"{workload}_phase5_shutdown_ready.run-{run}.stats",
        "phase5_pre_release": f"{workload}_phase5_pre_release.run-{run}.stats",
        "phase5_released": f"{workload}_phase5_released.run-{run}.stats",
    }
    found: dict[str, Path] = {}
    for phase, name in phases.items():
        path = result_dir / name
        if path.exists():
            found[phase] = path
    return found


def ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def scaled(value: int | float | None, factor: float) -> float | None:
    return None if value is None else float(value) * factor


def subtract(a: int | float | None, b: int | float | None) -> float | None:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def subtract_optional(after: int | None, before: int | None) -> int | None:
    if after is None or before is None:
        return None
    return after - before


def driver_cycle_sum(values: dict[str, str], suffix: str) -> int | None:
    prefix = "driver_cycle_"
    total = 0
    seen = False
    for key, value in values.items():
        if key.startswith(prefix) and key.endswith(suffix):
            parsed = as_int(values, key)
            if parsed is None:
                continue
            total += parsed
            seen = True
    return total if seen else None


PHASE6_TAIL_MIN_SECONDS = 0.010
PHASE6_TAIL_P99_RATIO = 10.0
PHASE6_RSS_CLIFF_KB = 1024 * 1024


def phase6_cycle_rows(
    result_dir: Path, workload: str, run: int, driver: dict[str, str]
) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []
    index = 0
    while True:
        p99 = as_float(driver, f"driver_phase6_cycle_{index}_latency_p99_seconds")
        max_seconds = as_float(driver, f"driver_phase6_cycle_{index}_latency_max_seconds")
        if p99 is None and max_seconds is None:
            break
        memory = parse_kv(
            result_dir / f"{workload}_phase6_cycle_{index:02d}_end.run-{run}.phase6_memory"
        )
        cycles.append(
            {
                "cycle": index,
                "p99_seconds": p99,
                "max_seconds": max_seconds,
                "worker_rss_kb": as_int(memory, "selected_vmrss_kb"),
                "allocator_environment": memory.get("allocator_environment", ""),
                "minflt_delta": as_int(memory, "proc_minflt_delta"),
            }
        )
        index += 1
    return cycles


def phase6_interpretation_warnings(
    workload: str, run: int, cycles: list[dict[str, Any]]
) -> list[str]:
    warnings: list[str] = []
    if not cycles:
        return warnings
    row_name = f"{workload}.run-{run}"
    environments = sorted(
        {c["allocator_environment"] for c in cycles if c["allocator_environment"]}
    )
    if any("junk:true" in env for env in environments):
        warnings.append(
            f"WARNING [BR-005] {row_name}: non-production allocator config "
            f"({'; '.join(environments)}); vinyltest sets abort:true,junk:true for "
            "children, so do not extrapolate absolute latency or RSS to production."
        )
    elif not environments:
        warnings.append(
            f"note [BR-005] {row_name}: allocator environment not captured; vinyltest "
            "children default to MALLOC_CONF=abort:true,junk:true, so treat absolute "
            "latency and RSS as non-production."
        )
    previous_rss: int | None = None
    for cycle in cycles:
        rss = cycle["worker_rss_kb"]
        p99 = cycle["p99_seconds"]
        max_seconds = cycle["max_seconds"]
        if (
            max_seconds is not None
            and p99 is not None
            and max_seconds >= PHASE6_TAIL_MIN_SECONDS
            and max_seconds >= PHASE6_TAIL_P99_RATIO * p99
            and previous_rss is not None
            and rss is not None
            and previous_rss - rss >= PHASE6_RSS_CLIFF_KB
        ):
            warnings.append(
                f"WARNING [BR-006] {row_name}: cycle {cycle['cycle']} max "
                f"{max_seconds * 1000.0:.3f}ms is >={PHASE6_TAIL_P99_RATIO:.0f}x p99 with "
                f"a >=1GiB worker RSS drop ({previous_rss} -> {rss} KiB); allocator "
                "decay-purge signature, consult benchmarks/rules/ BR-006 before "
                "attributing the tail to VMOD or Vinyl code."
            )
        if rss is not None:
            previous_rss = rss
    return warnings


def comparison_contract_active(result_dir: Path) -> bool:
    """Whether this artifact opts into a fail-closed comparison contract.

    Historical research artifacts predate the contract and remain readable as
    historical diagnostics.  New comparative rows must set this marker rather
    than quietly inheriting the permissive historical parser.
    """
    metadata = parse_kv(result_dir / "metadata.env")
    remote = parse_kv(result_dir / "remote-run.env")
    return metadata.get("benchmark_contract") in {"comparison-v1", "interning-screen-v1"} or remote.get(
        "benchmark_contract"
    ) in {"comparison-v1", "interning-screen-v1"}


def cache_main_capture_valid(result_dir: Path, workload: str, run: int, endpoint: str) -> tuple[int, str]:
    # Endpoint captures are named independently of .run-N because they are
    # written within one VTC run.  Keep the run in the error reason so rejected
    # ledgers remain unambiguous when a result directory has several repeats.
    prefix = result_dir / f"{workload}.run-{run}.{endpoint}.cache-main"
    identity = parse_kv(Path(str(prefix) + ".identity"))
    rollup = Path(str(prefix) + ".smaps_rollup")
    required = {
        "schema": "cache-main-memory-v1",
        "endpoint": endpoint,
        "selected_comm": "cache-main",
        "identity_valid": "1",
        "identity_post_capture_valid": "1",
    }
    for key, expected in required.items():
        if identity.get(key) != expected:
            return 0, f"capture_{endpoint}_{key}_invalid:run-{run}"
    if (
        not identity.get("selected_pid", "").isdigit()
        or int(identity["selected_pid"]) <= 0
        or not identity.get("selected_starttime_ticks", "").isdigit()
        or int(identity["selected_starttime_ticks"]) <= 0
    ):
        return 0, f"capture_{endpoint}_pid_identity_missing:run-{run}"
    if Path(identity.get("selected_exe", "")).name != "vinyld" or not identity.get("boot_id"):
        return 0, f"capture_{endpoint}_process_identity_missing:run-{run}"
    if not rollup.is_file() or not rollup.read_text(encoding="utf-8", errors="replace").strip():
        return 0, f"capture_{endpoint}_smaps_rollup_missing:run-{run}"
    if not re.search(r"^Pss:\s+[1-9][0-9]*\s+kB$", rollup.read_text(encoding="utf-8", errors="replace"), re.MULTILINE):
        return 0, f"capture_{endpoint}_pss_invalid:run-{run}"
    return 1, "ok"


def _capture_identity(result_dir: Path, workload: str, run: int, endpoint: str) -> dict[str, str]:
    prefix = result_dir / f"{workload}.run-{run}.{endpoint}.cache-main"
    return parse_kv(Path(str(prefix) + ".identity"))


def cache_main_capture_pss_kb(result_dir: Path, workload: str, run: int, endpoint: str) -> int | None:
    path = result_dir / f"{workload}.run-{run}.{endpoint}.cache-main.smaps_rollup"
    if not path.is_file():
        return None
    match = re.search(
        r"^Pss:\s+(\d+)\s+kB$",
        path.read_text(encoding="utf-8", errors="replace"),
        re.MULTILINE,
    )
    return int(match.group(1)) if match else None


def _valid_sha256(value: str | None) -> bool:
    return bool(value and SHA256_VALUE_RE.fullmatch(value))


def _required_hash(reasons: list[str], values: dict[str, str], key: str, *, allow_none: bool = False) -> None:
    value = values.get(key)
    if allow_none and value == "none":
        return
    if not _valid_sha256(value):
        reasons.append(f"provenance_missing:{key}")


def _comparison_latency_sampling_validity(
    result_dir: Path, run: int, driver: dict[str, str], reasons: list[str]
) -> None:
    prefixes = sorted(
        key.removesuffix("_latency_sampling_method")
        for key in driver
        if key.endswith("_latency_sampling_method")
    )
    if not prefixes:
        reasons.append("latency_sampling_telemetry_missing")
        return
    for prefix in prefixes:
        required = (
            "latency_sampling_limit", "latency_sampling_seen", "latency_sampling_dropped",
            "latency_samples", "latency_samples_path",
        )
        if driver.get(f"{prefix}_latency_sampling_method") != "deterministic-reservoir-v1":
            reasons.append(f"latency_sampling_method_invalid:{prefix}")
        for suffix in required:
            if f"{prefix}_{suffix}" not in driver or not driver[f"{prefix}_{suffix}"].strip():
                reasons.append(f"latency_sampling_metric_missing:{prefix}_{suffix}")
        limit = as_int(driver, f"{prefix}_latency_sampling_limit")
        seen = as_int(driver, f"{prefix}_latency_sampling_seen")
        dropped = as_int(driver, f"{prefix}_latency_sampling_dropped")
        samples = as_int(driver, f"{prefix}_latency_samples")
        if limit is None or limit <= 0:
            reasons.append(f"latency_sampling_limit_invalid:{prefix}")
        if seen is None or seen < 0 or dropped is None or dropped < 0 or samples is None or samples < 0:
            reasons.append(f"latency_sampling_counts_invalid:{prefix}")
        elif samples > seen or dropped < seen - samples:
            reasons.append(f"latency_sampling_counts_inconsistent:{prefix}")
        raw_path = driver.get(f"{prefix}_latency_samples_path", "")
        if raw_path:
            raw_name = Path(raw_path).name
            if "." in raw_name:
                stem, extension = raw_name.split(".", 1)
                captured = result_dir / f"{stem}.run-{run}.{extension}"
            else:
                captured = result_dir / raw_name
            if not captured.is_file():
                reasons.append(f"latency_samples_artifact_missing:{prefix}")
            else:
                raw_lines = captured.read_text(encoding="utf-8", errors="replace").splitlines()
                if not raw_lines or raw_lines[0] != "seconds" or samples is None or len(raw_lines) - 1 != samples:
                    reasons.append(f"latency_samples_artifact_invalid:{prefix}")
        for suffix in ("latency_p50_seconds", "latency_p95_seconds", "latency_p99_seconds", "latency_max_seconds"):
            value = as_float(driver, f"{prefix}_{suffix}")
            if samples is not None and samples > 0 and (value is None or not math.isfinite(value) or value < 0):
                reasons.append(f"latency_sampling_value_invalid:{prefix}_{suffix}")


def _comparison_pacing_validity(driver: dict[str, str], reasons: list[str]) -> None:
    prefixes = sorted(
        key.removesuffix("_scheduled_slots")
        for key in driver
        if key.endswith("_scheduled_slots")
    )
    if not prefixes:
        reasons.append("pacing_telemetry_missing")
        return
    for prefix in prefixes:
        missing = [f"{prefix}_{suffix}" for suffix in COMPARISON_PACING_FIELDS if f"{prefix}_{suffix}" not in driver]
        reasons.extend(f"pacing_metric_missing:{field}" for field in missing)
        if missing:
            continue
        values = {suffix: as_float(driver, f"{prefix}_{suffix}") for suffix in COMPARISON_PACING_FIELDS}
        if any(value is None or not math.isfinite(value) or value < 0 for value in values.values()):
            reasons.append(f"pacing_metric_invalid:{prefix}")
            continue
        scheduled = int(values["scheduled_slots"] or 0)
        executed = int(values["executed_slots"] or 0)
        skipped = int(values["skipped_slots"] or 0)
        errors = int(values["errors"] or 0)
        if scheduled <= 0 or scheduled != executed + skipped:
            reasons.append(f"pacing_slot_accounting_invalid:{prefix}")
        if errors > executed:
            reasons.append(f"pacing_error_count_invalid:{prefix}")


def _comparison_phase_cpu_validity(time_values: dict[str, str], reasons: list[str]) -> None:
    """Require complete phase-aligned process CPU attribution for comparisons."""
    schema = time_values.get("system_phase_cpu_telemetry_schema")
    if schema is None:
        reasons.append("phase_cpu_telemetry_schema_missing")
    elif schema != "phase-aligned-process-cpu-v1":
        reasons.append("phase_cpu_telemetry_schema_invalid")
    for phase in ("load", "warm"):
        samples = as_int(time_values, f"system_phase_{phase}_samples")
        wall_seconds = as_float(time_values, f"system_phase_{phase}_wall_seconds")
        if samples is None:
            reasons.append(f"phase_cpu_metric_missing:{phase}_samples")
        elif samples <= 0:
            reasons.append(f"phase_cpu_metric_invalid:{phase}_samples")
        if wall_seconds is None:
            reasons.append(f"phase_cpu_metric_missing:{phase}_wall_seconds")
        elif not math.isfinite(wall_seconds) or wall_seconds <= 0:
            reasons.append(f"phase_cpu_metric_invalid:{phase}_wall_seconds")
        for process in ("cache_main", "driver", "backend"):
            key = f"system_phase_{phase}_{process}_cpu_seconds"
            value = as_float(time_values, key)
            if value is None:
                reasons.append(f"phase_cpu_metric_missing:{phase}_{process}_cpu_seconds")
            elif not math.isfinite(value) or value < 0:
                reasons.append(f"phase_cpu_metric_invalid:{phase}_{process}_cpu_seconds")


def _comparison_fixed_work_validity(driver: dict[str, str], reasons: list[str]) -> None:
    for key in (
        "driver_load_fixed_work_seconds",
        "driver_load_pending_drain_seconds",
    ):
        value = as_float(driver, key)
        if value is None:
            reasons.append(f"fixed_work_metric_missing:{key}")
        elif not math.isfinite(value) or value < 0:
            reasons.append(f"fixed_work_metric_invalid:{key}")
    warm_hits = as_int(driver, "driver_warm_hits")
    if warm_hits is None:
        reasons.append("warm_hit_metric_missing")
    elif warm_hits <= 0:
        reasons.append("warm_hit_metric_invalid")


def comparison_contract_validity(
    result_dir: Path,
    workload: str,
    run: int,
    time_values: dict[str, str],
    driver: dict[str, str],
    stats: dict[str, int],
) -> tuple[int, str]:
    """Return scoped, retained rejection reasons for strict comparison rows."""
    if not comparison_contract_active(result_dir):
        return 1, "not_applicable"
    metadata = parse_kv(result_dir / "metadata.env")
    remote = parse_kv(result_dir / "remote-run.env")
    provenance = parse_kv(result_dir / "build-provenance.env")
    contract = metadata.get("benchmark_contract") or remote.get("benchmark_contract")
    interning_screen = contract == "interning-screen-v1"
    reasons: list[str] = []
    required_provenance = [
        "build_provenance_version",
        "vinyl_build_input_sha256",
        "cachetag_build_input_sha256",
        "vinyl_binary_sha256",
        "cachetag_binary_sha256",
        "build_commands_sha256",
        "dockerfile_sha256",
        "docker_image_id",
    ]
    if not interning_screen:
        required_provenance[3:3] = [
            "xkey_build_input_sha256",
            "xkey_compat_artifact_sha256",
            "xkey_config_sha256",
        ]
        required_provenance.insert(8, "xkey_binary_sha256")
    for key in required_provenance:
        if key == "build_provenance_version":
            accepted_versions = {"4"} if interning_screen else {"3", "4"}
            if provenance.get(key) not in accepted_versions:
                reasons.append(f"provenance_missing:{key}")
        elif key == "docker_image_id":
            if not provenance.get(key) or provenance.get(key) == "none":
                reasons.append(f"provenance_missing:{key}")
        else:
            _required_hash(reasons, provenance, key)
    if provenance.get("build_provenance_mode") != "strict" or provenance.get("build_provenance_eligible") != "1":
        reasons.append("provenance_not_comparison_eligible")
    for source in (("cachetag", "vinyl") if interning_screen else ("cachetag", "vinyl", "xkey")):
        if provenance.get(f"{source}_dirty_state") != "clean":
            reasons.append(f"provenance_{source}_not_clean")
    if interning_screen:
        contract_value = lambda key: metadata.get(key) or remote.get(key)
        if contract_value("run_xkey") != "0":
            reasons.append("interning_screen_xkey_arm_present")
        if contract_value("run_noindex") != "0":
            reasons.append("interning_screen_noindex_arm_present")
        set_interning = contract_value("bench_set_interning")
        configure_args = contract_value("cachetag_configure_args")
        expected_configure_args = {
            "0": "--disable-set-interning",
            "1": "--enable-set-interning",
        }
        if set_interning not in expected_configure_args:
            reasons.append("interning_screen_set_interning_invalid")
        elif configure_args != expected_configure_args[set_interning]:
            reasons.append("interning_screen_configure_args_invalid")
        if provenance.get("bench_set_interning") != set_interning:
            reasons.append("interning_screen_provenance_set_interning_mismatch")
        if provenance.get("cachetag_configure_args") != configure_args:
            reasons.append("interning_screen_provenance_configure_args_mismatch")
    fixture_name = metadata.get("fixture_name") or remote.get("fixture_name") or driver.get("driver_fixture_name")
    declared_fixture_fingerprint = metadata.get("fixture_fingerprint") or remote.get("fixture_fingerprint")
    fixture_fingerprint = declared_fixture_fingerprint
    manifest_path = result_dir / "fixtures" / f"{fixture_name}.manifest.json" if fixture_name else None
    if manifest_path and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = manifest.get("expected", {})
            manifest_fingerprint = expected.get("fixture_fingerprint")
            if declared_fixture_fingerprint and declared_fixture_fingerprint != manifest_fingerprint:
                reasons.append("fixture_fingerprint_declaration_mismatch")
            fixture_fingerprint = manifest_fingerprint
            for field, driver_key in (("objects", "driver_fixture_expected_objects"), ("relationships", "driver_fixture_expected_relationships")):
                expected_value = expected.get(field)
                actual_value = as_int(driver, driver_key)
                if expected_value is not None and actual_value != expected_value:
                    reasons.append(f"fixture_{field}_mismatch")
        except (OSError, json.JSONDecodeError):
            reasons.append("fixture_manifest_invalid")
    elif fixture_name:
        reasons.append("fixture_manifest_missing")
    if not fixture_name:
        reasons.append("fixture_name_missing")
    if not fixture_fingerprint:
        reasons.append("fixture_fingerprint_missing")
    elif driver.get("driver_fixture_fingerprint") != fixture_fingerprint:
        reasons.append("fixture_fingerprint_mismatch")
    if driver.get("driver_fixture_name") != fixture_name:
        reasons.append("fixture_name_mismatch")
    expected_objects = as_int(driver, "driver_fixture_expected_objects")
    loaded_objects = as_int(driver, "driver_load_requests")
    if expected_objects is None or expected_objects <= 0 or loaded_objects is None or loaded_objects != expected_objects:
        reasons.append("work_volume_invalid")
    expected_relationships = as_int(driver, "driver_fixture_expected_relationships")
    if expected_relationships is None or expected_relationships <= 0:
        reasons.append("work_relationship_volume_invalid")
    backend_objects = as_int(driver, "driver_load_backend_objects")
    backend_expected = as_int(driver, "driver_load_backend_objects_expected")
    backend_validation = as_bool_int(driver, "driver_load_backend_objects_validation")
    if backend_objects is None or backend_expected is None or backend_validation != 1 or expected_objects is None:
        reasons.append("backend_work_volume_telemetry_missing")
    elif backend_objects != expected_objects or backend_expected != expected_objects:
        reasons.append("backend_work_volume_invalid")
    if as_int(driver, "driver_errors") is None:
        reasons.append("driver_telemetry_missing")
    elif as_int(driver, "driver_errors") != 0:
        reasons.append("driver_errors")
    if driver.get("driver_phase_telemetry_schema") != "phase-aligned-v1":
        reasons.append("phase_telemetry_incomplete")
    if driver.get("driver_pacing_schema") != "slot-skipping-v1":
        reasons.append("pacing_telemetry_incomplete")
    _comparison_pacing_validity(driver, reasons)
    _comparison_latency_sampling_validity(result_dir, run, driver, reasons)
    _comparison_fixed_work_validity(driver, reasons)
    for key, configured in (
        ("driver_runtime_gomaxprocs", metadata.get("bench_driver_gomaxprocs")),
        ("driver_runtime_gogc", metadata.get("bench_driver_gogc")),
        ("driver_runtime_gomemlimit", metadata.get("bench_driver_gomemlimit")),
    ):
        if not configured or driver.get(key) != configured:
            reasons.append(f"runtime_control_mismatch:{key}")
    for process in ("cache_process", "driver", "backend"):
        if not time_values.get(f"system_tracked_{process}_cpus_allowed_list"):
            reasons.append(f"cpu_placement_missing:{process}")
    if "swap_activity" not in time_values:
        reasons.append("swap_telemetry_missing")
    elif as_int(time_values, "swap_activity") is None:
        reasons.append("swap_telemetry_invalid")
    elif as_int(time_values, "swap_activity") != 0:
        reasons.append("swap_activity")
    if stat_suffix(stats, "n_lru_nuked") is None:
        reasons.append("eviction_telemetry_missing")
    elif stat_suffix(stats, "n_lru_nuked") != 0:
        reasons.append("unexpected_eviction")
    if stat_suffix(stats, "n_expired") is None:
        reasons.append("expiry_telemetry_missing")
    elif stat_suffix(stats, "n_expired") != 0:
        reasons.append("unexpected_expiry")
    for counter in ("threads", "thread_queue_len", "threads_limited", "threads_failed"):
        if stat_suffix(stats, counter) is None:
            reasons.append(f"worker_telemetry_missing:{counter}")
    if stat_suffix(stats, "threads_failed") not in (None, 0):
        reasons.append("worker_failures")
    _comparison_phase_cpu_validity(time_values, reasons)
    system = parse_kv(result_dir / "system.env")
    if not system:
        system = parse_kv(result_dir / "host-system.env")
    for key in COMPARISON_COHORT_FIELDS:
        if not system.get(key):
            reasons.append(f"cohort_field_missing:{key}")
    cohort = metadata.get("benchmark_cohort_fingerprint") or remote.get("benchmark_cohort_fingerprint")
    if not cohort:
        reasons.append("cohort_fingerprint_missing")
    required_endpoints = set((metadata.get("required_cache_main_endpoints") or "post_load,post_load_confirmation").split(","))
    required_endpoints.update(("post_load", "post_load_confirmation"))
    identities: list[tuple[str, str, str]] = []
    for endpoint in sorted(item.strip() for item in required_endpoints if item.strip()):
        valid, reason = cache_main_capture_valid(result_dir, workload, run, endpoint)
        if not valid:
            reasons.append(reason)
            continue
        identity = _capture_identity(result_dir, workload, run, endpoint)
        identities.append((identity.get("boot_id", ""), identity.get("selected_pid", ""), identity.get("selected_starttime_ticks", "")))
    if identities and len(set(identities)) != 1:
        reasons.append("capture_process_identity_changed")
    first_pss = cache_main_capture_pss_kb(result_dir, workload, run, "post_load")
    confirm_pss = cache_main_capture_pss_kb(result_dir, workload, run, "post_load_confirmation")
    if first_pss is None or confirm_pss is None:
        reasons.append("post_load_confirmation_missing")
    elif first_pss <= 0 or confirm_pss <= 0:
        reasons.append("post_load_confirmation_invalid")
    elif max(first_pss, confirm_pss) and abs(first_pss - confirm_pss) / max(first_pss, confirm_pss) > 0.005:
        reasons.append("post_load_confirmation_drift")
    return int(not reasons), "ok" if not reasons else ",".join(reasons)


def linear_slope(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xbar = sum(xs) / len(xs)
    ybar = sum(ys) / len(ys)
    denom = sum((x - xbar) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - xbar) * (y - ybar) for x, y in points) / denom


def largest_component(row: dict[str, Any]) -> tuple[str, float] | None:
    components = {
        "index": row.get("cachetag_index_memory_bytes"),
        "volatile_side_table": row.get("cachetag_volatile_side_table_bytes"),
        "purgemap": row.get("cachetag_purgemap_bytes"),
    }
    numeric = [(name, float(value)) for name, value in components.items() if value is not None]
    if not numeric:
        return None
    return max(numeric, key=lambda item: item[1])


def audit_memory_slopes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        for row in result.get("workload_summaries", []):
            loaded = row.get("loaded_objects_median")
            tracked = row.get("tracked_memory_bytes_median")
            if loaded is None or tracked is None:
                continue
            key = (str(row.get("implementation")), str(row.get("profile")))
            grouped.setdefault(key, []).append(row)

    audits: list[dict[str, Any]] = []
    component_fields = {
        "tracked": "tracked_memory_bytes_median",
        "volatile_side_table": "cachetag_volatile_side_table_bytes_median",
        "purgemap": "cachetag_purgemap_bytes_median",
    }
    for (implementation, profile), rows in sorted(grouped.items()):
        by_scale: dict[float, dict[str, Any]] = {}
        for row in rows:
            loaded = row.get("loaded_objects_median")
            if loaded is None:
                continue
            by_scale[float(loaded)] = row
        ordered = [by_scale[scale] for scale in sorted(by_scale)]
        if len(ordered) < 2:
            continue
        slopes: dict[str, float | None] = {}
        for label, field in component_fields.items():
            points = [
                (float(row["loaded_objects_median"]), float(row[field]))
                for row in ordered
                if row.get(field) is not None
            ]
            slopes[label] = linear_slope(points)
        max_row = ordered[-1]
        largest = largest_component(
            {
                "cachetag_index_memory_bytes": max_row.get("cachetag_index_memory_bytes_median"),
                "cachetag_volatile_side_table_bytes": max_row.get(
                    "cachetag_volatile_side_table_bytes_median"
                ),
                "cachetag_purgemap_bytes": max_row.get("cachetag_purgemap_bytes_median"),
            }
        )
        audits.append(
            {
                "implementation": implementation,
                "profile": profile,
                "scales": [int(row["loaded_objects_median"]) for row in ordered],
                "slopes_bytes_per_object": slopes,
                "largest_component_at_max_scale": (
                    {"name": largest[0], "bytes": largest[1]} if largest else None
                ),
            }
        )
    return audits


def workload_rows(result_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for time_path in sorted(result_dir.glob("*.run-*.time")):
        parsed = workload_from_time(time_path)
        if parsed is None:
            continue
        workload, run = parsed
        time_values = parse_kv(time_path)
        driver_values = workload_driver_values(result_dir, workload, run)
        sampling_validity = sampler_artifact_validity(time_path, time_values)
        system_memory_valid, system_memory_reason = system_memory_artifact_validity(
            time_values, int(sampling_validity["valid"])
        )
        raw_latency_valid, raw_latency_reason = stream1_raw_latency_validity(
            result_dir, workload, run, driver_values
        )
        workload_valid = int(as_int(time_values, "exit_code") == 0)
        overall_valid = int(
            workload_valid == 1
            and sampling_validity["valid"] == 1
            and system_memory_valid == 1
            and raw_latency_valid != 0
        )
        stats_files = workload_stats_files(result_dir, workload, run)
        stats: dict[str, int] = {}
        stats_file = ""
        if stats_files:
            stats_file = stats_files[0].name
            stats = parse_vsc_stats(stats_files[0])
        comparison_valid, comparison_reason = comparison_contract_validity(
            result_dir, workload, run, time_values, driver_values, stats
        )
        overall_valid = int(overall_valid == 1 and comparison_valid == 1)
        phase_stats = {
            phase: parse_vsc_stats(path)
            for phase, path in phase_stats_files(result_dir, workload, run).items()
        }
        shutdown_values = parse_kv(
            result_dir / f"{workload}_phase5_shutdown_cold.env"
        )
        implementation = workload_implementation(workload)
        concurrent_seconds = as_float(driver_values, "driver_concurrent_seconds")
        concurrent_reads = as_int(driver_values, "driver_concurrent_reads")
        concurrent_inserts = as_int(driver_values, "driver_concurrent_inserts")
        concurrent_purges = as_int(driver_values, "driver_concurrent_purges")
        loaded_objects = as_int(driver_values, "driver_load_requests")
        driver_cycle_backend_total = driver_cycle_sum(driver_values, "_backend_objects")
        user_seconds = as_float(time_values, "user_seconds")
        system_seconds = as_float(time_values, "system_seconds")
        vtc_cpu_seconds = (
            user_seconds + system_seconds
            if user_seconds is not None and system_seconds is not None
            else None
        )
        warm_hits = as_int(driver_values, "driver_warm_hits")
        load_backend_objects = as_int(driver_values, "driver_load_backend_objects")
        load_cache_main_cpu_seconds = as_float(
            time_values, "system_phase_load_cache_main_cpu_seconds"
        )
        warm_cache_main_cpu_seconds = as_float(
            time_values, "system_phase_warm_cache_main_cpu_seconds"
        )
        # Optional: present only when the row ran with BENCH_PERF_STAT and the
        # host could actually count. Missing means absent, never zero, and is
        # never a validity failure.
        warm_perf_stat_details = parse_perf_stat_csv_details(
            result_dir / f"{workload}.run-{run}.warm.perf-stat.csv"
        )
        warm_instructions_event = warm_perf_stat_details.get("instructions")
        warm_cycles_event = warm_perf_stat_details.get("cycles")
        warm_task_clock_event = warm_perf_stat_details.get("task-clock")
        warm_instructions = (
            int(float(warm_instructions_event["count"])) if warm_instructions_event else None
        )
        warm_cycles = int(float(warm_cycles_event["count"])) if warm_cycles_event else None
        warm_task_clock_seconds = perf_stat_seconds(warm_task_clock_event)
        warm_running_percents = [
            value
            for value in (
                perf_stat_running_percent(warm_instructions_event),
                perf_stat_running_percent(warm_cycles_event),
                perf_stat_running_percent(warm_task_clock_event),
            )
            if value is not None
        ]
        live_objects = stat_suffix(stats, "n_object")
        mem_edges = cachetag_counter(stats, "volatile_edges")
        if mem_edges is None:
            tags_per_object = as_int(driver_values, "driver_tags_per_object")
            mem_edges = (
                loaded_objects * tags_per_object
                if loaded_objects is not None and tags_per_object is not None
                else None
            )
        tracked = tracked_memory_bytes(stats, implementation)
        index_memory = cachetag_counter(stats, "index_memory_bytes")
        historical_metrics = (
            historical_cachetag_memory_metrics(stats)
            if historical_cachetag_implementation(implementation)
            else {}
        )
        mem_index_base_bytes = historical_metrics.get("mem_index_base_bytes")
        mem_side_table_bytes = historical_metrics.get("mem_side_table_bytes")
        mem_shard_table_bytes = historical_metrics.get("mem_shard_table_bytes")
        mem_epoch_slot_bytes = historical_metrics.get("mem_epoch_slot_bytes")
        mem_key_bytes = historical_metrics.get("mem_key_bytes")
        mem_key_id_table_bytes = historical_metrics.get("mem_key_id_table_bytes")
        mem_key_metadata_total_bytes = historical_metrics.get("mem_key_metadata_total_bytes")
        mem_object_table_bytes = historical_metrics.get("mem_object_table_bytes")
        mem_posting_bytes = historical_metrics.get("mem_posting_bytes")
        mem_reverse_bytes = historical_metrics.get("mem_reverse_bytes")
        component_bytes = historical_metrics.get("component_bytes") or 0
        component_seen = historical_metrics.get("component_bytes") is not None
        index_non_key_bytes = historical_metrics.get("index_non_key_bytes")
        phase6_cycles = phase6_cycle_rows(result_dir, workload, run, driver_values)
        phase_samples = phase_sample_classifications(time_path)
        row: dict[str, Any] = {
            "workload": workload,
            "run": run,
            "implementation": implementation,
            "profile": workload_profile(workload),
            "exit_code": as_int(time_values, "exit_code"),
            "workload_valid": workload_valid,
            "workload_validity_reason": "ok" if workload_valid else "nonzero_exit",
            "system_sampling_valid": sampling_validity["valid"],
            "system_sampling_validity_reason": sampling_validity["reason"],
            "system_sampling_actual_samples": sampling_validity["actual_samples"],
            "system_sampling_expected_samples": sampling_validity["expected_samples"],
            "system_sampling_cadence_ratio": sampling_validity["cadence_ratio"],
            "system_sampling_longest_gap_seconds": sampling_validity["longest_gap_seconds"],
            "system_memory_valid": system_memory_valid,
            "system_memory_validity_reason": system_memory_reason,
            "raw_latency_valid": raw_latency_valid,
            "raw_latency_validity_reason": raw_latency_reason,
            "comparison_contract_valid": comparison_valid,
            "comparison_contract_validity_reason": comparison_reason,
            "overall_valid": overall_valid,
            "overall_validity_reason": "ok" if overall_valid else ",".join(
                reason
                for valid, reason in (
                    (workload_valid, "workload_invalid"),
                    (int(sampling_validity["valid"]), "system_sampling_invalid"),
                    (system_memory_valid, "system_memory_invalid"),
                    (int(raw_latency_valid != 0), "raw_latency_invalid"),
                    (comparison_valid, comparison_reason),
                )
                if valid != 1
            ),
            # These are signatures, not a capacity judgement. A .time file
            # spans a benchmark workload, so phase-specific resource capture
            # is unavailable in historical artifacts; phase classifications
            # below retain that scope explicitly instead of borrowing another
            # workload's conclusion.
            "resource_signature": classify_resource_signature([time_values]),
            "attribution": classify_attribution([time_values]),
            "phase_sample_classifications": phase_samples,
            "stats_file": stats_file,
            "phase6_cycles": phase6_cycles,
            "phase6_warnings": phase6_interpretation_warnings(workload, run, phase6_cycles),
            "tracked_memory_bytes": tracked,
            "tracked_bytes_per_loaded_object": ratio(tracked, loaded_objects),
            "tracked_bytes_per_live_object": ratio(tracked, live_objects),
            "tracked_bytes_per_edge": ratio(tracked, mem_edges),
            "loaded_objects": loaded_objects,
            "live_objects": live_objects,
            "live_edges": mem_edges,
            "lru_nuked": stat_suffix(stats, "n_lru_nuked"),
            "server_threads": stats.get("MAIN.threads"),
            "server_thread_queue_len": stats.get("MAIN.thread_queue_len"),
            "server_threads_limited": stats.get("MAIN.threads_limited"),
            "server_threads_created": stats.get("MAIN.threads_created"),
            "server_threads_failed": stats.get("MAIN.threads_failed"),
            "buddy_c_req": buddy_counter(stats, "c_req"),
            "buddy_c_fail": buddy_counter(stats, "c_fail"),
            "buddy_c_bytes": buddy_counter(stats, "c_bytes"),
            "buddy_c_freed": buddy_counter(stats, "c_freed"),
            "buddy_c_lru_wakeups": buddy_counter(stats, "c_lru_wakeups"),
            "buddy_c_lru_reserve_used_bytes": buddy_counter(
                stats, "c_lru_reserve_used_bytes"
            ),
            "buddy_c_lru_nuke_fill_reserve": buddy_counter(
                stats, "c_lru_nuke_fill_reserve"
            ),
            "buddy_c_lru_nuke_reserve_drained": buddy_counter(
                stats, "c_lru_nuke_reserve_drained"
            ),
            "buddy_g_alloc": buddy_counter(stats, "g_alloc"),
            "buddy_g_bytes": buddy_counter(stats, "g_bytes"),
            "buddy_g_space": buddy_counter(stats, "g_space"),
            "cachetag_index_memory_bytes": index_memory,
            **{
                f"cachetag_{counter}": cachetag_counter(stats, counter)
                for counter in SET_INTERNING_COUNTERS
            },
            "cachetag_keys_total": historical_metrics.get("keys_total"),
            "cachetag_mem_keys": historical_metrics.get("mem_keys"),
            "cachetag_mem_index_base_bytes": mem_index_base_bytes,
            "cachetag_volatile_side_table_bytes": cachetag_counter(stats, "volatile_side_table_bytes"),
            "cachetag_volatile_side_table_buckets": cachetag_counter(stats, "volatile_side_table_buckets"),
            "cachetag_side_table_grows": cachetag_counter(stats, "side_table_grows"),
            "cachetag_side_table_grow_usec": cachetag_counter(stats, "side_table_grow_usec"),
            "cachetag_side_table_grow_max_usec": cachetag_counter(stats, "side_table_grow_max_usec"),
            "cachetag_side_table_grow_rehashed_slots": cachetag_counter(
                stats, "side_table_grow_rehashed_slots"
            ),
            "cachetag_mem_shard_table_bytes": mem_shard_table_bytes,
            "cachetag_mem_epoch_slot_bytes": mem_epoch_slot_bytes,
            "cachetag_mem_epoch_slot_used_slots": historical_metrics.get("mem_epoch_slot_used_slots"),
            "cachetag_mem_epoch_slot_capacity_slots": historical_metrics.get("mem_epoch_slot_capacity_slots"),
            "cachetag_mem_epoch_slot_slack_slots": historical_metrics.get("mem_epoch_slot_slack_slots"),
            "cachetag_component_memory_bytes": component_bytes if component_seen else None,
            "cachetag_component_gap_bytes": subtract(tracked, component_bytes) if component_seen else None,
            "cachetag_index_non_key_bytes": index_non_key_bytes,
            "cachetag_mem_key_bytes": mem_key_bytes,
            "cachetag_mem_key_id_table_bytes": mem_key_id_table_bytes or 0,
            "cachetag_mem_key_id_table_used_slots": historical_metrics.get("mem_key_id_table_used_slots"),
            "cachetag_mem_key_id_table_capacity_slots": historical_metrics.get("mem_key_id_table_capacity_slots"),
            "cachetag_mem_key_id_table_slack_slots": historical_metrics.get("mem_key_id_table_slack_slots"),
            "cachetag_mem_key_metadata_total_bytes": mem_key_metadata_total_bytes,
            "cachetag_mem_object_table_bytes": mem_object_table_bytes,
            "cachetag_mem_object_table_capacity": historical_metrics.get("mem_object_table_capacity"),
            "cachetag_mem_object_table_high_water_slots": historical_metrics.get("mem_object_table_high_water_slots"),
            "cachetag_mem_object_table_slack_slots": historical_metrics.get("mem_object_table_slack_slots"),
            "cachetag_mem_posting_bytes": mem_posting_bytes,
            "cachetag_mem_posting_segment_used_slots": historical_metrics.get("mem_posting_segment_used_slots"),
            "cachetag_mem_posting_segment_capacity_slots": historical_metrics.get("mem_posting_segment_capacity_slots"),
            "cachetag_mem_posting_segment_slack_slots": historical_metrics.get("mem_posting_segment_slack_slots"),
            "cachetag_mem_reverse_bytes": mem_reverse_bytes,
            "cachetag_mem_compact_full_calls": historical_metrics.get("mem_compact_full_calls"),
            "cachetag_mem_compact_incremental_calls": historical_metrics.get("mem_compact_incremental_calls"),
            "cachetag_mem_compaction_handles_scanned": historical_metrics.get("mem_compaction_handles_scanned"),
            "cachetag_mem_compaction_handles_validated": historical_metrics.get("mem_compaction_handles_validated"),
            "cachetag_mem_compaction_handles_kept": historical_metrics.get("mem_compaction_handles_kept"),
            "cachetag_mem_key_gc_pin_release": historical_metrics.get("mem_key_gc_pin_release"),
            "cachetag_mem_key_gc_incremental": historical_metrics.get("mem_key_gc_incremental"),
            "cachetag_mem_key_gc_full_compact": historical_metrics.get("mem_key_gc_full_compact"),
            "cachetag_mem_validation_obj_lock_acquisitions": historical_metrics.get("mem_validation_obj_lock_acquisitions"),
            "cachetag_stale_fast_epoch_slot_hits": historical_metrics.get("stale_fast_epoch_slot_hits"),
            "cachetag_stale_epoch_slot_fallbacks": historical_metrics.get("stale_epoch_slot_fallbacks"),
            "cachetag_purgemap_entries": cachetag_counter(stats, "purgemap_entries"),
            "cachetag_purgemap_table_slots": cachetag_counter(stats, "purgemap_table_slots"),
            "cachetag_purgemap_tombstones": cachetag_counter(stats, "purgemap_tombstones"),
            "cachetag_purgemap_empty_slots": cachetag_counter(stats, "purgemap_empty_slots"),
            "cachetag_purgemap_bytes": cachetag_counter(stats, "purgemap_bytes"),
            "cachetag_purgemap_hard_floor": cachetag_counter(stats, "purgemap_hard_floor"),
            "cachetag_purgemap_soft_floor": cachetag_counter(stats, "purgemap_soft_floor"),
            "cachetag_purgemap_seq": cachetag_counter(stats, "purgemap_seq"),
            "cachetag_purgemap_prunes": cachetag_counter(stats, "purgemap_prunes"),
            "cachetag_purgemap_pruned_entries": cachetag_counter(stats, "purgemap_pruned_entries"),
            "cachetag_purgemap_rebuilds_grow": cachetag_counter(stats, "purgemap_rebuilds_grow"),
            "cachetag_purgemap_rebuilds_same_size": cachetag_counter(
                stats, "purgemap_rebuilds_same_size"
            ),
            "cachetag_purgemap_probe_hard_hits": cachetag_counter(stats, "purgemap_probe_hard_hits"),
            "cachetag_purgemap_probe_soft_hits": cachetag_counter(stats, "purgemap_probe_soft_hits"),
            "cachetag_purgemap_insert_probe_hits": cachetag_counter(stats, "purgemap_insert_probe_hits"),
            "cachetag_sweep_passes": cachetag_counter(stats, "sweep_passes"),
            "cachetag_sweep_scanned": cachetag_counter(stats, "sweep_scanned"),
            "cachetag_sweep_killed": cachetag_counter(stats, "sweep_killed"),
            "cachetag_sweep_reduced": cachetag_counter(stats, "sweep_reduced"),
            "cachetag_sweep_wakeups": cachetag_counter(stats, "sweep_wakeups"),
            "cachetag_sweep_iterations": cachetag_counter(stats, "sweep_iterations"),
            "cachetag_sweep_last_batches": cachetag_counter(stats, "sweep_last_batches"),
            "cachetag_sweep_batch_scanned_max": cachetag_counter(
                stats, "sweep_batch_scanned_max"
            ),
            "cachetag_sweep_batch_hold_over_2ms": cachetag_counter(stats, "sweep_batch_hold_over_2ms"),
            "cachetag_sweep_batch_hold_over_5ms": cachetag_counter(stats, "sweep_batch_hold_over_5ms"),
            "cachetag_sweep_batch_hold_over_10ms": cachetag_counter(stats, "sweep_batch_hold_over_10ms"),
            "cachetag_sweep_remaining": cachetag_counter(stats, "sweep_remaining"),
            "cachetag_sweep_obj_mtx_wait_last_usec": cachetag_counter(
                stats, "sweep_obj_mtx_wait_last_usec"
            ),
            "cachetag_sweep_obj_mtx_hold_last_usec": cachetag_counter(
                stats, "sweep_obj_mtx_hold_last_usec"
            ),
            "cachetag_sweep_unlocked_gap_last_usec": cachetag_counter(
                stats, "sweep_unlocked_gap_last_usec"
            ),
            "cachetag_sweep_per_object_max_usec": cachetag_counter(
                stats, "sweep_per_object_max_usec"
            ),
            "cachetag_sweep_total_last_usec": cachetag_counter(stats, "sweep_total_last_usec"),
            "cachetag_sweep_last_scanned": cachetag_counter(stats, "sweep_last_scanned"),
            "cachetag_sweep_last_killed": cachetag_counter(stats, "sweep_last_killed"),
            "cachetag_sweep_last_reduced": cachetag_counter(stats, "sweep_last_reduced"),
            "cachetag_purgemap_auto_reclaim_defer_last_usec": cachetag_counter(
                stats, "purgemap_auto_reclaim_defer_last_usec"
            ),
            "cachetag_purgemap_auto_reclaim_filter_last_usec": cachetag_counter(
                stats, "purgemap_auto_reclaim_filter_last_usec"
            ),
            "cachetag_purgemap_auto_reclaim_transient_bytes": cachetag_counter(
                stats, "purgemap_auto_reclaim_transient_bytes"
            ),
            "cachetag_publication_readers_phase0": cachetag_counter(
                stats, "publication_readers_phase0"
            ),
            "cachetag_publication_readers_phase1": cachetag_counter(
                stats, "publication_readers_phase1"
            ),
            "cachetag_publication_acquires": cachetag_counter(
                stats, "publication_acquires"
            ),
            "cachetag_publication_releases": cachetag_counter(
                stats, "publication_releases"
            ),
            "driver_phase5_shutdown": as_int(
                driver_values, "driver_phase5_shutdown"
            ),
            "driver_phase5_shutdown_cold_wall_seconds": (
                as_float(shutdown_values, "phase5_shutdown_cold_wall_ns") / 1e9
                if as_float(shutdown_values, "phase5_shutdown_cold_wall_ns") is not None
                else None
            ),
            **{
                counter: cachetag_counter(stats, counter)
                for counter in PURGEMAP_FELLOW_DIRECT_COUNTERS
            },
            "fellow_disk_obj_get": fellow_counter(stats, "c_dsk_obj_get"),
            "fellow_disk_obj_get_present": fellow_counter(stats, "c_dsk_obj_get_present"),
            "fellow_disk_obj_get_coalesce": fellow_counter(stats, "c_dsk_obj_get_coalesce"),
            "fellow_disk_obj_get_fail": fellow_counter(stats, "c_dsk_obj_get_fail"),
            "vinyld_rss_max_kb": as_int(time_values, "system_tracked_vinyld_rss_max_kb"),
            "cgroup_peak_bytes": as_int(time_values, "system_cgroup_memory_peak_max_bytes"),
            "cache_main_post_load_pss_kb": cache_main_capture_pss_kb(
                result_dir, workload, run, "post_load"
            ),
            "cache_main_post_load_confirmation_pss_kb": cache_main_capture_pss_kb(
                result_dir, workload, run, "post_load_confirmation"
            ),
            "wall_seconds": as_float(time_values, "wall_seconds"),
            "vtc_cpu_seconds": vtc_cpu_seconds,
            "vtc_cpu_seconds_per_backend_object": ratio(
                vtc_cpu_seconds,
                driver_cycle_backend_total if driver_cycle_backend_total is not None else loaded_objects,
            ),
            "driver_load_requests_per_second": as_float(driver_values, "driver_load_requests_per_second"),
            "driver_load_fixed_work_seconds": as_float(
                driver_values, "driver_load_fixed_work_seconds"
            ),
            "driver_load_pending_drain_seconds": as_float(
                driver_values, "driver_load_pending_drain_seconds"
            ),
            "cache_main_load_cpu_seconds": load_cache_main_cpu_seconds,
            "cache_main_load_cpu_seconds_per_object": ratio(
                load_cache_main_cpu_seconds, load_backend_objects
            ),
            "driver_warm_requests_per_second": as_float(driver_values, "driver_warm_requests_per_second"),
            "driver_warm_latency_p99_seconds": as_float(
                driver_values, "driver_warm_latency_p99_seconds"
            ),
            "driver_warm_latency_max_seconds": as_float(
                driver_values, "driver_warm_latency_max_seconds"
            ),
            "cache_main_warm_cpu_seconds": warm_cache_main_cpu_seconds,
            "cache_main_warm_cpu_seconds_per_hit": ratio(
                warm_cache_main_cpu_seconds, warm_hits
            ),
            "vinyld_warm_instructions": warm_instructions,
            "vinyld_warm_cycles": warm_cycles,
            "vinyld_warm_task_clock_seconds": warm_task_clock_seconds,
            # Same denominator as cache_main_warm_cpu_seconds_per_hit, so the
            # two warm-cost views divide by the same work volume (BR-019).
            "vinyld_warm_instructions_per_hit": ratio(warm_instructions, warm_hits),
            "vinyld_warm_cycles_per_hit": ratio(warm_cycles, warm_hits),
            "vinyld_warm_task_clock_seconds_per_hit": ratio(
                warm_task_clock_seconds, warm_hits
            ),
            "vinyld_warm_ipc": ratio(warm_instructions, warm_cycles),
            "vinyld_warm_instructions_running_percent": perf_stat_running_percent(
                warm_instructions_event
            ),
            "vinyld_warm_cycles_running_percent": perf_stat_running_percent(
                warm_cycles_event
            ),
            "vinyld_warm_task_clock_running_percent": perf_stat_running_percent(
                warm_task_clock_event
            ),
            "vinyld_warm_perf_stat_running_percent_min": (
                min(warm_running_percents) if warm_running_percents else None
            ),
            "driver_concurrent_reads": concurrent_reads,
            "driver_concurrent_inserts": concurrent_inserts,
            "driver_concurrent_purges": concurrent_purges,
            "driver_concurrent_seconds": concurrent_seconds,
            "driver_concurrent_target_rps": as_int(
                driver_values, "driver_concurrent_target_rps"
            ),
            "driver_churn_cycles": as_int(driver_values, "driver_churn_cycles"),
            "driver_tag_length_class": driver_values.get("driver_tag_length_class"),
            "driver_tag_shape_sample_objects": as_int(
                driver_values, "driver_tag_shape_sample_objects"
            ),
            "driver_tag_shape_min_tags_per_object": as_int(
                driver_values, "driver_tag_shape_min_tags_per_object"
            ),
            "driver_tag_shape_max_tags_per_object": as_int(
                driver_values, "driver_tag_shape_max_tags_per_object"
            ),
            "driver_tag_shape_min_tag_length": as_int(
                driver_values, "driver_tag_shape_min_tag_length"
            ),
            "driver_tag_shape_max_tag_length": as_int(
                driver_values, "driver_tag_shape_max_tag_length"
            ),
            "driver_tag_shape_sample_unique_tags": as_int(
                driver_values, "driver_tag_shape_sample_unique_tags"
            ),
            "driver_tag_shape_validation_configured": as_int(
                driver_values, "driver_tag_shape_validation_configured"
            ),
            "driver_tag_shape_validation_ok": as_int(
                driver_values, "driver_tag_shape_validation_ok"
            ),
            "driver_tag_shape_expected_tags_per_object": as_int(
                driver_values, "driver_tag_shape_expected_tags_per_object"
            ),
            "driver_tag_shape_length_class_checked": as_int(
                driver_values, "driver_tag_shape_length_class_checked"
            ),
            "driver_tag_shape_expected_min_tag_length": as_int(
                driver_values, "driver_tag_shape_expected_min_tag_length"
            ),
            "driver_tag_shape_expected_max_tag_length": as_int(
                driver_values, "driver_tag_shape_expected_max_tag_length"
            ),
            "driver_tag_shape_unique_count_checked": as_int(
                driver_values, "driver_tag_shape_unique_count_checked"
            ),
            "driver_tag_shape_expected_sample_unique_tags": as_int(
                driver_values, "driver_tag_shape_expected_sample_unique_tags"
            ),
            "driver_churn_expected_keys_total": as_int(
                driver_values, "driver_churn_expected_keys_total"
            ),
            "driver_churn_expected_live_generation_keys": as_int(
                driver_values, "driver_churn_expected_live_generation_keys"
            ),
            "driver_cycle_backend_objects_total": driver_cycle_backend_total,
            "driver_cycle_tagged_objects_total": driver_cycle_sum(
                driver_values, "_tagged_objects"
            ),
            "driver_cycle_load_successes_total": driver_cycle_sum(
                driver_values, "_load_successes"
            ),
            "driver_concurrent_read_requests_per_second": (
                float(concurrent_reads) / concurrent_seconds
                if concurrent_reads is not None and concurrent_seconds and concurrent_seconds > 0
                else None
            ),
            "driver_concurrent_insert_requests_per_second": (
                float(concurrent_inserts) / concurrent_seconds
                if concurrent_inserts is not None and concurrent_seconds and concurrent_seconds > 0
                else None
            ),
            "driver_concurrent_purge_requests_per_second": (
                float(concurrent_purges) / concurrent_seconds
                if concurrent_purges is not None and concurrent_seconds and concurrent_seconds > 0
                else None
            ),
            "driver_concurrent_read_rps_1s_min": as_float(
                driver_values, "driver_concurrent_read_rps_1s_min"
            ),
            "driver_concurrent_read_rps_1s_max": as_float(
                driver_values, "driver_concurrent_read_rps_1s_max"
            ),
            "driver_concurrent_insert_rps_1s_min": as_float(
                driver_values, "driver_concurrent_insert_rps_1s_min"
            ),
            "driver_concurrent_insert_rps_1s_max": as_float(
                driver_values, "driver_concurrent_insert_rps_1s_max"
            ),
            "driver_concurrent_purge_rps_1s_min": as_float(
                driver_values, "driver_concurrent_purge_rps_1s_min"
            ),
            "driver_concurrent_purge_rps_1s_max": as_float(
                driver_values, "driver_concurrent_purge_rps_1s_max"
            ),
            "driver_concurrent_read_latency_1s_max_seconds": as_float(
                driver_values, "driver_concurrent_read_latency_1s_max_seconds"
            ),
            "driver_concurrent_insert_latency_1s_max_seconds": as_float(
                driver_values, "driver_concurrent_insert_latency_1s_max_seconds"
            ),
            "driver_concurrent_purge_latency_1s_max_seconds": as_float(
                driver_values, "driver_concurrent_purge_latency_1s_max_seconds"
            ),
            "driver_read_latency_p95_seconds": as_float(driver_values, "driver_read_latency_p95_seconds"),
            "driver_read_latency_p999_seconds": as_float(driver_values, "driver_read_latency_p999_seconds"),
            "driver_insert_latency_p95_seconds": as_float(driver_values, "driver_insert_latency_p95_seconds"),
            "driver_insert_latency_p999_seconds": as_float(driver_values, "driver_insert_latency_p999_seconds"),
            "driver_purge_storm_read_requests_per_second": as_float(
                driver_values, "driver_purge_storm_read_requests_per_second"
            ),
            "driver_purge_storm_purge_requests_per_second": as_float(
                driver_values, "driver_purge_storm_purge_requests_per_second"
            ),
            "driver_purge_storm_read_latency_p95_seconds": as_float(
                driver_values, "driver_purge_storm_read_latency_p95_seconds"
            ),
            "driver_purge_storm_purge_latency_p50_seconds": as_float(
                driver_values, "driver_purge_storm_purge_latency_p50_seconds"
            ),
            "driver_purge_storm_purge_latency_p99_seconds": as_float(
                driver_values, "driver_purge_storm_purge_latency_p99_seconds"
            ),
            "driver_purge_storm_purge_latency_p999_seconds": as_float(
                driver_values, "driver_purge_storm_purge_latency_p999_seconds"
            ),
            "driver_purge_storm_unknown_purges": as_int(
                driver_values, "driver_purge_storm_unknown_purges"
            ),
            "driver_purge_storm_soft_purges": as_int(
                driver_values, "driver_purge_storm_soft_purges"
            ),
            "driver_populated_map_entries_inserted": as_int(
                driver_values, "driver_populated_map_entries_inserted"
            ),
            "driver_populated_map_purges_per_second": as_float(
                driver_values, "driver_populated_map_purges_per_second"
            ),
            "driver_cold_residency_purge_actual": as_int(
                driver_values, "driver_cold_residency_purge_actual"
            ),
            "driver_cold_residency_objects_last": as_int(
                driver_values, "driver_cold_residency_objects_last"
            ),
            "driver_cold_residency_objects_min": as_int(
                driver_values, "driver_cold_residency_objects_min"
            ),
            "driver_cold_residency_objects_max": as_int(
                driver_values, "driver_cold_residency_objects_max"
            ),
            "driver_cold_residency_sweep_reads": as_int(
                driver_values, "driver_cold_residency_sweep_reads"
            ),
            "driver_cold_residency_sweep_read_requests_per_second": as_float(
                driver_values, "driver_cold_residency_sweep_read_requests_per_second"
            ),
            "driver_cold_residency_sweep_read_latency_p95_seconds": as_float(
                driver_values, "driver_cold_residency_sweep_read_latency_p95_seconds"
            ),
            "driver_cold_residency_sweep_read_latency_p99_seconds": as_float(
                driver_values, "driver_cold_residency_sweep_read_latency_p99_seconds"
            ),
            "driver_cold_residency_sweep_read_latency_p999_seconds": as_float(
                driver_values, "driver_cold_residency_sweep_read_latency_p999_seconds"
            ),
            "driver_cold_residency_sweep_errors": as_int(
                driver_values, "driver_cold_residency_sweep_errors"
            ),
            "driver_cold_residency_window_fresh_latency_p99_seconds": as_float(
                driver_values, "driver_cold_residency_window_fresh_latency_p99_seconds"
            ),
            "driver_cold_residency_window_fresh_latency_p999_seconds": as_float(
                driver_values, "driver_cold_residency_window_fresh_latency_p999_seconds"
            ),
            "driver_phase4_pre_requests_per_second": as_float(
                driver_values, "driver_phase4_pre_requests_per_second"
            ),
            "driver_phase4_pre_latency_p99_seconds": as_float(
                driver_values, "driver_phase4_pre_latency_p99_seconds"
            ),
            "driver_phase4_pre_latency_p999_seconds": as_float(
                driver_values, "driver_phase4_pre_latency_p999_seconds"
            ),
            "driver_phase4_sweep_requests_per_second": as_float(
                driver_values, "driver_phase4_sweep_requests_per_second"
            ),
            "driver_phase4_sweep_latency_p99_seconds": as_float(
                driver_values, "driver_phase4_sweep_latency_p99_seconds"
            ),
            "driver_phase4_sweep_latency_p999_seconds": as_float(
                driver_values, "driver_phase4_sweep_latency_p999_seconds"
            ),
            "driver_phase4_sweep_purge_wall_seconds": as_float(
                driver_values, "driver_phase4_sweep_purge_wall_seconds"
            ),
            "driver_phase4_sweep_compact_returned": as_int(
                driver_values, "driver_phase4_sweep_compact_returned"
            ),
            "driver_phase4_sweep_compact_wall_seconds": as_float(
                driver_values, "driver_phase4_sweep_compact_wall_seconds"
            ),
            "driver_phase4_sweep_stale_hits": as_int(
                driver_values, "driver_phase4_sweep_stale_hits"
            ),
            "driver_phase4_sweep_stale_older_responses": as_int(
                driver_values, "driver_phase4_sweep_stale_older_responses"
            ),
            "driver_phase4_sweep_stale_newer_responses": as_int(
                driver_values, "driver_phase4_sweep_stale_newer_responses"
            ),
            "driver_phase4_sweep_current_epoch_stale_responses": as_int(
                driver_values, "driver_phase4_sweep_current_epoch_stale_responses"
            ),
            "driver_phase4_sweep_epoch_evidence_requested_1_returned_1_cache_hit": as_int(
                driver_values,
                "driver_phase4_sweep_epoch_evidence_requested_1_returned_1_cache_hit",
            ),
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_1_cache_hit": as_int(
                driver_values,
                "driver_phase4_sweep_epoch_evidence_requested_2_returned_1_cache_hit",
            ),
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_miss": as_int(
                driver_values,
                "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_miss",
            ),
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_hit": as_int(
                driver_values,
                "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_hit",
            ),
            "driver_phase4_post_requests_per_second": as_float(
                driver_values, "driver_phase4_post_requests_per_second"
            ),
            "driver_phase4_post_latency_p99_seconds": as_float(
                driver_values, "driver_phase4_post_latency_p99_seconds"
            ),
            "driver_phase4_post_latency_p999_seconds": as_float(
                driver_values, "driver_phase4_post_latency_p999_seconds"
            ),
            "driver_phase4_post_stale_hits": as_int(
                driver_values, "driver_phase4_post_stale_hits"
            ),
            "driver_phase4_post_stale_responses": as_int(
                driver_values, "driver_phase4_post_stale_responses"
            ),
            "driver_phase4_post_stale_older_responses": as_int(
                driver_values, "driver_phase4_post_stale_older_responses"
            ),
            "driver_phase4_post_stale_newer_responses": as_int(
                driver_values, "driver_phase4_post_stale_newer_responses"
            ),
            "driver_phase4_post_current_epoch_stale_responses": as_int(
                driver_values, "driver_phase4_post_current_epoch_stale_responses"
            ),
            "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_miss": as_int(
                driver_values,
                "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_miss",
            ),
            "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_hit": as_int(
                driver_values,
                "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_hit",
            ),
            "driver_phase4_window_fresh_latency_p999_seconds": as_float(
                driver_values, "driver_phase4_window_fresh_latency_p999_seconds"
            ),
            "driver_phase5_hold_ms_configured": as_int(
                driver_values, "driver_phase5_hold_ms_configured"
            ),
            "driver_phase5_cap_purges_configured": as_int(
                driver_values, "driver_phase5_cap_purges_configured"
            ),
            "driver_phase5_hold_publication": as_int(
                driver_values, "driver_phase5_hold_publication"
            ),
            "driver_phase5_hold_active_wait_seconds": as_float(
                driver_values, "driver_phase5_hold_active_wait_seconds"
            ),
            "driver_phase5_initial_purge_wall_seconds": as_float(
                driver_values, "driver_phase5_initial_purge_wall_seconds"
            ),
            "driver_phase5_held_compact_wall_seconds": as_float(
                driver_values, "driver_phase5_held_compact_wall_seconds"
            ),
            "driver_phase5_held_load_wall_seconds": as_float(
                driver_values, "driver_phase5_held_load_wall_seconds"
            ),
            "driver_phase5_hold_fetch_wall_seconds": as_float(
                driver_values, "driver_phase5_hold_fetch_wall_seconds"
            ),
            "driver_phase5_release_compact_wall_seconds": as_float(
                driver_values, "driver_phase5_release_compact_wall_seconds"
            ),
            "driver_phase5_held_reads": as_int(
                driver_values, "driver_phase5_held_reads"
            ),
            "driver_phase5_held_purges": as_int(
                driver_values, "driver_phase5_held_purges"
            ),
            "driver_phase5_held_purges_published": as_int(
                driver_values, "driver_phase5_held_purges_published"
            ),
            "driver_phase5_held_errors": as_int(
                driver_values, "driver_phase5_held_errors"
            ),
            "driver_phase5_held_read_rps_1s_min": as_float(
                driver_values, "driver_phase5_held_read_rps_1s_min"
            ),
            "driver_phase5_held_read_rps_1s_max": as_float(
                driver_values, "driver_phase5_held_read_rps_1s_max"
            ),
            "driver_phase5_held_purge_rps_1s_min": as_float(
                driver_values, "driver_phase5_held_purge_rps_1s_min"
            ),
            "driver_phase5_held_purge_rps_1s_max": as_float(
                driver_values, "driver_phase5_held_purge_rps_1s_max"
            ),
            "driver_phase5_held_read_latency_p99_seconds": as_float(
                driver_values, "driver_phase5_held_read_latency_p99_seconds"
            ),
            "driver_phase5_held_read_latency_p999_seconds": as_float(
                driver_values, "driver_phase5_held_read_latency_p999_seconds"
            ),
            "driver_phase5_held_purge_latency_p99_seconds": as_float(
                driver_values, "driver_phase5_held_purge_latency_p99_seconds"
            ),
            "driver_phase5_held_purge_latency_p999_seconds": as_float(
                driver_values, "driver_phase5_held_purge_latency_p999_seconds"
            ),
            "driver_phase5_window_fresh_latency_p999_seconds": as_float(
                driver_values, "driver_phase5_window_fresh_latency_p999_seconds"
            ),
            "driver_errors": as_int(driver_values, "driver_errors"),
        }
        # Historical artifact compatibility: keep retired component counters
        # available only for archived cachetag_epoch/cachetag_purgemap rows.
        # Fresh cachetag reports must not publish those removed metrics, even
        # when an old stats file happens to be mixed into the result directory.
        if implementation == "cachetag":
            retired_prefixes = (
                "cachetag_mem_",
                "cachetag_index_non_key",
                "cachetag_component_",
                "cachetag_stale_",
                "cachetag_keys_total",
            )
            for key in tuple(row):
                if key.startswith(retired_prefixes):
                    del row[key]
        for counter in PHASE4_VSC_COUNTERS:
            row[f"cachetag_{counter}"] = cachetag_counter(stats, counter)
        for phase, values in phase_stats.items():
            phase_tracked = tracked_memory_bytes(values, implementation)
            row[f"{phase}_tracked_memory_bytes"] = phase_tracked
            row[f"{phase}_live_objects"] = stat_suffix(values, "n_object")
            row[f"{phase}_cachetag_mem_objects"] = cachetag_counter(values, "volatile_objects")
            row[f"{phase}_live_edges"] = cachetag_counter(values, "volatile_edges")
            row[f"{phase}_cachetag_volatile_side_table_bytes"] = cachetag_counter(
                values, "volatile_side_table_bytes"
            )
            for counter in PURGEMAP_FELLOW_DIRECT_COUNTERS:
                row[f"{phase}_{counter}"] = cachetag_counter(values, counter)
            row[f"{phase}_fellow_disk_obj_get"] = fellow_counter(values, "c_dsk_obj_get")
            row[f"{phase}_fellow_disk_obj_get_present"] = fellow_counter(
                values, "c_dsk_obj_get_present"
            )
            row[f"{phase}_fellow_disk_obj_get_coalesce"] = fellow_counter(
                values, "c_dsk_obj_get_coalesce"
            )
            row[f"{phase}_fellow_disk_obj_get_fail"] = fellow_counter(
                values, "c_dsk_obj_get_fail"
            )
            for counter in PHASE4_VSC_COUNTERS:
                row[f"{phase}_cachetag_{counter}"] = cachetag_counter(values, counter)
            for counter in PHASE5_VSC_COUNTERS:
                row[f"{phase}_cachetag_{counter}"] = cachetag_counter(values, counter)
        for phase, before, after in zip(
            PHASE4_VSC_DELTA_PHASES,
            ("phase4_start", "phase4_pre", "phase4_compact", "phase4_refill"),
            PHASE4_VSC_DELTA_PHASES,
        ):
            before_values = phase_stats.get(before, {})
            after_values = phase_stats.get(after, {})
            for counter in PHASE4_VSC_CUMULATIVE_COUNTERS:
                row[f"{phase}_delta_cachetag_{counter}"] = subtract_optional(
                    cachetag_counter(after_values, counter),
                    cachetag_counter(before_values, counter),
                )
        row.update(phase4_attribution(result_dir, workload, run, driver_values))
        rows.append(row)
    return rows


def aggregate_workload_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["workload"]), []).append(row)
    summaries: list[dict[str, Any]] = []
    for workload, items in sorted(grouped.items()):
        numeric_fields = (
            "tracked_memory_bytes",
            "tracked_bytes_per_loaded_object",
            "tracked_bytes_per_live_object",
            "tracked_bytes_per_edge",
            "loaded_objects",
            "live_objects",
            "live_edges",
            "lru_nuked",
            "buddy_c_req",
            "buddy_c_fail",
            "buddy_c_bytes",
            "buddy_c_freed",
            "buddy_c_lru_wakeups",
            "buddy_c_lru_reserve_used_bytes",
            "buddy_c_lru_nuke_fill_reserve",
            "buddy_c_lru_nuke_reserve_drained",
            "buddy_g_alloc",
            "buddy_g_bytes",
            "buddy_g_space",
            "cachetag_index_memory_bytes",
            *(f"cachetag_{counter}" for counter in SET_INTERNING_COUNTERS),
            "cachetag_volatile_side_table_bytes",
            "cachetag_volatile_side_table_buckets",
            "cachetag_side_table_grows",
            "cachetag_purgemap_entries",
            "cachetag_purgemap_table_slots",
            "cachetag_purgemap_tombstones",
            "cachetag_purgemap_empty_slots",
            "cachetag_purgemap_bytes",
            "cachetag_purgemap_hard_floor",
            "cachetag_purgemap_soft_floor",
            "cachetag_purgemap_seq",
            "cachetag_purgemap_prunes",
            "cachetag_purgemap_pruned_entries",
            "cachetag_purgemap_rebuilds_grow",
            "cachetag_purgemap_rebuilds_same_size",
            "cachetag_purgemap_probe_hard_hits",
            "cachetag_purgemap_probe_soft_hits",
            "cachetag_purgemap_insert_probe_hits",
            "cachetag_sweep_passes",
            "cachetag_sweep_scanned",
            "cachetag_sweep_killed",
            "cachetag_sweep_reduced",
            "cachetag_sweep_wakeups",
            "cachetag_sweep_iterations",
            *PURGEMAP_FELLOW_DIRECT_COUNTERS,
            "fellow_disk_obj_get",
            "fellow_disk_obj_get_present",
            "fellow_disk_obj_get_coalesce",
            "fellow_disk_obj_get_fail",
            "post_load_tracked_memory_bytes",
            "post_restart_tracked_memory_bytes",
            "post_first_touch_tracked_memory_bytes",
            "post_cold_purge_tracked_memory_bytes",
            "post_hot_purge_tracked_memory_bytes",
            *PURGEMAP_FELLOW_DIRECT_PHASE_FIELDS,
            *(
                f"{phase}_{field}"
                for phase in PURGEMAP_RESTART_PHASES
                for field in (
                    "cachetag_mem_objects",
                    "live_edges",
                    "cachetag_volatile_side_table_bytes",
                    "fellow_disk_obj_get",
                    "fellow_disk_obj_get_present",
                    "fellow_disk_obj_get_coalesce",
                    "fellow_disk_obj_get_fail",
                )
            ),
            "vinyld_rss_max_kb",
            "cgroup_peak_bytes",
            "cache_main_post_load_pss_kb",
            "cache_main_post_load_confirmation_pss_kb",
            "vtc_cpu_seconds",
            "vtc_cpu_seconds_per_backend_object",
            "driver_load_requests_per_second",
            "driver_load_fixed_work_seconds",
            "driver_load_pending_drain_seconds",
            "cache_main_load_cpu_seconds",
            "cache_main_load_cpu_seconds_per_object",
            "driver_warm_requests_per_second",
            "driver_warm_latency_p99_seconds",
            "driver_warm_latency_max_seconds",
            "cache_main_warm_cpu_seconds",
            "cache_main_warm_cpu_seconds_per_hit",
            "vinyld_warm_instructions",
            "vinyld_warm_cycles",
            "vinyld_warm_task_clock_seconds",
            "vinyld_warm_instructions_per_hit",
            "vinyld_warm_cycles_per_hit",
            "vinyld_warm_task_clock_seconds_per_hit",
            "vinyld_warm_ipc",
            "vinyld_warm_instructions_running_percent",
            "vinyld_warm_cycles_running_percent",
            "vinyld_warm_task_clock_running_percent",
            "vinyld_warm_perf_stat_running_percent_min",
            "driver_concurrent_reads",
            "driver_concurrent_inserts",
            "driver_concurrent_purges",
            "driver_concurrent_seconds",
            "driver_concurrent_target_rps",
            "driver_tag_shape_sample_objects",
            "driver_tag_shape_min_tags_per_object",
            "driver_tag_shape_max_tags_per_object",
            "driver_tag_shape_min_tag_length",
            "driver_tag_shape_max_tag_length",
            "driver_tag_shape_sample_unique_tags",
            "driver_tag_shape_validation_configured",
            "driver_tag_shape_validation_ok",
            "driver_tag_shape_expected_tags_per_object",
            "driver_tag_shape_length_class_checked",
            "driver_tag_shape_expected_min_tag_length",
            "driver_tag_shape_expected_max_tag_length",
            "driver_tag_shape_unique_count_checked",
            "driver_tag_shape_expected_sample_unique_tags",
            "driver_churn_cycles",
            "driver_churn_expected_keys_total",
            "driver_churn_expected_live_generation_keys",
            "driver_cycle_backend_objects_total",
            "driver_cycle_tagged_objects_total",
            "driver_cycle_load_successes_total",
            "cachetag_sweep_last_batches",
            "cachetag_sweep_batch_scanned_max",
            "cachetag_sweep_batch_hold_over_2ms",
            "cachetag_sweep_batch_hold_over_5ms",
            "cachetag_sweep_batch_hold_over_10ms",
            "cachetag_sweep_remaining",
            "cachetag_sweep_obj_mtx_wait_last_usec",
            "cachetag_sweep_obj_mtx_hold_last_usec",
            "cachetag_sweep_unlocked_gap_last_usec",
            "cachetag_sweep_per_object_max_usec",
            "cachetag_sweep_total_last_usec",
            "cachetag_sweep_last_scanned",
            "cachetag_sweep_last_killed",
            "cachetag_sweep_last_reduced",
            "cachetag_purgemap_auto_reclaim_defer_last_usec",
            "cachetag_purgemap_auto_reclaim_filter_last_usec",
            "cachetag_purgemap_auto_reclaim_transient_bytes",
            "cachetag_publication_readers_phase0",
            "cachetag_publication_readers_phase1",
            "cachetag_publication_acquires",
            "cachetag_publication_releases",
            "driver_phase5_shutdown",
            "driver_phase5_shutdown_cold_wall_seconds",
            "driver_phase5_hold_ms_configured",
            "driver_phase5_cap_purges_configured",
            "driver_phase5_hold_publication",
            "driver_phase5_hold_active_wait_seconds",
            "driver_phase5_initial_purge_wall_seconds",
            "driver_phase5_held_compact_wall_seconds",
            "driver_phase5_held_load_wall_seconds",
            "driver_phase5_hold_fetch_wall_seconds",
            "driver_phase5_release_compact_wall_seconds",
            "driver_phase5_held_reads",
            "driver_phase5_held_purges",
            "driver_phase5_held_purges_published",
            "driver_phase5_held_errors",
            "driver_phase5_held_read_rps_1s_min",
            "driver_phase5_held_read_rps_1s_max",
            "driver_phase5_held_purge_rps_1s_min",
            "driver_phase5_held_purge_rps_1s_max",
            "driver_phase5_held_read_latency_p99_seconds",
            "driver_phase5_held_read_latency_p999_seconds",
            "driver_phase5_held_purge_latency_p99_seconds",
            "driver_phase5_held_purge_latency_p999_seconds",
            "driver_phase5_window_fresh_latency_p999_seconds",
            "driver_concurrent_read_requests_per_second",
            "driver_concurrent_insert_requests_per_second",
            "driver_concurrent_purge_requests_per_second",
            "driver_concurrent_read_rps_1s_min",
            "driver_concurrent_read_rps_1s_max",
            "driver_concurrent_insert_rps_1s_min",
            "driver_concurrent_insert_rps_1s_max",
            "driver_concurrent_purge_rps_1s_min",
            "driver_concurrent_purge_rps_1s_max",
            "driver_concurrent_read_latency_1s_max_seconds",
            "driver_concurrent_insert_latency_1s_max_seconds",
            "driver_concurrent_purge_latency_1s_max_seconds",
            "driver_read_latency_p95_seconds",
            "driver_read_latency_p999_seconds",
            "driver_insert_latency_p95_seconds",
            "driver_insert_latency_p999_seconds",
            "driver_purge_storm_read_requests_per_second",
            "driver_purge_storm_purge_requests_per_second",
            "driver_purge_storm_read_latency_p95_seconds",
            "driver_purge_storm_purge_latency_p50_seconds",
            "driver_purge_storm_purge_latency_p99_seconds",
            "driver_purge_storm_purge_latency_p999_seconds",
            "driver_purge_storm_unknown_purges",
            "driver_purge_storm_soft_purges",
            "driver_populated_map_entries_inserted",
            "driver_populated_map_purges_per_second",
            "driver_cold_residency_purge_actual",
            "driver_cold_residency_objects_last",
            "driver_cold_residency_objects_min",
            "driver_cold_residency_objects_max",
            "driver_cold_residency_sweep_reads",
            "driver_cold_residency_sweep_read_requests_per_second",
            "driver_cold_residency_sweep_read_latency_p95_seconds",
            "driver_cold_residency_sweep_read_latency_p99_seconds",
            "driver_cold_residency_sweep_read_latency_p999_seconds",
            "driver_cold_residency_sweep_errors",
            "driver_cold_residency_window_fresh_latency_p99_seconds",
            "driver_cold_residency_window_fresh_latency_p999_seconds",
            "driver_phase4_pre_requests_per_second",
            "driver_phase4_pre_latency_p99_seconds",
            "driver_phase4_pre_latency_p999_seconds",
            "driver_phase4_sweep_requests_per_second",
            "driver_phase4_sweep_latency_p99_seconds",
            "driver_phase4_sweep_latency_p999_seconds",
            "driver_phase4_sweep_purge_wall_seconds",
            "driver_phase4_sweep_compact_returned",
            "driver_phase4_sweep_compact_wall_seconds",
            "driver_phase4_sweep_stale_hits",
            "driver_phase4_sweep_stale_older_responses",
            "driver_phase4_sweep_stale_newer_responses",
            "driver_phase4_sweep_current_epoch_stale_responses",
            "driver_phase4_sweep_epoch_evidence_requested_1_returned_1_cache_hit",
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_1_cache_hit",
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_miss",
            "driver_phase4_sweep_epoch_evidence_requested_2_returned_2_cache_hit",
            "driver_phase4_post_requests_per_second",
            "driver_phase4_post_latency_p99_seconds",
            "driver_phase4_post_latency_p999_seconds",
            "driver_phase4_post_stale_hits",
            "driver_phase4_post_stale_responses",
            "driver_phase4_post_stale_older_responses",
            "driver_phase4_post_stale_newer_responses",
            "driver_phase4_post_current_epoch_stale_responses",
            "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_miss",
            "driver_phase4_post_epoch_evidence_requested_2_returned_2_cache_hit",
            "driver_phase4_window_fresh_latency_p999_seconds",
            *PHASE4_ATTRIBUTION_FIELDS,
            *(f"cachetag_{counter}" for counter in PHASE4_VSC_COUNTERS),
            *(f"cachetag_{counter}" for counter in PHASE5_VSC_COUNTERS),
            *(f"{phase}_cachetag_{counter}" for phase in PHASE4_VSC_DELTA_PHASES for counter in PHASE4_VSC_COUNTERS),
            *(f"{phase}_cachetag_{counter}" for phase in ("phase5_held_load_start", "phase5_held_load_end", "phase5_shutdown_ready") for counter in PHASE5_VSC_COUNTERS),
            *(f"{phase}_delta_cachetag_{counter}" for phase in PHASE4_VSC_DELTA_PHASES for counter in PHASE4_VSC_CUMULATIVE_COUNTERS),
        )
        valid_items = [row for row in items if row.get("overall_valid") == 1]
        summary: dict[str, Any] = {
            "workload": workload,
            "implementation": items[0]["implementation"],
            "profile": items[0]["profile"],
            "runs": len(items),
            "passes": sum(1 for row in items if row.get("exit_code") == 0),
            "failures": sum(1 for row in items if row.get("exit_code") != 0),
            "valid_runs": len(valid_items),
            "invalid_runs": len(items) - len(valid_items),
        }
        for field in ("driver_tag_length_class",):
            vals = sorted({str(row[field]) for row in valid_items if row.get(field)})
            summary[field] = vals[0] if len(vals) == 1 else ",".join(vals)
        for field in numeric_fields:
            vals = [float(row[field]) for row in valid_items if row.get(field) is not None]
            summary[field + "_observations"] = len(vals)
            summary[field + "_median"] = median(vals)
            summary[field + "_min"] = min(vals) if vals else None
            summary[field + "_max"] = max(vals) if vals else None
        summaries.append(summary)
    return summaries


def result_data(result_dir: Path) -> dict[str, Any]:
    metadata = parse_kv(result_dir / "metadata.env")
    remote = parse_kv(result_dir / "remote-run.env")
    matrix = remote.get("matrix") or metadata.get("bench_matrix") or result_dir.name
    result_id = remote.get("result_id") or metadata.get("bench_result_id") or result_dir.name
    time_files = sorted(result_dir.glob("*.run-*.time"))
    driver_files = sorted(result_dir.glob("*.driver"))
    times = [parse_kv(path) for path in time_files]
    drivers = [parse_kv(path) for path in driver_files]
    rows = workload_rows(result_dir)
    validity_by_run = {
        (str(row["workload"]), int(row["run"])): row.get("overall_valid") == 1
        for row in rows
    }
    judged_times = [
        values
        for path, values in zip(time_files, times)
        if (parsed := workload_from_time(path)) is not None and validity_by_run.get(parsed, False)
    ]

    process_pass_count = sum(1 for values in times if as_int(values, "exit_code") == 0)
    process_fail_count = len(times) - process_pass_count
    walls = sorted(value for values in judged_times if (value := as_float(values, "wall_seconds")) is not None)
    cpu_avg = [value for values in judged_times if (value := as_float(values, "system_cpu_busy_avg_percent")) is not None]
    cpu_max = [value for values in judged_times if (value := as_float(values, "system_cpu_busy_max_percent")) is not None]
    core_max = [value for values in judged_times if (value := as_float(values, "system_cpu_any_core_busy_max_percent")) is not None]
    mem_min = [value for values in judged_times if (value := as_float(values, "system_memavailable_min_percent")) is not None]
    cgroup_peak = [value for values in judged_times if (value := as_int(values, "system_cgroup_memory_peak_max_bytes")) is not None]
    disk_read = sum(as_int(values, "system_disk_read_sectors_delta") or 0 for values in judged_times)
    disk_write = sum(as_int(values, "system_disk_write_sectors_delta") or 0 for values in judged_times)
    disk_write_bps_max = [value for values in judged_times if (value := as_float(values, "system_disk_write_bytes_per_second_max")) is not None]
    disk_read_bps_max = [value for values in judged_times if (value := as_float(values, "system_disk_read_bytes_per_second_max")) is not None]
    disk_util_max = [value for values in judged_times if (value := as_float(values, "system_disk_util_percent_max")) is not None]
    disk_write_await_max = [value for values in judged_times if (value := as_float(values, "system_disk_write_await_ms_max")) is not None]
    disk_flush_await_max = [value for values in judged_times if (value := as_float(values, "system_disk_flush_await_ms_max")) is not None]
    disk_flush_iops_max = [value for values in judged_times if (value := as_float(values, "system_disk_flush_ios_per_second_max")) is not None]
    swap_files = [path.name for path, values in zip(time_files, times) if (as_int(values, "swap_activity") or 0) > 0]
    driver_errors = [
        {"file": path.name, "error": values.get("driver_error", "driver error")}
        for path, values in zip(driver_files, drivers)
        if (as_int(values, "driver_errors") or 0) > 0
    ]
    valid_count = sum(1 for row in rows if row.get("overall_valid") == 1)
    invalid_count = len(rows) - valid_count
    classifications = workload_phase_classifications(rows)
    return {
        "result_id": result_id,
        "matrix": matrix,
        "path": str(result_dir),
        "hardware": hardware_fingerprint(result_dir),
        "comparison_cohort_fingerprint": metadata.get("benchmark_cohort_fingerprint")
        or remote.get("benchmark_cohort_fingerprint"),
        # The measured VCL shape. It is inside the cohort fingerprint, so a
        # one-call row can never merge with a two-call row; this field is what
        # makes the difference readable instead of only enforced.
        "bench_stale_deliver": metadata.get("bench_stale_deliver")
        or command_env_value(remote, "BENCH_STALE_DELIVER")
        or "",
        "bench_perf_stat": metadata.get("bench_perf_stat")
        or command_env_value(remote, "BENCH_PERF_STAT")
        or "",
        "runs": {
            "pass": valid_count,
            "fail": invalid_count,
            "valid": valid_count,
            "invalid": invalid_count,
            "process_pass": process_pass_count,
            "process_fail": process_fail_count,
            "total": len(times),
        },
        "wall_seconds": {
            "min": walls[0] if walls else None,
            "p50": percentile(walls, 0.50) if walls else None,
            "p95": percentile(walls, 0.95) if walls else None,
            "max": walls[-1] if walls else None,
        },
        "cpu": {
            "avg_max_percent": max(cpu_avg) if cpu_avg else None,
            "run_max_percent": max(cpu_max) if cpu_max else None,
            "busiest_core_max_percent": max(core_max) if core_max else None,
        },
        "memory": {
            "memavailable_min_percent": min(mem_min) if mem_min else None,
            "cgroup_peak_bytes": max(cgroup_peak) if cgroup_peak else None,
        },
        "swap": {"activity": bool(swap_files), "files": swap_files},
        "disk": {
            "read_sectors_delta": disk_read,
            "write_sectors_delta": disk_write,
            "read_bytes_per_second_max": max(disk_read_bps_max) if disk_read_bps_max else None,
            "write_bytes_per_second_max": max(disk_write_bps_max) if disk_write_bps_max else None,
            "flush_ios_per_second_max": max(disk_flush_iops_max) if disk_flush_iops_max else None,
            "util_percent_max": max(disk_util_max) if disk_util_max else None,
            "write_await_ms_max": max(disk_write_await_max) if disk_write_await_max else None,
            "flush_await_ms_max": max(disk_flush_await_max) if disk_flush_await_max else None,
        },
        # A result-level label would conflate unrelated workloads and phases.
        # The only matrix roll-up is deliberately non-diagnostic.
        "classification_rollup": "mixed",
        "driver_errors": driver_errors,
        "workloads": rows,
        "workload_summaries": aggregate_workload_rows(rows),
        "workload_phase_classifications": classifications,
        "sweep_configuration": sweep_configuration(result_dir),
    }


def render_cross_result_audit(results: list[dict[str, Any]]) -> list[str]:
    audits = audit_memory_slopes(results)
    if not audits:
        return []
    lines = ["Cross-result memory slope audit:"]
    for audit in audits:
        slopes = audit["slopes_bytes_per_object"]
        largest = audit["largest_component_at_max_scale"]
        largest_text = "n/a"
        if largest is not None:
            largest_text = f"{largest['name']} ({fmt_bytes(int(largest['bytes']))})"
        lines.append(
            "  "
            f"{audit['implementation']}/{audit['profile']} "
            f"scales={','.join(str(scale) for scale in audit['scales'])} "
            f"tracked_slope={fmt_float(slopes.get('tracked'))} B/object "
            f"key_slope={fmt_float(slopes.get('key'))} "
            f"index_non_key_slope={fmt_float(slopes.get('index_non_key'))} "
            f"side_table_slope={fmt_float(slopes.get('side_table'))} "
            f"epoch_slot_slope={fmt_float(slopes.get('epoch_slot'))} "
            f"object_table_slope={fmt_float(slopes.get('object_table'))} "
            f"posting_slope={fmt_float(slopes.get('posting'))} "
            f"reverse_slope={fmt_float(slopes.get('reverse'))} "
            f"largest_at_max={largest_text}"
        )
    return lines


def classify_resource_signature(times: list[dict[str, str]]) -> str:
    """Return the strongest observed host-resource signature, not a limit claim."""
    max_cpu = max((as_float(t, "system_cpu_busy_max_percent") or 0.0 for t in times), default=0.0)
    max_core = max((as_float(t, "system_cpu_any_core_busy_max_percent") or 0.0 for t in times), default=0.0)
    max_iowait = max((as_float(t, "system_cpu_iowait_max_percent") or 0.0 for t in times), default=0.0)
    max_disk_util = max((as_float(t, "system_disk_util_percent_max") or 0.0 for t in times), default=0.0)
    min_mem = min(
        (as_float(t, "system_memavailable_min_percent") for t in times if as_float(t, "system_memavailable_min_percent") is not None),
        default=None,
    )
    swap = any((as_int(t, "swap_activity") or 0) > 0 for t in times)
    if swap or (min_mem is not None and min_mem < 10.0):
        return "memory"
    if max_iowait >= 20.0 or max_disk_util >= 80.0:
        return "IO"
    # A serial constraint remains evidence even when other work makes the
    # aggregate CPU figure high. The old classifier suppressed this case.
    if max_core >= 90.0:
        return "single-core"
    if max_cpu >= 85.0:
        return "aggregate CPU"
    return "none observed"


def classify_attribution(times: list[dict[str, str]]) -> str:
    """Classify process CPU evidence conservatively from sampled maxima.

    The maxima are not coincident and cannot prove causality. We report one
    component only when it is materially higher than every other tracked
    component; otherwise this is explicitly mixed or unresolved.
    """
    labels = {
        "vinyld": "system_tracked_cache_process_cpu_max_percent",
        "driver": "system_tracked_driver_cpu_max_percent",
        "backend": "system_tracked_backend_cpu_max_percent",
    }
    maxima = {
        label: max((as_float(time, key) or 0.0 for time in times), default=0.0)
        for label, key in labels.items()
    }
    observed = {label: value for label, value in maxima.items() if value > 0.0}
    if not observed:
        return "unresolved"
    highest_label, highest = max(observed.items(), key=lambda item: item[1])
    others = [value for label, value in observed.items() if label != highest_label]
    # A one-core sample is too weak to assign a system knee, and maxima from
    # competing processes within 25% are co-located mixed evidence.
    if highest < 100.0:
        return "unresolved"
    if others and highest < max(others) * 1.25:
        return "mixed"
    return highest_label


def phase_sample_classifications(time_path: Path) -> dict[str, dict[str, Any]]:
    """Classify only samples that the driver marked as belonging to a phase."""
    sample_path = Path(str(time_path) + ".samples.jsonl")
    grouped: dict[str, list[dict[str, Any]]] = {}
    try:
        lines = sample_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue
        phases = str(sample.get("phase_active", "")).split(",")
        for phase in (phase.strip() for phase in phases):
            if phase:
                grouped.setdefault(phase, []).append(sample)

    classifications: dict[str, dict[str, Any]] = {}
    for phase, samples in grouped.items():
        def maximum(key: str) -> float | None:
            values = [float(sample[key]) for sample in samples if sample.get(key) is not None]
            return max(values) if values else None

        signature_values = {
            "system_cpu_busy_max_percent": maximum("system_cpu_busy_percent"),
            "system_cpu_any_core_busy_max_percent": maximum(
                "system_cpu_any_core_busy_percent"
            ),
            "system_cpu_iowait_max_percent": maximum("system_cpu_iowait_percent"),
            "system_disk_util_percent_max": maximum("system_disk_util_percent"),
            "system_memavailable_min_percent": None,
            "swap_activity": 0,
            "system_tracked_cache_process_cpu_max_percent": maximum(
                "tracked_cache_process_cpu_percent"
            ),
            "system_tracked_driver_cpu_max_percent": maximum("tracked_driver_cpu_percent"),
            "system_tracked_backend_cpu_max_percent": maximum("tracked_backend_cpu_percent"),
        }
        mem_percentages = []
        for sample in samples:
            available = sample.get("system_meminfo_memavailable_kb")
            total = sample.get("system_meminfo_memtotal_kb")
            if available is not None and total:
                mem_percentages.append(100.0 * float(available) / float(total))
        if mem_percentages:
            signature_values["system_memavailable_min_percent"] = min(mem_percentages)
        signature_values = {
            key: value for key, value in signature_values.items() if value is not None
        }
        classifications[phase] = {
            "sample_count": len(samples),
            "resource_signature": classify_resource_signature([signature_values]),
            "attribution": classify_attribution([signature_values]),
        }
    return classifications


def phase_rates(row: dict[str, Any]) -> list[tuple[str, float]]:
    """Extract independent throughput measurements for marked workload phases."""
    fields = (
        ("load", "driver_load_requests_per_second"),
        ("warm", "driver_warm_requests_per_second"),
        ("phase4_pre", "driver_phase4_pre_requests_per_second"),
        ("phase4_sweep", "driver_phase4_sweep_requests_per_second"),
        ("phase4_post", "driver_phase4_post_requests_per_second"),
    )
    return [
        (phase, float(row[field]))
        for phase, field in fields
        if row.get(field) is not None and float(row[field]) > 0.0
    ]


def one_value_or_mixed(values: Iterable[str]) -> str:
    distinct = sorted(set(values))
    return distinct[0] if len(distinct) == 1 else "mixed"


def workload_phase_classifications(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep workload and marked-phase classification scopes independent."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("overall_valid") != 1:
            continue
        for phase, rps in phase_rates(row):
            item = dict(row)
            item["requests_per_second"] = rps
            phase_evidence = row.get("phase_sample_classifications", {}).get(phase, {})
            item["phase_resource_signature"] = phase_evidence.get(
                "resource_signature", "none observed"
            )
            item["phase_attribution"] = phase_evidence.get("attribution", "unresolved")
            item["phase_sample_count"] = phase_evidence.get("sample_count", 0)
            grouped.setdefault((str(row["workload"]), phase), []).append(item)
    classifications: list[dict[str, Any]] = []
    for (workload, phase), items in sorted(grouped.items()):
        classifications.append(
            {
                "workload": workload,
                "phase": phase,
                # A result directory has no client sweep. It must not infer
                # under-saturation merely because a host threshold was quiet.
                "load_state": "inconclusive",
                "resource_signature": one_value_or_mixed(
                    str(item["phase_resource_signature"]) for item in items
                ),
                "attribution": one_value_or_mixed(
                    str(item["phase_attribution"]) for item in items
                ),
                "phase_samples": sum(int(item["phase_sample_count"]) for item in items),
                "valid_runs": len(items),
                "requests_per_second_median": median(
                    [float(item["requests_per_second"]) for item in items]
                ),
                # Do not discard repetitions here. A normal RUNS=3 artifact
                # is one client point with three independent observations,
                # not one observation whose median happened to be retained.
                "run_requests_per_second": [
                    {
                        "run": item.get("run"),
                        "requests_per_second": float(item["requests_per_second"]),
                    }
                    for item in sorted(items, key=lambda item: int(item.get("run", 0)))
                ],
            }
        )
    return classifications


def command_env_value(remote: dict[str, str], key: str) -> str | None:
    for token in remote.get("command_env", "").split():
        if token.startswith(key + "="):
            return token.split("=", 1)[1]
    return None


def sweep_configuration(result_dir: Path) -> dict[str, Any]:
    metadata = parse_kv(result_dir / "metadata.env")
    remote = parse_kv(result_dir / "remote-run.env")
    provenance = parse_kv(result_dir / "build-provenance.env")
    client_count = (
        metadata.get("bench_clients")
        or remote.get("bench_clients_auto")
        or command_env_value(remote, "BENCH_CLIENTS")
    )
    worker_cap = (
        metadata.get("bench_vinyl_thread_pool_max")
        or remote.get("bench_vinyl_thread_pool_max")
        or command_env_value(remote, "BENCH_VINYL_THREAD_POOL_MAX")
    )
    thread_pools = (
        metadata.get("bench_vinyl_thread_pools")
        or remote.get("bench_vinyl_thread_pools")
        or command_env_value(remote, "BENCH_VINYL_THREAD_POOLS")
    )
    env_names = {
        "cachetag_configure_args": "CACHE_TAG_CONFIGURE_ARGS",
        "bench_set_interning": "BENCH_SET_INTERNING",
        "bench_stale_deliver": "BENCH_STALE_DELIVER",
        "objects": "OBJECTS",
        "tags_per_object": "TAGS_PER_OBJECT",
        "bench_profile": "BENCH_PROFILE",
        "bench_buckets": "BENCH_BUCKETS",
        "bench_tag_universe": "BENCH_TAG_UNIVERSE",
        "bench_tag_length_class": "BENCH_TAG_LENGTH_CLASS",
        "bench_storage": "BENCH_STORAGE",
        "bench_storage_kind": "BENCH_STORAGE_KIND",
        "bench_fellow_size": "BENCH_FELLOW_SIZE",
        "bench_fellow_segment_size": "BENCH_FELLOW_SEGMENT_SIZE",
        "bench_fellow_block_size": "BENCH_FELLOW_BLOCK_SIZE",
        "bench_buddy_size": "BENCH_BUDDY_SIZE",
        "bench_backend_body_bytes": "BENCH_BACKEND_BODY_BYTES",
        "bench_http_timeout": "BENCH_HTTP_TIMEOUT",
        "bench_warm_seconds": "BENCH_WARM_SECONDS",
        "cache_tag_bench_ttl": "CACHE_TAG_BENCH_TTL",
        "bench_cache_tag_persist": "BENCH_CACHE_TAG_PERSIST",
        "bench_cache_tag_wal_fsync": "BENCH_CACHE_TAG_WAL_FSYNC",
        "bench_cpuset_cpus": "BENCH_CPUSET_CPUS",
        "bench_driver_cpuset_cpus": "BENCH_DRIVER_CPUSET_CPUS",
        "bench_backend_cpuset_cpus": "BENCH_BACKEND_CPUSET_CPUS",
    }
    match_identity = tuple(
        (key, metadata.get(key, command_env_value(remote, env_name) or ""))
        for key, env_name in env_names.items()
    )
    revisions = {
        key: metadata.get(key, "")
        for key in ("vinyl_revision", "cachetag_revision", "slash_revision", "xkey_revision")
    }
    build_hashes = {
        key: value
        for key, value in (
            ("vinyl_build_input_sha256", provenance.get("vinyl_build_input_sha256", "")),
            ("cachetag_build_input_sha256", provenance.get("cachetag_build_input_sha256", "")),
            ("slash_build_input_sha256", provenance.get("slash_build_input_sha256", "")),
        )
        if value and value != "none"
    }
    return {
        "client_count": as_int({"value": client_count or ""}, "value"),
        "worker_cap": as_int({"value": worker_cap or ""}, "value"),
        "thread_pools": as_int({"value": thread_pools or ""}, "value"),
        # Build-input hashes are authoritative for dirty source trees, where
        # the revision fields are intentionally empty. Keep both in the key:
        # a changed hash must never merge into a same-revision cohort.
        "build": tuple(
            (key, value)
            for key, value in (
                *revisions.items(),
                *build_hashes.items(),
                ("image", metadata.get("image", "")),
            )
        ),
        "match_identity": match_identity,
        # A dirty source tree is identified by its captured build-input hash.
        # Reject only when neither revision nor authoritative hash exists.
        "cachetag_source_identity_recorded": bool(
            revisions["cachetag_revision"] or build_hashes.get("cachetag_build_input_sha256")
        ),
    }


def relative_noise_percent(values: list[float]) -> float | None:
    average = sum(values) / len(values)
    if average <= 0.0 or len(values) < 2:
        return None
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) / average * 100.0


def analyze_campaign_sweeps(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find repeated, matched client-sweep knees without assigning a cause.

    A single point can describe a resource signature but cannot prove a knee.
    This analyser therefore requires same-code repeated measurements at every
    client count and rejects changed worker settings rather than treating a
    two-variable experiment as a client sweep.
    """
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for result in results:
        config = result.get("sweep_configuration") or {}
        for classification in result.get("workload_phase_classifications", []):
            key = (
                result.get("hardware"),
                tuple(config.get("build", ())),
                tuple(config.get("match_identity", ())),
                classification["workload"],
                classification["phase"],
            )
            for repetition in classification.get("run_requests_per_second", []):
                grouped.setdefault(key, []).append(
                    {
                        "path": result.get("path"),
                        "run": repetition.get("run"),
                        "client_count": config.get("client_count"),
                        "worker_cap": config.get("worker_cap"),
                        "thread_pools": config.get("thread_pools"),
                        "cachetag_source_identity_recorded": config.get("cachetag_source_identity_recorded"),
                        "requests_per_second": repetition["requests_per_second"],
                    }
                )
    analyses: list[dict[str, Any]] = []
    for (hardware, build, match_identity, workload, phase), observations in sorted(grouped.items(), key=lambda item: str(item[0])):
        errors: list[str] = []
        worker_settings = {(item["worker_cap"], item["thread_pools"]) for item in observations}
        if not all(item.get("cachetag_source_identity_recorded") for item in observations):
            errors.append("cachetag_source_identity_missing")
        if None in {item["client_count"] for item in observations}:
            errors.append("client_count_missing")
        if any(cap is None or pools is None for cap, pools in worker_settings):
            errors.append("worker_configuration_missing")
        if len(worker_settings) != 1:
            errors.append("worker_configuration_changed")
        points: list[dict[str, Any]] = []
        for client_count in sorted({item["client_count"] for item in observations if item["client_count"] is not None}):
            values = [
                float(item["requests_per_second"])
                for item in observations
                if item["client_count"] == client_count
            ]
            if len(values) < SWEEP_MIN_REPETITIONS:
                errors.append(f"insufficient_repetitions:c{client_count}")
            points.append(
                {
                    "client_count": client_count,
                    "repetitions": len(values),
                    "requests_per_second_median": median(values),
                    "noise_percent": relative_noise_percent(values),
                }
            )
        if len(points) < 2:
            errors.append("insufficient_client_points")
        noise_values = [
            float(point["noise_percent"])
            for point in points
            if point["noise_percent"] is not None
        ]
        noise = max(noise_values, default=math.inf)
        threshold = max(SWEEP_MIN_SIGNIFICANT_CHANGE_PERCENT, noise * SWEEP_NOISE_MULTIPLIER)
        state = "inconclusive"
        if not errors:
            peak_index = max(range(len(points)), key=lambda index: points[index]["requests_per_second_median"])
            peak = points[peak_index]["requests_per_second_median"]
            has_significant_rollover = any(
                (peak - point["requests_per_second_median"]) / peak * 100.0 > threshold
                for point in points[peak_index + 1:]
            )
            if has_significant_rollover:
                state = "rollover"
            elif all(
                abs(points[index]["requests_per_second_median"] - points[index - 1]["requests_per_second_median"])
                / points[index - 1]["requests_per_second_median"] * 100.0 <= threshold
                for index in range(1, len(points))
            ):
                state = "plateau"
            elif points[-1]["requests_per_second_median"] > points[0]["requests_per_second_median"]:
                state = "rising"
        analyses.append(
            {
                "hardware": hardware,
                "build": build,
                "match_identity": match_identity,
                "workload": workload,
                "phase": phase,
                "load_state": state,
                "validation": "ok" if not errors else "failed",
                "validation_errors": sorted(set(errors)),
                "significance_threshold_percent": threshold if math.isfinite(threshold) else None,
                "points": points,
            }
        )
    return analyses


def summarize_result(result_dir: Path) -> tuple[str, str]:
    data = result_data(result_dir)

    lines = [
        f"Result: {data['result_id']}",
        f"Matrix: {data['matrix']}",
        f"Path: {data['path']}",
        f"Hardware: {data['hardware']}",
        f"Runs: valid={data['runs']['valid']} invalid={data['runs']['invalid']} process_pass={data['runs']['process_pass']} process_fail={data['runs']['process_fail']} total={data['runs']['total']}",
        f"VCL shape: stale_deliver={fmt_vcl_shape(data.get('bench_stale_deliver'))} "
        f"perf_stat={data.get('bench_perf_stat') or 'n/a'} "
        f"cohort={data.get('comparison_cohort_fingerprint') or 'n/a'}",
    ]
    wall = data["wall_seconds"]
    if wall["min"] is not None:
        lines.append(
            "Wall seconds: "
            f"min={wall['min']:.2f} p50={wall['p50']:.2f} "
            f"p95={wall['p95']:.2f} max={wall['max']:.2f}"
        )
    cpu = data["cpu"]
    memory = data["memory"]
    disk = data["disk"]
    lines.extend(
        [
            f"CPU: avg_max={fmt_float(cpu['avg_max_percent'], '%')} run_max={fmt_float(cpu['run_max_percent'], '%')} busiest_core_max={fmt_float(cpu['busiest_core_max_percent'], '%')}",
            f"Memory: memavailable_min={fmt_float(memory['memavailable_min_percent'], '%')} cgroup_peak={fmt_bytes(memory['cgroup_peak_bytes'])}",
            f"Swap: {'yes' if data['swap']['activity'] else 'no'}",
            f"Disk sectors delta: read={disk['read_sectors_delta']} write={disk['write_sectors_delta']}",
            f"Disk IO sampled max: read={fmt_rate(disk['read_bytes_per_second_max'])} write={fmt_rate(disk['write_bytes_per_second_max'])} flush={fmt_float(disk['flush_ios_per_second_max'], '/s')} util={fmt_float(disk['util_percent_max'], '%')} write_await={fmt_float(disk['write_await_ms_max'], 'ms')} flush_await={fmt_float(disk['flush_await_ms_max'], 'ms')}",
            "Classification roll-up: mixed (see per-workload/per-phase classifications)",
        ]
    )
    if (
        cpu["avg_max_percent"] is not None
        and cpu["avg_max_percent"] < LOW_CPU_BUSY_PERCENT
        and memory["memavailable_min_percent"] is not None
        and memory["memavailable_min_percent"] > (100.0 - LOW_MEMORY_DROP_PERCENT)
    ):
        lines.append(
            "WARNING: low CPU and memory pressure; do not treat this run as a throughput limit."
        )
    if data["runs"]["invalid"]:
        failed = [
            f"{row['workload']}.run-{row['run']}.time "
            f"({row.get('overall_validity_reason', 'invalid')}; "
            f"sampling={row.get('system_sampling_validity_reason', 'n/a')}; "
            f"memory={row.get('system_memory_validity_reason', 'n/a')}; "
            f"raw={row.get('raw_latency_validity_reason', 'n/a')})"
            for row in data["workloads"]
            if row.get("overall_valid") != 1
        ]
        lines.append("Invalid repetitions: " + ", ".join(failed[:10]))
    if data["driver_errors"]:
        rendered = [f"{row['file']}: {row['error']}" for row in data["driver_errors"][:10]]
        lines.append("Driver errors: " + " | ".join(rendered))
    if data["workload_phase_classifications"]:
        lines.append("Per-workload/per-phase classification:")
        for classification in data["workload_phase_classifications"]:
            lines.append(
                "  "
                f"{classification['workload']}/{classification['phase']}: "
                f"load_state={classification['load_state']} "
                f"resource_signature={classification['resource_signature']} "
                f"attribution={classification['attribution']} "
                f"rps={fmt_float(classification['requests_per_second_median'])}"
            )
    workload_summaries = data["workload_summaries"]
    if workload_summaries:
        lines.append("Per-workload memory and throughput:")
        lines.append(
            "  workload | runs | tracked | tracked/loaded | tracked/live | tracked/edge | vinyld_rss | cgroup_peak | load_rps | warm_rps | concurrent_read_rps"
        )
        if any(
            (row.get("driver_concurrent_target_rps_median") or 0) > 0
            for row in workload_summaries
        ):
            lines.append(
                "  note: concurrent_read_rps rows marked with cap are fixed offered-load latency/stability soaks, not throughput ceilings."
            )
        for row in workload_summaries:
            lines.append(
                "  "
                f"{row['workload']} | "
                f"{row['valid_runs']}/{row['runs']} | "
                f"{fmt_bytes(int(row['tracked_memory_bytes_median']) if row['tracked_memory_bytes_median'] is not None else None)} | "
                f"{fmt_float(row['tracked_bytes_per_loaded_object_median'])} | "
                f"{fmt_float(row['tracked_bytes_per_live_object_median'])} | "
                f"{fmt_float(row['tracked_bytes_per_edge_median'])} | "
                f"{fmt_bytes(int(row['vinyld_rss_max_kb_median'] * 1024) if row['vinyld_rss_max_kb_median'] is not None else None)} | "
                f"{fmt_bytes(int(row['cgroup_peak_bytes_median']) if row['cgroup_peak_bytes_median'] is not None else None)} | "
                f"{fmt_float(row['driver_load_requests_per_second_median'])} | "
                f"{fmt_float(row['driver_warm_requests_per_second_median'])} | "
                f"{fmt_concurrent_read_rps(row)}"
            )
            if row.get("cache_main_post_load_pss_kb_median") is not None:
                lines.append(
                    "    comparison endpoints: "
                    f"post_load_pss={fmt_bytes(int(row['cache_main_post_load_pss_kb_median'] * 1024))} "
                    f"confirmation_pss={fmt_bytes(int(row['cache_main_post_load_confirmation_pss_kb_median'] * 1024))} "
                    f"fixed_work={fmt_float(row.get('driver_load_fixed_work_seconds_median'), 's')} "
                    f"pending_drain={fmt_float(row.get('driver_load_pending_drain_seconds_median'), 's')} "
                    f"load_cache_main={fmt_float(scaled(row.get('cache_main_load_cpu_seconds_per_object_median'), 1_000_000), 'us/object')} "
                    f"warm_cache_main={fmt_float(scaled(row.get('cache_main_warm_cpu_seconds_per_hit_median'), 1_000_000), 'us/hit')} "
                    f"warm_p99={fmt_float(scaled(row.get('driver_warm_latency_p99_seconds_median'), 1_000), 'ms')} "
                    f"warm_max={fmt_float(scaled(row.get('driver_warm_latency_max_seconds_median'), 1_000), 'ms')}"
                )
            valid_runs = row.get("valid_runs")
            perf_parts = []
            for label, field, scale, unit in (
                ("instructions", "vinyld_warm_instructions", 1.0, ""),
                ("cycles", "vinyld_warm_cycles", 1.0, ""),
                ("task_clock", "vinyld_warm_task_clock_seconds", 1.0, "s"),
                ("instructions/hit", "vinyld_warm_instructions_per_hit", 1.0, ""),
                ("cycles/hit", "vinyld_warm_cycles_per_hit", 1.0, ""),
                ("task_clock/hit", "vinyld_warm_task_clock_seconds_per_hit", 1_000_000.0, "us/hit"),
                ("ipc", "vinyld_warm_ipc", 1.0, ""),
                ("running_min", "vinyld_warm_perf_stat_running_percent_min", 1.0, "%"),
            ):
                value = row.get(field + "_median")
                if value is None:
                    continue
                observations = row.get(field + "_observations")
                coverage = f"{observations}/{valid_runs}"
                if observations != valid_runs:
                    coverage += " report-only"
                perf_parts.append(
                    f"{label}(n={coverage})={fmt_float(value * scale, unit)}"
                )
            if perf_parts:
                lines.append("    warm perf stat (vinyld): " + " ".join(perf_parts))
            if row.get("cachetag_volatile_interned_sets_median") is not None:
                lines.append(
                    "    set interning VSC: "
                    f"sets={fmt_float(row.get('cachetag_volatile_interned_sets_median'))} "
                    f"refs={fmt_float(row.get('cachetag_volatile_interned_set_refs_median'))} "
                    f"hits/misses={fmt_float(row.get('cachetag_volatile_interned_set_hits_median'))}/"
                    f"{fmt_float(row.get('cachetag_volatile_interned_set_misses_median'))} "
                    f"set_bytes={fmt_bytes(int(row['cachetag_volatile_interned_set_bytes_median']) if row.get('cachetag_volatile_interned_set_bytes_median') is not None else None)} "
                    f"table_bytes={fmt_bytes(int(row['cachetag_volatile_interned_table_bytes_median']) if row.get('cachetag_volatile_interned_table_bytes_median') is not None else None)}"
                )
            if row.get("post_restart_tracked_memory_bytes_median") is not None:
                lines.append(
                    "    restart: "
                    f"post_load={fmt_bytes(int(row['post_load_tracked_memory_bytes_median']) if row.get('post_load_tracked_memory_bytes_median') is not None else None)} "
                    f"post_restart={fmt_bytes(int(row['post_restart_tracked_memory_bytes_median']))} "
                    f"cachetag_objects={fmt_float(row.get('post_restart_cachetag_mem_objects_median'))} "
                    f"cachetag_edges={fmt_float(row.get('post_restart_live_edges_median'))} "
                    f"volatile_side_table={fmt_bytes(int(row['post_restart_cachetag_volatile_side_table_bytes_median']) if row.get('post_restart_cachetag_volatile_side_table_bytes_median') is not None else None)} "
                    f"fdo_reads={fmt_float(row.get('post_restart_fellow_disk_obj_get_median'))} "
                    f"direct_probes={fmt_float(row.get('post_restart_purgemap_fellow_direct_probes_median'))}"
                )
            if row.get("driver_tag_shape_min_tags_per_object_median") is not None:
                lines.append(
                    "    tag shape: "
                    f"class={row.get('driver_tag_length_class') or 'n/a'} "
                    f"sample_objects={fmt_float(row.get('driver_tag_shape_sample_objects_median'))} "
                    f"tags/object={fmt_float(row.get('driver_tag_shape_min_tags_per_object_median'))}-"
                    f"{fmt_float(row.get('driver_tag_shape_max_tags_per_object_median'))} "
                    f"tag_len={fmt_float(row.get('driver_tag_shape_min_tag_length_median'))}-"
                    f"{fmt_float(row.get('driver_tag_shape_max_tag_length_median'))} "
                    f"sample_unique_tags={fmt_float(row.get('driver_tag_shape_sample_unique_tags_median'))}"
                )
            if row.get("driver_tag_shape_validation_ok_median") is not None:
                lines.append(
                    "    tag shape validation: "
                    f"configured={fmt_on_off(row.get('driver_tag_shape_validation_configured_median'))} "
                    f"ok={fmt_ok_fail(row.get('driver_tag_shape_validation_ok_median'))} "
                    f"expected_tags/object={fmt_float(row.get('driver_tag_shape_expected_tags_per_object_median'))} "
                    f"length_checked={fmt_on_off(row.get('driver_tag_shape_length_class_checked_median'))} "
                    f"expected_tag_len={fmt_float(row.get('driver_tag_shape_expected_min_tag_length_median'))}-"
                    f"{fmt_float(row.get('driver_tag_shape_expected_max_tag_length_median'))} "
                    f"unique_checked={fmt_on_off(row.get('driver_tag_shape_unique_count_checked_median'))} "
                    f"expected_unique={fmt_float(row.get('driver_tag_shape_expected_sample_unique_tags_median'))}"
                )
            if row.get("phase4_schema_valid_median") is not None:
                lines.append(
                    "    Phase 4 attribution: "
                    f"schema={'ok' if row.get('phase4_schema_valid_median') == 1 else 'fail'} "
                    f"compact={'yes' if row.get('phase4_compact_present_median') == 1 else 'no'} "
                    f"dropped={fmt_float(row.get('phase4_dropped_samples_median'))}"
                )
                for phase in PHASE4_DISTRIBUTIONS:
                    lines.append(
                        f"      {phase}: "
                        f"n={fmt_float(row.get(phase + '_samples_median'))} "
                        f"p99={fmt_float(row.get(phase + '_latency_p99_seconds_median'), 's')} "
                        f"p99.9={fmt_float(row.get(phase + '_latency_p999_seconds_median'), 's')} "
                        f"p99.99={fmt_float(row.get(phase + '_latency_p9999_seconds_median'), 's')} "
                        f"max={fmt_float(row.get(phase + '_latency_max_seconds_median'), 's')} "
                        f">50ms={fmt_float(row.get(phase + '_latency_above_50ms_median'))} "
                        f"offered={fmt_float(row.get(phase + '_offered_requests_per_second_median'), '/s')} "
                        f"achieved={fmt_float(row.get(phase + '_achieved_requests_per_second_median'), '/s')} "
                        f"scheduled/started/completed={fmt_float(row.get(phase + '_requests_scheduled_median'))}/"
                        f"{fmt_float(row.get(phase + '_requests_started_median'))}/"
                        f"{fmt_float(row.get(phase + '_requests_completed_median'))} "
                        f"skipped={fmt_float(row.get(phase + '_skipped_pacing_slots_median'))} "
                        f"hits/misses/errors={fmt_float(row.get(phase + '_hits_median'))}/"
                        f"{fmt_float(row.get(phase + '_misses_median'))}/"
                        f"{fmt_float(row.get(phase + '_errors_median'))}"
                    )
            if row.get("driver_phase5_hold_ms_configured_median") is not None:
                lines.append(
                    "    Phase 5 publication: "
                    f"hold={fmt_float(row.get('driver_phase5_hold_ms_configured_median'), 'ms')} "
                    f"publication={fmt_float(row.get('driver_phase5_hold_publication_median'))} "
                    f"cap_purges={fmt_float(row.get('driver_phase5_cap_purges_configured_median'))} "
                    f"reads={fmt_float(row.get('driver_phase5_held_reads_median'))} "
                    f"purges={fmt_float(row.get('driver_phase5_held_purges_median'))} "
                    f"queued={fmt_float(row.get('driver_phase5_held_purges_published_median'))} "
                    f"errors={fmt_float(row.get('driver_phase5_held_errors_median'))} "
                    f"read_p99={fmt_float(row.get('driver_phase5_held_read_latency_p99_seconds_median'), 's')} "
                    f"purge_p99={fmt_float(row.get('driver_phase5_held_purge_latency_p99_seconds_median'), 's')} "
                    f"sweep_wakeups={fmt_float(row.get('cachetag_sweep_wakeups_median'))} "
                    f"sweep_iterations={fmt_float(row.get('cachetag_sweep_iterations_median'))} "
                    f"deferral_max={fmt_float(row.get('cachetag_purgemap_auto_reclaim_defer_last_usec_median'), 'us')} "
                    f"pub={fmt_float(row.get('cachetag_publication_acquires_median'))}/"
                    f"{fmt_float(row.get('cachetag_publication_releases_median'))}"
                )
                if row.get("driver_phase5_shutdown_cold_wall_seconds_median") is not None:
                    lines.append(
                        "    Phase 5 cold/discard: "
                        f"wall={fmt_float(row.get('driver_phase5_shutdown_cold_wall_seconds_median'), 's')}"
                    )
            if row.get("cachetag_resize_reconciled_bytes_median") is not None:
                lines.append(
                    "    Phase 4 resize VSC: "
                    f"state={fmt_float(row.get('cachetag_side_resize_state_median'))} "
                    f"reason={fmt_float(row.get('cachetag_side_resize_reason_median'))} "
                    f"active/retiring/detached="
                    f"{fmt_float(row.get('cachetag_resize_active_bytes_median'), 'B')}/"
                    f"{fmt_float(row.get('cachetag_resize_retiring_bytes_median'), 'B')}/"
                    f"{fmt_float(row.get('cachetag_resize_detached_bytes_median'), 'B')} "
                    f"reconciled={fmt_float(row.get('cachetag_resize_reconciled_bytes_median'), 'B')}"
                )
                lines.append(
                    "      resize events: "
                    f"object_grow_publish={fmt_float(row.get('cachetag_object_segment_grow_publishes_median'))} "
                    f"object_detach={fmt_float(row.get('cachetag_object_segment_detach_batches_median'))} "
                    f"side_grow/rebuild/shrink="
                    f"{fmt_float(row.get('cachetag_side_resize_grow_publishes_median'))}/"
                    f"{fmt_float(row.get('cachetag_side_resize_rebuild_publishes_median'))}/"
                    f"{fmt_float(row.get('cachetag_side_resize_shrink_publishes_median'))} "
                    f"cancel/rollback="
                    f"{fmt_float(row.get('cachetag_side_resize_shrink_cancellations_median'))}/"
                    f"{fmt_float(row.get('cachetag_side_resize_shrink_rollbacks_median'))} "
                    f"low_water starts/restarts/rearms/cancels="
                    f"{fmt_float(row.get('cachetag_resize_low_water_starts_median'))}/"
                    f"{fmt_float(row.get('cachetag_resize_low_water_restarts_median'))}/"
                    f"{fmt_float(row.get('cachetag_resize_low_water_rearms_median'))}/"
                    f"{fmt_float(row.get('cachetag_resize_low_water_cancellations_median'))} "
                    f"attach_side_grows/max_old_buckets="
                    f"{fmt_float(row.get('cachetag_side_resize_attach_grow_publishes_median'))}/"
                    f"{fmt_float(row.get('cachetag_side_resize_attach_grow_old_buckets_max_median'))} "
                    f"emergency_object_allocs/max_old_capacity="
                    f"{fmt_float(row.get('cachetag_object_emergency_segment_allocations_median'))}/"
                    f"{fmt_float(row.get('cachetag_object_emergency_segment_old_capacity_max_median'))}"
                )
                lines.append(
                    "      resize batch holds: "
                    f"max={fmt_float(row.get('cachetag_resize_batch_obj_mtx_hold_max_usec_median'), 'us')} "
                    f">2/5/10ms="
                    f"{fmt_float(row.get('cachetag_resize_batch_obj_mtx_hold_over_2ms_median'))}/"
                    f"{fmt_float(row.get('cachetag_resize_batch_obj_mtx_hold_over_5ms_median'))}/"
                    f"{fmt_float(row.get('cachetag_resize_batch_obj_mtx_hold_over_10ms_median'))} "
                    f"legacy object_shrink={fmt_float(row.get('cachetag_object_shrink_calls_median'))} "
                    f"side_shrink_rehash={fmt_float(row.get('cachetag_side_shrink_rehash_calls_median'))} "
                    f"record_shrink_calls={fmt_float(row.get('cachetag_record_shrink_calls_median'))}"
                )
                if has_detached_free_metrics(row, ""):
                    lines.append(
                        "      detached container frees: "
                        + fmt_detached_free_metrics(row, "")
                    )
                for phase in PHASE4_VSC_DELTA_PHASES:
                    if has_detached_free_metrics(row, f"{phase}_delta_", f"{phase}_"):
                        lines.append(
                            f"        {phase}: "
                            + fmt_detached_free_metrics(row, f"{phase}_delta_", f"{phase}_")
                        )
            # Historical artifact compatibility: component/index decomposition
            # is rendered only for retired cachetag_epoch/cachetag_purgemap rows.
            if (
                row.get("cachetag_component_memory_bytes_median") is not None
                and historical_cachetag_implementation(str(row.get("implementation", "")))
            ):
                largest = largest_component(
                    {
                        "cachetag_mem_key_metadata_total_bytes": row.get(
                            "cachetag_mem_key_metadata_total_bytes_median"
                        ),
                        "cachetag_mem_object_table_bytes": row.get(
                            "cachetag_mem_object_table_bytes_median"
                        ),
                        "cachetag_mem_posting_bytes": row.get("cachetag_mem_posting_bytes_median"),
                        "cachetag_mem_reverse_bytes": row.get("cachetag_mem_reverse_bytes_median"),
                        "cachetag_index_non_key_bytes": row.get(
                            "cachetag_index_non_key_bytes_median"
                        ),
                    }
                )
                largest_text = "n/a"
                if largest is not None:
                    largest_text = f"{largest[0]} ({fmt_bytes(int(largest[1]))})"
                lines.append(
                    "    components: "
                    f"broad_tracked={fmt_bytes(int(row['tracked_memory_bytes_median']) if row.get('tracked_memory_bytes_median') is not None else None)} "
                    f"index={fmt_bytes(int(row['cachetag_index_memory_bytes_median']) if row.get('cachetag_index_memory_bytes_median') is not None else None)} "
                    f"key={fmt_bytes(int(row['cachetag_mem_key_bytes_median']) if row.get('cachetag_mem_key_bytes_median') is not None else None)} "
                    f"key_id_table={fmt_bytes(int(row['cachetag_mem_key_id_table_bytes_median']) if row.get('cachetag_mem_key_id_table_bytes_median') is not None else None)} "
                    f"key_metadata={fmt_bytes(int(row['cachetag_mem_key_metadata_total_bytes_median']) if row.get('cachetag_mem_key_metadata_total_bytes_median') is not None else None)} "
                    f"index_non_key={fmt_bytes(int(row['cachetag_index_non_key_bytes_median']) if row.get('cachetag_index_non_key_bytes_median') is not None else None)} "
                    f"object_table={fmt_bytes(int(row['cachetag_mem_object_table_bytes_median']) if row.get('cachetag_mem_object_table_bytes_median') is not None else None)} "
                    f"posting={fmt_bytes(int(row['cachetag_mem_posting_bytes_median']) if row.get('cachetag_mem_posting_bytes_median') is not None else None)} "
                    f"reverse={fmt_bytes(int(row['cachetag_mem_reverse_bytes_median']) if row.get('cachetag_mem_reverse_bytes_median') is not None else None)} "
                    f"gap={fmt_bytes(int(row['cachetag_component_gap_bytes_median']) if row.get('cachetag_component_gap_bytes_median') is not None else None)} "
                    f"largest={largest_text}"
                )
                if row.get("cachetag_mem_side_table_bytes_median") is not None:
                    lines.append(
                        "    index fixed: "
                        f"base={fmt_bytes(int(row['cachetag_mem_index_base_bytes_median']) if row.get('cachetag_mem_index_base_bytes_median') is not None else None)} "
                        f"side_table={fmt_bytes(int(row['cachetag_mem_side_table_bytes_median']))} "
                        f"side_buckets={fmt_float(row.get('cachetag_mem_side_table_buckets_median'))} "
                        f"shards={fmt_bytes(int(row['cachetag_mem_shard_table_bytes_median']) if row.get('cachetag_mem_shard_table_bytes_median') is not None else None)} "
                        f"epoch_slots={fmt_bytes(int(row['cachetag_mem_epoch_slot_bytes_median']) if row.get('cachetag_mem_epoch_slot_bytes_median') is not None else None)}"
                    )
                    if row.get("cachetag_side_table_grows_median") is not None:
                        lines.append(
                            "    side growth: "
                            f"grows={fmt_float(row.get('cachetag_side_table_grows_median'))} "
                            f"grow_usec={fmt_float(row.get('cachetag_side_table_grow_usec_median'))} "
                            f"grow_max_usec={fmt_float(row.get('cachetag_side_table_grow_max_usec_median'))} "
                            f"rehashed_slots={fmt_float(row.get('cachetag_side_table_grow_rehashed_slots_median'))}"
                        )
                    if row.get("driver_concurrent_read_rps_1s_min_median") is not None:
                        lines.append(
                            "    concurrent 1s bins: "
                            f"read_rps={fmt_float(row.get('driver_concurrent_read_rps_1s_min_median'))}-"
                            f"{fmt_float(row.get('driver_concurrent_read_rps_1s_max_median'))} "
                            f"insert_rps={fmt_float(row.get('driver_concurrent_insert_rps_1s_min_median'))}-"
                            f"{fmt_float(row.get('driver_concurrent_insert_rps_1s_max_median'))} "
                            f"purge_rps={fmt_float(row.get('driver_concurrent_purge_rps_1s_min_median'))}-"
                            f"{fmt_float(row.get('driver_concurrent_purge_rps_1s_max_median'))} "
                            f"read_max={fmt_float(row.get('driver_concurrent_read_latency_1s_max_seconds_median'), 's')} "
                            f"insert_max={fmt_float(row.get('driver_concurrent_insert_latency_1s_max_seconds_median'), 's')} "
                            f"purge_max={fmt_float(row.get('driver_concurrent_purge_latency_1s_max_seconds_median'), 's')}"
                        )
                    lines.append(
                        "    epoch/stale: "
                        f"epoch_slots={fmt_float(row.get('cachetag_mem_epoch_slot_used_slots_median'))}/"
                        f"{fmt_float(row.get('cachetag_mem_epoch_slot_capacity_slots_median'))} "
                        f"slack={fmt_float(row.get('cachetag_mem_epoch_slot_slack_slots_median'))} "
                        f"stale_fast_epoch_slot_hits={fmt_float(row.get('cachetag_stale_fast_epoch_slot_hits_median'))} "
                        f"stale_epoch_slot_fallbacks={fmt_float(row.get('cachetag_stale_epoch_slot_fallbacks_median'))}"
                    )
                    if row.get("cachetag_mem_compact_full_calls_median") is not None:
                        lines.append(
                            "    compaction diagnostics: "
                            f"full_calls={fmt_float(row.get('cachetag_mem_compact_full_calls_median'))} "
                            f"incremental_calls={fmt_float(row.get('cachetag_mem_compact_incremental_calls_median'))} "
                            f"handles_scanned={fmt_float(row.get('cachetag_mem_compaction_handles_scanned_median'))} "
                            f"handles_validated={fmt_float(row.get('cachetag_mem_compaction_handles_validated_median'))} "
                            f"handles_kept={fmt_float(row.get('cachetag_mem_compaction_handles_kept_median'))} "
                            f"gc_pin={fmt_float(row.get('cachetag_mem_key_gc_pin_release_median'))} "
                            f"gc_incremental={fmt_float(row.get('cachetag_mem_key_gc_incremental_median'))} "
                            f"gc_full={fmt_float(row.get('cachetag_mem_key_gc_full_compact_median'))} "
                            f"validation_obj_locks={fmt_float(row.get('cachetag_mem_validation_obj_lock_acquisitions_median'))} "
                            f"churn_backend={fmt_float(row.get('driver_cycle_backend_objects_total_median'))} "
                            f"churn_tagged={fmt_float(row.get('driver_cycle_tagged_objects_total_median'))} "
                            f"churn_load_successes={fmt_float(row.get('driver_cycle_load_successes_total_median'))}"
                        )
                if row.get("cachetag_mem_object_table_capacity_median") is not None:
                    lines.append(
                        "    capacity: "
                        f"object_table_high_water={fmt_float(row.get('cachetag_mem_object_table_high_water_slots_median'))}/"
                        f"{fmt_float(row.get('cachetag_mem_object_table_capacity_median'))} "
                        f"slack={fmt_float(row.get('cachetag_mem_object_table_slack_slots_median'))} "
                        f"posting_slots={fmt_float(row.get('cachetag_mem_posting_segment_used_slots_median'))}/"
                        f"{fmt_float(row.get('cachetag_mem_posting_segment_capacity_slots_median'))} "
                        f"slack={fmt_float(row.get('cachetag_mem_posting_segment_slack_slots_median'))}"
                    )
                if row.get("purgemap_fellow_attr_objects_written_median") is not None:
                    lines.append(
                        "    purgemap Fellow FDO: "
                        f"objects_written={fmt_float(row.get('purgemap_fellow_attr_objects_written_median'))} "
                        f"bytes_written={fmt_bytes(int(row['purgemap_fellow_attr_bytes_written_median']) if row.get('purgemap_fellow_attr_bytes_written_median') is not None else None)} "
                        f"direct_probes={fmt_float(row.get('purgemap_fellow_direct_probes_median'))} "
                        f"namespace_records={fmt_float(row.get('purgemap_fellow_namespace_records_probed_median'))} "
                        f"absent={fmt_float(row.get('purgemap_fellow_attr_absent_median'))} "
                        f"invalid={fmt_float(row.get('purgemap_fellow_attr_invalid_median'))} "
                        f"read_failures={fmt_float(row.get('purgemap_fellow_attr_read_failures_median'))} "
                        f"store_invariant_failures={fmt_float(row.get('purgemap_fellow_store_invariant_failures_median'))} "
                        f"volatile_fallbacks={fmt_float(row.get('purgemap_volatile_fallback_attaches_median'))}"
                    )
            if row.get("buddy_c_req_median") is not None:
                lines.append(
                    "    buddy: "
                    f"req={fmt_float(row.get('buddy_c_req_median'))} "
                    f"fail={fmt_float(row.get('buddy_c_fail_median'))} "
                    f"bytes={fmt_bytes(int(row['buddy_c_bytes_median']) if row.get('buddy_c_bytes_median') is not None else None)} "
                    f"freed={fmt_bytes(int(row['buddy_c_freed_median']) if row.get('buddy_c_freed_median') is not None else None)} "
                    f"g_bytes={fmt_bytes(int(row['buddy_g_bytes_median']) if row.get('buddy_g_bytes_median') is not None else None)} "
                    f"g_space={fmt_bytes(int(row['buddy_g_space_median']) if row.get('buddy_g_space_median') is not None else None)} "
                    f"g_alloc={fmt_float(row.get('buddy_g_alloc_median'))} "
                    f"lru_wakeups={fmt_float(row.get('buddy_c_lru_wakeups_median'))} "
                    f"reserve_used={fmt_bytes(int(row['buddy_c_lru_reserve_used_bytes_median']) if row.get('buddy_c_lru_reserve_used_bytes_median') is not None else None)} "
                    f"nuke_fill_reserve={fmt_float(row.get('buddy_c_lru_nuke_fill_reserve_median'))} "
                    f"nuke_reserve_drained={fmt_float(row.get('buddy_c_lru_nuke_reserve_drained_median'))}"
                )
    phase6_rows = [row for row in data["workloads"] if row.get("phase6_cycles")]
    if phase6_rows:
        lines.append("Phase 6 cycles:")
        for row in phase6_rows:
            lines.append(f"  {row['workload']}.run-{row['run']}:")
            for cycle in row["phase6_cycles"]:
                p99 = cycle["p99_seconds"]
                max_seconds = cycle["max_seconds"]
                rss = cycle["worker_rss_kb"]
                lines.append(
                    f"    cycle {cycle['cycle']:02d}: "
                    f"p99={fmt_float(p99 * 1000.0 if p99 is not None else None, 'ms')} "
                    f"max={fmt_float(max_seconds * 1000.0 if max_seconds is not None else None, 'ms')} "
                    f"worker_rss={fmt_bytes(rss * 1024 if rss is not None else None)} "
                    f"minflt_delta={fmt_whole_or_float(cycle['minflt_delta'])}"
                )
        for row in phase6_rows:
            lines.extend(row["phase6_warnings"])
    return data["hardware"], "\n".join(lines)


ARM_STATISTICAL_METRICS = (
    "cache_main_post_load_pss_kb",
    "cache_main_post_load_confirmation_pss_kb",
    "driver_load_requests_per_second",
    "driver_load_fixed_work_seconds",
    "driver_load_pending_drain_seconds",
    "cache_main_load_cpu_seconds",
    "cache_main_load_cpu_seconds_per_object",
    "driver_warm_requests_per_second",
    "driver_warm_latency_p99_seconds",
    "driver_warm_latency_max_seconds",
    "cache_main_warm_cpu_seconds",
    "cache_main_warm_cpu_seconds_per_hit",
    "vinyld_warm_instructions",
    "vinyld_warm_cycles",
    "vinyld_warm_task_clock_seconds",
    "vinyld_warm_instructions_per_hit",
    "vinyld_warm_cycles_per_hit",
    "vinyld_warm_task_clock_seconds_per_hit",
    "vinyld_warm_ipc",
    "vinyld_warm_instructions_running_percent",
    "vinyld_warm_cycles_running_percent",
    "vinyld_warm_task_clock_running_percent",
    "vinyld_warm_perf_stat_running_percent_min",
    "vtc_cpu_seconds",
    "vtc_cpu_seconds_per_backend_object",
    "wall_seconds",
    "vinyld_rss_max_kb",
    "cgroup_peak_bytes",
    "cachetag_purgemap_bytes",
)

ARM_EXACT_METRICS = (
    "driver_churn_expected_keys_total",
    "driver_churn_expected_live_generation_keys",
    "driver_cycle_backend_objects_total",
    "driver_cycle_tagged_objects_total",
    "driver_cycle_load_successes_total",
    "cachetag_purgemap_entries",
    "cachetag_purgemap_prunes",
)

ARM_METRIC_DISPLAY: dict[str, tuple[float, str]] = {
    "cache_main_post_load_pss_kb": (1.0 / 1024.0, "MiB"),
    "cache_main_post_load_confirmation_pss_kb": (1.0 / 1024.0, "MiB"),
    "cache_main_load_cpu_seconds_per_object": (1_000_000.0, "us/object"),
    "driver_warm_latency_p99_seconds": (1_000.0, "ms"),
    "driver_warm_latency_max_seconds": (1_000.0, "ms"),
    "cache_main_warm_cpu_seconds_per_hit": (1_000_000.0, "us/hit"),
    "vinyld_warm_instructions_per_hit": (1.0, "instructions/hit"),
    "vinyld_warm_cycles_per_hit": (1.0, "cycles/hit"),
    "vinyld_warm_task_clock_seconds_per_hit": (1_000_000.0, "us/hit"),
    "vinyld_warm_ipc": (1.0, "instructions/cycle"),
    "vinyld_warm_instructions_running_percent": (1.0, "% running"),
    "vinyld_warm_cycles_running_percent": (1.0, "% running"),
    "vinyld_warm_task_clock_running_percent": (1.0, "% running"),
    "vinyld_warm_perf_stat_running_percent_min": (1.0, "% running minimum"),
    "vtc_cpu_seconds_per_backend_object": (1_000_000.0, "us/object"),
    "vinyld_rss_max_kb": (1.0 / 1024.0, "MiB"),
    "cgroup_peak_bytes": (1.0 / (1024.0 * 1024.0), "MiB"),
    "cachetag_purgemap_bytes": (1.0 / (1024.0 * 1024.0), "MiB"),
}


def arm_stat(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": min(values), "median": median(values), "max": max(values)}


def fmt_arm_stat(
    stat: dict[str, float | int | None], scale: float = 1.0, eligible: int | None = None
) -> str:
    coverage = f"{stat['n']}/{eligible}" if eligible is not None else str(stat["n"])
    if stat["median"] is None:
        return f"n={coverage} n/a"
    return (
        f"n={coverage} {fmt_float(float(stat['median']) * scale)} "
        f"[{fmt_float(float(stat['min']) * scale)}, {fmt_float(float(stat['max']) * scale)}]"
    )


def delta_percent(after: float | None, before: float | None) -> float | None:
    if after is None or before is None or before == 0:
        return None
    return ((after - before) / before) * 100.0


def load_arm_results(arm_specs: list[str]) -> tuple[dict[str, list[dict[str, Any]]], list[tempfile.TemporaryDirectory[str]]]:
    arms: dict[str, list[dict[str, Any]]] = {}
    tempdirs: list[tempfile.TemporaryDirectory[str]] = []
    for spec in arm_specs:
        if "=" not in spec:
            raise SystemExit("--arm must be NAME=PATH")
        name, raw_path = spec.split("=", 1)
        if not name:
            raise SystemExit("--arm name must not be empty")
        roots, tempdir = expand_inputs([Path(raw_path)])
        if tempdir is not None:
            tempdirs.append(tempdir)
        result_dirs: list[Path] = []
        for root in roots:
            result_dirs.extend(find_result_dirs(root))
        if not result_dirs:
            raise SystemExit(f"no benchmark result directories found for arm {name}: {raw_path}")
        for result_dir in sorted(set(result_dirs)):
            arms.setdefault(name, []).append(result_data(result_dir))
    return arms, tempdirs


def comparison_arm_cohort_validity(arms: dict[str, list[dict[str, Any]]]) -> tuple[int, str]:
    """Reject an arm comparison when its comparison-v1 cohorts differ.

    The coarse hardware label is useful for historical artifacts but is not a
    sufficient BR-014 identity.  A comparison cohort is carried per result
    directory and must be identical across all active comparison arms.
    """
    active_results = [
        result
        for results in arms.values()
        for result in results
        if comparison_contract_active(Path(result["path"]))
    ]
    fingerprints = {
        str(result.get("comparison_cohort_fingerprint"))
        for results in arms.values()
        for result in results
        if result.get("comparison_cohort_fingerprint")
    }
    if not active_results:
        return 1, "not_applicable"
    if any(not result.get("comparison_cohort_fingerprint") for result in active_results):
        return 0, "cohort_fingerprint_missing"
    if not fingerprints:
        return 0, "cohort_fingerprint_missing"
    if len(fingerprints) != 1:
        return 0, "cohort_fingerprint_changed_across_arms"
    return 1, "ok"


def render_arm_comparison(arms: dict[str, list[dict[str, Any]]]) -> str:
    arm_names = list(arms)
    hardware = {
        result["hardware"]
        for results in arms.values()
        for result in results
    }
    rows_by_arm: dict[str, dict[str, list[dict[str, Any]]]] = {}
    judged_comparison = any(
        comparison_contract_active(Path(result["path"]))
        for results in arms.values()
        for result in results
    )
    workloads: set[str] = set()
    explicit_backends_by_profile: dict[str, set[str]] = {}
    for results in arms.values():
        for result in results:
            for row in result["workloads"]:
                backend = historical_cachetag_backend_for_arm(row)
                if backend is None:
                    continue
                profile = str(row.get("profile") or workload_profile(str(row["workload"])))
                explicit_backends_by_profile.setdefault(profile, set()).add(backend)
    for arm, results in arms.items():
        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            for row in result["workloads"]:
                if row.get("overall_valid") != 1:
                    continue
                for workload in arm_workload_keys(row, explicit_backends_by_profile):
                    grouped.setdefault(workload, []).append(row)
                    workloads.add(workload)
        rows_by_arm[arm] = grouped

    cohort_valid, cohort_reason = comparison_arm_cohort_validity(arms)

    lines = ["Arm comparison:"]
    for arm in arm_names:
        run_count = sum(len(result["workloads"]) for result in arms[arm])
        valid_run_count = sum(
            1
            for result in arms[arm]
            for row in result["workloads"]
            if row.get("overall_valid") == 1
        )
        lines.append(
            f"  {arm}: result_dirs={len(arms[arm])} "
            f"workload_runs={run_count} valid_workload_runs={valid_run_count}"
        )
    for arm in arm_names:
        shapes = sorted(
            {fmt_vcl_shape(result.get("bench_stale_deliver")) for result in arms[arm]}
        )
        lines.append(f"  {arm} VCL shape: " + (" | ".join(shapes) if shapes else "n/a"))
    if len(hardware) == 1:
        lines.append(f"Hardware: {next(iter(hardware))}")
    else:
        lines.append("WARNING: hardware fingerprint changed across arms:")
        for fingerprint in sorted(hardware):
            lines.append(f"  {fingerprint}")
    if cohort_valid != 1:
        lines.append(
            "WARNING: comparison rejected: "
            f"{cohort_reason}; comparative metrics are withheld."
        )
        return "\n".join(lines)

    for workload in sorted(workloads):
        lines.append("")
        lines.append(f"Workload: {workload}")
        for metric in ARM_STATISTICAL_METRICS:
            stats = []
            coverage = []
            for arm in arm_names:
                eligible_rows = rows_by_arm.get(arm, {}).get(workload, [])
                vals = [
                    float(row[metric])
                    for row in eligible_rows
                    if row.get(metric) is not None
                ]
                stats.append((arm, arm_stat(vals)))
                coverage.append((arm, len(vals), len(eligible_rows)))
            if all(stat["median"] is None for _, stat in stats):
                continue
            if (
                judged_comparison
                and metric in PERF_STAT_ROW_METRICS
                and any(observed != eligible for _, observed, eligible in coverage)
            ):
                rendered_coverage = " | ".join(
                    f"{arm}={observed}/{eligible}"
                    for arm, observed, eligible in coverage
                )
                lines.append(
                    f"  WARNING: {metric} comparison withheld: judged perf stat requires every valid repetition; coverage "
                    + rendered_coverage
                )
                continue
            scale, unit = ARM_METRIC_DISPLAY.get(metric, (1.0, ""))
            eligible_by_arm = {arm: eligible for arm, _, eligible in coverage}
            rendered = [
                f"{arm}={fmt_arm_stat(stat, scale, eligible_by_arm[arm])}"
                for arm, stat in stats
            ]
            delta = None
            if len(stats) == 2:
                delta = delta_percent(stats[1][1]["median"], stats[0][1]["median"])
            suffix = f" delta={fmt_float(delta, '%')}" if delta is not None else ""
            unit_suffix = f" ({unit})" if unit else ""
            lines.append(f"  {metric}{unit_suffix}: " + " | ".join(rendered) + suffix)
        exact_parts = []
        for metric in ARM_EXACT_METRICS:
            rendered_values = []
            seen_any = False
            for arm in arm_names:
                values = sorted(
                    {
                        int(float(row[metric]))
                        for row in rows_by_arm.get(arm, {}).get(workload, [])
                        if row.get(metric) is not None
                    }
                )
                if values:
                    seen_any = True
                rendered_values.append(f"{arm}={','.join(str(v) for v in values) if values else 'n/a'}")
            if seen_any:
                exact_parts.append(f"  {metric}: " + " | ".join(rendered_values))
        if exact_parts:
            lines.append("  exact counters:")
            lines.extend(exact_parts)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        help="Arm input as NAME=PATH. Repeat for N artifacts per arm.",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="Result directories or .tgz artifacts")
    args = parser.parse_args()

    if args.arm:
        arms, arm_tempdirs = load_arm_results(args.arm)
        try:
            if args.format == "json":
                print(json.dumps(arms, indent=2, sort_keys=True))
            else:
                print(render_arm_comparison(arms))
        finally:
            for tempdir in arm_tempdirs:
                tempdir.cleanup()
        return 0

    if not args.paths:
        raise SystemExit("paths are required unless --arm is used")

    roots, tempdir = expand_inputs(args.paths)
    try:
        result_dirs: list[Path] = []
        for root in roots:
            result_dirs.extend(find_result_dirs(root))
        if not result_dirs:
            raise SystemExit("no benchmark result directories found")
        if args.format == "json":
            results = [result_data(result_dir) for result_dir in sorted(set(result_dirs))]
            groups: dict[str, list[dict[str, Any]]] = {}
            for result in results:
                groups.setdefault(result["hardware"], []).append(result)
            print(
                json.dumps(
                    {
                        "hardware_groups": groups,
                        "results": results,
                        "memory_slope_audit": audit_memory_slopes(results),
                        "campaign_sweep_analysis": analyze_campaign_sweeps(results),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        result_payloads = [result_data(result_dir) for result_dir in sorted(set(result_dirs))]
        grouped: dict[str, list[str]] = {}
        rendered_by_path: dict[str, str] = {}
        for result in result_payloads:
            rendered_by_path[result["path"]] = summarize_result(Path(result["path"]))[1]
            grouped.setdefault(result["hardware"], []).append(rendered_by_path[result["path"]])
        for idx, (fingerprint, summaries) in enumerate(grouped.items(), 1):
            if idx > 1:
                print()
            print(f"Hardware Group: {fingerprint}")
            print("=" * (16 + len(fingerprint)))
            for summary_idx, summary in enumerate(summaries):
                if summary_idx:
                    print()
                print(summary)
        audit_lines = render_cross_result_audit(result_payloads)
        if audit_lines:
            print()
            print("\n".join(audit_lines))
        sweep_analyses = analyze_campaign_sweeps(result_payloads)
        if sweep_analyses:
            print()
            print("Campaign sweep analysis:")
            for analysis in sweep_analyses:
                points = ", ".join(
                    f"c{point['client_count']}={fmt_float(point['requests_per_second_median'])}/s"
                    for point in analysis["points"]
                )
                errors = ",".join(analysis["validation_errors"]) or "none"
                print(
                    "  "
                    f"{analysis['workload']}/{analysis['phase']}: "
                    f"load_state={analysis['load_state']} validation={analysis['validation']} "
                    f"errors={errors}; {points}"
                )
    finally:
        if tempdir is not None:
            tempdir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
