#!/usr/bin/env python3
"""Run a command and write portable process timing metrics."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import queue
import resource
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field


TRACKED_PROCESS_LABELS = ("vinyltest", "vinyld", "cache_process", "driver", "backend")
STATUS_MEMORY_FIELDS = {"VmRSS", "RssAnon", "RssFile", "VmData", "VmSwap"}
def usage_delta(before: resource.struct_rusage, after: resource.struct_rusage):
    return {
        "user_seconds": after.ru_utime - before.ru_utime,
        "system_seconds": after.ru_stime - before.ru_stime,
        "max_rss_kb": after.ru_maxrss,
        "minor_faults": after.ru_minflt - before.ru_minflt,
        "major_faults": after.ru_majflt - before.ru_majflt,
        "voluntary_context_switches": after.ru_nvcsw - before.ru_nvcsw,
        "involuntary_context_switches": after.ru_nivcsw - before.ru_nivcsw,
    }


def proc_values(path: Path, keys: set[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with path.open(encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2 or parts[0].rstrip(":") not in keys:
                    continue
                key = parts[0].rstrip(":")
                try:
                    values[key] = int(parts[1])
                except ValueError:
                    continue
    except OSError:
        pass
    return values


def system_memory_snapshot() -> dict[str, int]:
    values = {}
    vmstat = proc_values(Path("/proc/vmstat"), {"pswpin", "pswpout", "pgmajfault"})
    meminfo = proc_values(
        Path("/proc/meminfo"),
        {"MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree", "SwapCached"},
    )
    for key, value in vmstat.items():
        values["vmstat_" + key] = value
    for key, value in meminfo.items():
        values["meminfo_" + key.lower() + "_kb"] = value
    return values


def system_memory_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    metrics: dict[str, int] = {}
    for key in sorted(set(before) | set(after)):
        if key in before:
            metrics[key + "_before"] = before[key]
        if key in after:
            metrics[key + "_after"] = after[key]
        if key in before and key in after:
            metrics[key + "_delta"] = after[key] - before[key]
    swap_in = metrics.get("vmstat_pswpin_delta", 0)
    swap_out = metrics.get("vmstat_pswpout_delta", 0)
    metrics["swap_activity"] = 1 if swap_in > 0 or swap_out > 0 else 0
    return metrics


def read_proc_stat() -> dict[str, dict[str, int]]:
    fields = (
        "user",
        "nice",
        "system",
        "idle",
        "iowait",
        "irq",
        "softirq",
        "steal",
        "guest",
        "guest_nice",
    )
    cpus: dict[str, dict[str, int]] = {}
    try:
        with Path("/proc/stat").open(encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if not parts or not parts[0].startswith("cpu"):
                    continue
                values = {}
                for name, raw in zip(fields, parts[1:]):
                    try:
                        values[name] = int(raw)
                    except ValueError:
                        values[name] = 0
                cpus[parts[0]] = values
    except OSError:
        pass
    return cpus


def cpu_percentages(prev: dict[str, int], curr: dict[str, int]) -> tuple[float, float, float]:
    prev_total = sum(prev.values())
    curr_total = sum(curr.values())
    total_delta = curr_total - prev_total
    if total_delta <= 0:
        return 0.0, 0.0, 0.0
    idle_delta = (curr.get("idle", 0) + curr.get("iowait", 0)) - (
        prev.get("idle", 0) + prev.get("iowait", 0)
    )
    iowait_delta = curr.get("iowait", 0) - prev.get("iowait", 0)
    steal_delta = curr.get("steal", 0) - prev.get("steal", 0)
    busy = max(0, total_delta - idle_delta)
    return (
        100.0 * busy / total_delta,
        100.0 * max(0, iowait_delta) / total_delta,
        100.0 * max(0, steal_delta) / total_delta,
    )


def load_snapshot() -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    try:
        parts = Path("/proc/loadavg").read_text(encoding="ascii").split()
        values["load1"] = float(parts[0])
        running, total = parts[3].split("/", 1)
        values["procs_running"] = int(running)
        values["procs_total"] = int(total)
    except (OSError, ValueError, IndexError):
        pass
    try:
        stat = proc_values(Path("/proc/stat"), {"procs_blocked"})
        if "procs_blocked" in stat:
            values["procs_blocked"] = stat["procs_blocked"]
    except OSError:
        pass
    return values


def disk_snapshot() -> dict[str, int]:
    values = {
        "device_count": 0,
        "read_ios": 0,
        "read_sectors": 0,
        "read_ticks_ms": 0,
        "write_ios": 0,
        "write_sectors": 0,
        "write_ticks_ms": 0,
        "discard_ios": 0,
        "discard_sectors": 0,
        "discard_ticks_ms": 0,
        "flush_ios": 0,
        "flush_ticks_ms": 0,
        "ios_in_progress": 0,
        "io_ticks_ms": 0,
        "weighted_io_ticks_ms": 0,
    }
    try:
        with Path("/proc/diskstats").open(encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                if name.startswith(("loop", "ram", "dm-")):
                    continue
                try:
                    values["device_count"] += 1
                    values["read_ios"] += int(parts[3])
                    values["read_sectors"] += int(parts[5])
                    values["read_ticks_ms"] += int(parts[6])
                    values["write_ios"] += int(parts[7])
                    values["write_sectors"] += int(parts[9])
                    values["write_ticks_ms"] += int(parts[10])
                    values["ios_in_progress"] += int(parts[11])
                    values["io_ticks_ms"] += int(parts[12])
                    values["weighted_io_ticks_ms"] += int(parts[13])
                    if len(parts) >= 18:
                        values["discard_ios"] += int(parts[14])
                        values["discard_sectors"] += int(parts[16])
                        values["discard_ticks_ms"] += int(parts[17])
                    if len(parts) >= 20:
                        values["flush_ios"] += int(parts[18])
                        values["flush_ticks_ms"] += int(parts[19])
                except ValueError:
                    continue
    except OSError:
        pass
    return values


def disk_sample_metrics(
    before: dict[str, int], after: dict[str, int], elapsed: float
) -> dict[str, float]:
    if elapsed <= 0:
        return {}
    read_ios = after.get("read_ios", 0) - before.get("read_ios", 0)
    write_ios = after.get("write_ios", 0) - before.get("write_ios", 0)
    flush_ios = after.get("flush_ios", 0) - before.get("flush_ios", 0)
    io_ticks_ms = after.get("io_ticks_ms", 0) - before.get("io_ticks_ms", 0)
    weighted_io_ticks_ms = after.get("weighted_io_ticks_ms", 0) - before.get("weighted_io_ticks_ms", 0)
    read_ticks_ms = after.get("read_ticks_ms", 0) - before.get("read_ticks_ms", 0)
    write_ticks_ms = after.get("write_ticks_ms", 0) - before.get("write_ticks_ms", 0)
    flush_ticks_ms = after.get("flush_ticks_ms", 0) - before.get("flush_ticks_ms", 0)
    return {
        "read_bytes_per_second": 512.0 * (after.get("read_sectors", 0) - before.get("read_sectors", 0)) / elapsed,
        "write_bytes_per_second": 512.0 * (after.get("write_sectors", 0) - before.get("write_sectors", 0)) / elapsed,
        "read_ios_per_second": read_ios / elapsed,
        "write_ios_per_second": write_ios / elapsed,
        "flush_ios_per_second": flush_ios / elapsed,
        "util_percent": 100.0 * io_ticks_ms / (elapsed * 1000.0),
        "avg_queue_depth": weighted_io_ticks_ms / (elapsed * 1000.0),
        "read_await_ms": read_ticks_ms / read_ios if read_ios > 0 else 0.0,
        "write_await_ms": write_ticks_ms / write_ios if write_ios > 0 else 0.0,
        "flush_await_ms": flush_ticks_ms / flush_ios if flush_ios > 0 else 0.0,
    }


def cgroup_memory_snapshot() -> dict[str, int | str]:
    values: dict[str, int | str] = {}
    candidates = [
        Path("/sys/fs/cgroup"),
    ]
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="ascii")
        for line in cgroup.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                relative = parts[2].lstrip("/")
                candidates.insert(0, Path("/sys/fs/cgroup") / relative)
                break
    except OSError:
        pass
    for base in candidates:
        current = base / "memory.current"
        if not current.exists():
            continue
        for name in ("memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak"):
            path = base / name
            try:
                raw = path.read_text(encoding="ascii").strip()
            except OSError:
                continue
            key = "cgroup_" + name.replace(".", "_")
            try:
                values[key] = int(raw)
            except ValueError:
                values[key] = raw
        for name in ("memory.max", "memory.swap.max"):
            path = base / name
            try:
                raw = path.read_text(encoding="ascii").strip()
            except OSError:
                continue
            key = "cgroup_" + name.replace(".", "_")
            if raw == "max":
                values[key] = raw
            else:
                try:
                    values[key] = int(raw)
                except ValueError:
                    values[key] = raw
        for name in ("memory.stat", "memory.events", "memory.events.local"):
            path = base / name
            try:
                lines = path.read_text(encoding="ascii").splitlines()
            except OSError:
                continue
            prefix = "cgroup_" + name.replace(".", "_").replace("-", "_")
            for line in lines:
                parts = line.split()
                if len(parts) != 2:
                    continue
                try:
                    values[f"{prefix}_{parts[0]}"] = int(parts[1])
                except ValueError:
                    values[f"{prefix}_{parts[0]}"] = parts[1]
        values["cgroup_memory_path"] = str(base)
        break
    return values


def delta(prefix: str, before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    metrics = {}
    for key in sorted(set(before) | set(after)):
        if key in before:
            metrics[f"{prefix}_{key}_before"] = before[key]
        if key in after:
            metrics[f"{prefix}_{key}_after"] = after[key]
        if key in before and key in after:
            metrics[f"{prefix}_{key}_delta"] = after[key] - before[key]
    return metrics


@dataclass(frozen=True)
class ThreadCpuSample:
    pid: int
    tid: int
    ppid: int
    comm: str
    exe: str
    start_time_ticks: int
    cpu_ticks: int
    rss_kb: int
    memory_kb: dict[str, int]


@dataclass
class TrackedProcessState:
    label: str
    source: str
    status: str = "unseen"
    match_count: int = 0
    pid: int | None = None
    pids: str = ""
    comm: str = ""
    cmd: str = ""
    exe: str = ""
    start_time_ticks: int | None = None
    cpu_max_percent: float = 0.0
    rss_max_kb: int = 0
    memory_max_kb: dict[str, int] = field(default_factory=dict)
    detail_values: dict[str, int | str] = field(default_factory=dict)


def read_exe(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def read_process_memory_kb(pid: int) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        status_values = proc_values(Path(f"/proc/{pid}/status"), STATUS_MEMORY_FIELDS)
        for key, value in status_values.items():
            values[f"status_{key}_kb"] = value
    except OSError:
        pass
    return values


def parse_proc_stat(raw: str) -> tuple[int, str, int, int, int] | None:
    start = raw.find("(")
    end = raw.rfind(")")
    if start < 0 or end < start:
        return None
    try:
        pid = int(raw[:start].strip())
    except ValueError:
        return None
    comm = raw[start + 1 : end]
    parts = raw[end + 2 :].split()
    if len(parts) < 20:
        return None
    try:
        ppid = int(parts[1])
        cpu_ticks = int(parts[11]) + int(parts[12])
        start_time_ticks = int(parts[19])
    except ValueError:
        return None
    return pid, comm, ppid, cpu_ticks, start_time_ticks


def process_parent_snapshot() -> dict[int, int]:
    parents: dict[int, int] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return parents
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parsed = parse_proc_stat((entry / "stat").read_text(encoding="ascii"))
        except OSError:
            continue
        if parsed is None:
            continue
        pid, _, ppid, _, _ = parsed
        parents[pid] = ppid
    return parents


def descendant_pids(root_pid: int) -> set[int]:
    parents = process_parent_snapshot()
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if pid not in descendants and ppid in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def process_thread_snapshot(root_pid: int | None) -> dict[tuple[int, int], ThreadCpuSample]:
    if root_pid is None or not Path("/proc").is_dir():
        return {}
    pids = descendant_pids(root_pid)
    samples: dict[tuple[int, int], ThreadCpuSample] = {}
    for pid in pids:
        task_dir = Path(f"/proc/{pid}/task")
        if not task_dir.is_dir():
            continue
        exe = read_exe(pid)
        memory_kb = read_process_memory_kb(pid)
        rss_kb = memory_kb.get("status_VmRSS_kb", 0)
        try:
            tasks = list(task_dir.iterdir())
        except OSError:
            continue
        for task in tasks:
            if not task.name.isdigit():
                continue
            try:
                parsed = parse_proc_stat((task / "stat").read_text(encoding="ascii"))
            except OSError:
                continue
            if parsed is None:
                continue
            _, comm, ppid, cpu_ticks, start_time_ticks = parsed
            tid = int(task.name)
            samples[(pid, tid)] = ThreadCpuSample(
                pid,
                tid,
                ppid,
                comm,
                exe,
                start_time_ticks,
                cpu_ticks,
                rss_kb,
                memory_kb,
            )
    return samples


def metric_safe_text(value: str, limit: int = 160) -> str:
    cleaned = " ".join(value.replace("\n", " ").split())
    return cleaned[:limit]


def tracked_process_source(label: str) -> str:
    if label == "vinyltest":
        return "root-pid"
    if label == "vinyld":
        return "descendant-comm-or-exe"
    if label == "cache_process":
        return "descendant-comm-and-exe"
    if label == "driver":
        return "descendant-exe"
    if label == "backend":
        return "descendant-exe"
    return "unknown"


def tracked_process_matches(
    label: str, root_pid: int, processes: dict[int, ThreadCpuSample]
) -> list[ThreadCpuSample]:
    matches: list[ThreadCpuSample] = []
    for sample in processes.values():
        base = os.path.basename(sample.exe)
        if label == "vinyltest":
            if sample.pid == root_pid:
                matches.append(sample)
        elif label == "vinyld":
            if sample.comm == "vinyld" or base == "vinyld":
                matches.append(sample)
        elif label == "cache_process":
            if sample.comm == "cache-main" and base == "vinyld":
                matches.append(sample)
        elif label == "driver":
            if base == "cachetag-http-workload-driver":
                matches.append(sample)
        elif label == "backend":
            if base == "cachetag-benchmark-backend":
                matches.append(sample)
    return sorted(matches, key=lambda sample: sample.pid)


def process_representatives(
    samples: dict[tuple[int, int], ThreadCpuSample]
) -> dict[int, ThreadCpuSample]:
    representatives: dict[int, ThreadCpuSample] = {}
    for sample in samples.values():
        previous = representatives.get(sample.pid)
        if previous is None or sample.tid == sample.pid:
            representatives[sample.pid] = sample
    return representatives


@dataclass(frozen=True)
class DetailedProcessRequest:
    pid: int
    start_time_ticks: int


class DetailedProcessCollector:
    """Run mmap-sensitive procfs reads outside the cadence thread."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.helper_path = Path(__file__).with_name("read_process_details.py")
        self.requests: queue.Queue[DetailedProcessRequest] = queue.Queue(maxsize=1)
        self.results: queue.Queue[dict[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.active_process: subprocess.Popen[str] | None = None
        self.outstanding: set[DetailedProcessRequest] = set()
        self.abandoned_processes: dict[DetailedProcessRequest, subprocess.Popen[str]] = {}
        self.attempts = 0
        self.successes = 0
        self.timeouts = 0
        self.errors = 0
        self.identity_mismatches = 0
        self.skipped_outstanding = 0
        self.abandoned_helpers = 0
        self.cancelled = 0
        self.max_concurrent_helpers = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, request: DetailedProcessRequest) -> bool:
        self._reap_abandoned()
        with self.lock:
            if self.outstanding:
                self.skipped_outstanding += 1
                return False
            self.outstanding.add(request)
            self.attempts += 1
        try:
            self.requests.put_nowait(request)
        except queue.Full:
            with self.lock:
                self.outstanding.discard(request)
                self.skipped_outstanding += 1
            return False
        return True

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            active = self.active_process
        if active is not None and active.poll() is None:
            try:
                active.kill()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=max(1.0, self.timeout + 0.5))
            if self.thread.is_alive():
                with self.lock:
                    self.abandoned_helpers += 1
        self._reap_abandoned()

    def snapshot_metrics(self) -> dict[str, int]:
        self._reap_abandoned()
        with self.lock:
            active = int(self.active_process is not None and self.active_process.poll() is None)
            active += sum(1 for proc in self.abandoned_processes.values() if proc.poll() is None)
            return {
                "attempts": self.attempts,
                "successes": self.successes,
                "timeouts": self.timeouts,
                "errors": self.errors,
                "identity_mismatches": self.identity_mismatches,
                "skipped_outstanding": self.skipped_outstanding,
                "abandoned_helpers": self.abandoned_helpers,
                "cancelled": self.cancelled,
                "active_helpers": active,
                "max_concurrent_helpers": self.max_concurrent_helpers,
            }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                request = self.requests.get(timeout=0.05)
            except queue.Empty:
                continue
            result = self._collect(request)
            self.results.put(result)
            with self.lock:
                if result.get("status") != "abandoned":
                    self.outstanding.discard(request)
            self._reap_abandoned()

    def _reap_abandoned(self) -> None:
        with self.lock:
            abandoned = list(self.abandoned_processes.items())
        for request, proc in abandoned:
            if proc.poll() is None:
                continue
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            with self.lock:
                self.abandoned_processes.pop(request, None)
                self.outstanding.discard(request)

    def _collect(self, request: DetailedProcessRequest) -> dict[str, object]:
        result: dict[str, object] = {
            "pid": request.pid,
            "expected_start_time_ticks": request.start_time_ticks,
            "completed_monotonic": time.monotonic(),
        }
        command = [
            sys.executable,
            str(self.helper_path),
            "--pid",
            str(request.pid),
            "--expected-start-time",
            str(request.start_time_ticks),
        ]
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            with self.lock:
                self.errors += 1
            result.update({"status": "spawn_error", "error": f"{exc.errno}:{exc.strerror}"})
            return result
        with self.lock:
            self.active_process = proc
            self.max_concurrent_helpers = max(self.max_concurrent_helpers, 1)
        try:
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                with self.lock:
                    self.timeouts += 1
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.communicate(timeout=0.25)
                except subprocess.TimeoutExpired:
                    with self.lock:
                        self.abandoned_helpers += 1
                        self.abandoned_processes[request] = proc
                    result["status"] = "abandoned"
                else:
                    result["status"] = "timeout"
                return result
            if self.stop_event.is_set() and proc.returncode != 0:
                with self.lock:
                    self.cancelled += 1
                result["status"] = "cancelled"
                return result
            if proc.returncode != 0:
                with self.lock:
                    self.errors += 1
                result.update(
                    {
                        "status": "helper_error",
                        "error": metric_safe_text(stderr or f"exit:{proc.returncode}"),
                    }
                )
                return result
            try:
                payload = json.loads(stdout)
            except (json.JSONDecodeError, TypeError):
                with self.lock:
                    self.errors += 1
                result.update({"status": "invalid_output", "error": metric_safe_text(stdout)})
                return result
            result.update(payload)
            status = result.get("status")
            if (
                result.get("pid") != request.pid
                or result.get("expected_start_time_ticks") != request.start_time_ticks
                or result.get("observed_start_time_ticks_after") != request.start_time_ticks
            ):
                status = "identity_mismatch"
                result["status"] = status
            with self.lock:
                if status == "ok":
                    self.successes += 1
                elif status == "identity_mismatch":
                    self.identity_mismatches += 1
                else:
                    self.errors += 1
            return result
        finally:
            result["completed_monotonic"] = time.monotonic()
            with self.lock:
                if self.active_process is proc:
                    self.active_process = None


class SystemSampler:
    CADENCE_MIN_RATIO = 0.80
    CADENCE_MAX_GAP_INTERVALS = 5.0
    CADENCE_MAX_GAP_FLOOR_SECONDS = 1.0

    def __init__(
        self,
        interval: float = 1.0,
        sample_path: Path | None = None,
        detailed_memory_interval: float = 1.0,
        detailed_memory_timeout: float = 0.5,
    ) -> None:
        self.interval = interval
        self.sample_path = sample_path
        self.detailed_memory_interval = detailed_memory_interval
        self.detailed_memory_timeout = detailed_memory_timeout
        self.start_monotonic = 0.0
        self.stop_monotonic: float | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.thread_stalled = False
        self.thread_error = ""
        self.lock = threading.Lock()
        self.prev_cpu: dict[str, dict[str, int]] | None = None
        self.samples = 0
        self.sample_timestamps: list[float] = []
        self.cpu_samples = 0
        self.cpu_busy_sum = 0.0
        self.cpu_busy_max = 0.0
        self.cpu_any_core_busy_max = 0.0
        self.cpu_iowait_sum = 0.0
        self.cpu_iowait_max = 0.0
        self.cpu_steal_sum = 0.0
        self.cpu_steal_max = 0.0
        self.memavailable_min: int | None = None
        self.memavailable_before: int | None = None
        self.swapfree_min: int | None = None
        self.load1_max: float | None = None
        self.procs_running_max: int | None = None
        self.procs_blocked_max: int | None = None
        self.memtotal_kb: int | None = None
        self.memfree_min: int | None = None
        self.swaptotal_kb: int | None = None
        self.cgroup_memory_current_max: int | None = None
        self.cgroup_memory_peak_max: int | None = None
        self.cgroup_memory_swap_current_max: int | None = None
        self.cgroup_memory_swap_peak_max: int | None = None
        self.cgroup_memory_path: str | None = None
        self.cgroup_memory_max: int | str | None = None
        self.cgroup_memory_swap_max: int | str | None = None
        self.disk_before: dict[str, int] = {}
        self.prev_disk: dict[str, int] = {}
        self.prev_disk_monotonic = 0.0
        self.disk_after: dict[str, int] = {}
        self.disk_samples = 0
        self.disk_metric_sums: dict[str, float] = {}
        self.disk_metric_max: dict[str, float] = {}
        self.cpu_count = os.cpu_count() or 1
        self.process_root_pid: int | None = None
        self.prev_thread_cpu: dict[tuple[int, int], ThreadCpuSample] | None = None
        self.prev_process_sample_monotonic: float | None = None
        self.hot_threads: dict[tuple[int, int], tuple[float, ThreadCpuSample]] = {}
        self.hot_processes: dict[int, tuple[float, ThreadCpuSample]] = {}
        self.tracked_processes = {
            label: TrackedProcessState(label=label, source=tracked_process_source(label))
            for label in TRACKED_PROCESS_LABELS
        }
        self.detailed_collector = DetailedProcessCollector(detailed_memory_timeout)
        self.detailed_identity: DetailedProcessRequest | None = None
        self.next_detailed_memory_monotonic = 0.0
        self.detailed_last_success_monotonic: float | None = None
        self.detailed_last_success_pid: int | None = None
        self.detailed_last_success_start_time_ticks: int | None = None
        if self.sample_path is not None:
            self.sample_path.parent.mkdir(parents=True, exist_ok=True)
            self.sample_path.write_text("", encoding="utf-8")

    def start(self) -> None:
        self.start_monotonic = time.monotonic()
        mem = system_memory_snapshot()
        cgroup = cgroup_memory_snapshot()
        load = load_snapshot()
        self.memavailable_before = mem.get("meminfo_memavailable_kb")
        self.prev_cpu = read_proc_stat()
        self.disk_before = disk_snapshot()
        self.prev_disk = self.disk_before
        self.prev_disk_monotonic = self.start_monotonic
        with self.lock:
            self._update_non_cpu_locked(mem, cgroup, load)
        self.detailed_collector.start()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_process_root(self, pid: int) -> None:
        with self.lock:
            self.process_root_pid = pid
            self.prev_thread_cpu = None
            self.prev_process_sample_monotonic = None

    def stop(self) -> None:
        self.stop_monotonic = time.monotonic()
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=max(1.0, self.interval * 2))
            self.thread_stalled = self.thread.is_alive()
        self.detailed_collector.stop()
        with self.lock:
            self._drain_detailed_results_locked()
        self.disk_after = disk_snapshot()

    def _run(self) -> None:
        next_deadline = self.start_monotonic + self.interval
        while True:
            remaining = max(0.0, next_deadline - time.monotonic())
            if self.stop_event.wait(remaining):
                return
            try:
                self._sample()
            except Exception as exc:  # sampler failures must become artifact state
                with self.lock:
                    self.thread_error = f"{type(exc).__name__}:{exc}"
                return
            now = time.monotonic()
            missed = max(1, int((now - next_deadline) // self.interval) + 1)
            next_deadline += missed * self.interval

    def _sample(self) -> None:
        sample_started = time.monotonic()
        with self.lock:
            root_pid = self.process_root_pid
        curr_cpu = read_proc_stat()
        curr_disk = disk_snapshot()
        curr_threads = process_thread_snapshot(root_pid)
        current_processes = process_representatives(curr_threads)
        mem = system_memory_snapshot()
        cgroup = cgroup_memory_snapshot()
        load = load_snapshot()

        with self.lock:
            if self.prev_cpu is not None and "cpu" in self.prev_cpu and "cpu" in curr_cpu:
                busy, iowait, steal = cpu_percentages(self.prev_cpu["cpu"], curr_cpu["cpu"])
                self.cpu_samples += 1
                self.cpu_busy_sum += busy
                self.cpu_busy_max = max(self.cpu_busy_max, busy)
                self.cpu_iowait_sum += iowait
                self.cpu_iowait_max = max(self.cpu_iowait_max, iowait)
                self.cpu_steal_sum += steal
                self.cpu_steal_max = max(self.cpu_steal_max, steal)
                any_core_busy = 0.0
                for name, prev in self.prev_cpu.items():
                    if name == "cpu" or name not in curr_cpu:
                        continue
                    core_busy, _, _ = cpu_percentages(prev, curr_cpu[name])
                    any_core_busy = max(any_core_busy, core_busy)
                self.cpu_any_core_busy_max = max(self.cpu_any_core_busy_max, any_core_busy)
            self.prev_cpu = curr_cpu
            self._sample_disk_locked(curr_disk, sample_started)
            self._sample_process_cpu_locked(curr_threads, current_processes, sample_started)
            self._update_non_cpu_locked(mem, cgroup, load)
            self._drain_detailed_results_locked()
            monotonic_seconds = max(0.0, sample_started - self.start_monotonic)
            self.samples += 1
            self.sample_timestamps.append(monotonic_seconds)
            row = self._sample_row_locked(current_processes, mem, cgroup, monotonic_seconds)

        if self.sample_path is not None:
            try:
                with self.sample_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            except OSError:
                pass

    def _update_non_cpu_locked(
        self,
        mem: dict[str, int],
        cgroup: dict[str, int | str],
        load: dict[str, float | int],
    ) -> None:
        memtotal = mem.get("meminfo_memtotal_kb")
        if memtotal is not None:
            self.memtotal_kb = memtotal
        memavailable = mem.get("meminfo_memavailable_kb")
        if memavailable is not None:
            if self.memavailable_min is None or memavailable < self.memavailable_min:
                self.memavailable_min = memavailable
        memfree = mem.get("meminfo_memfree_kb")
        if memfree is not None:
            if self.memfree_min is None or memfree < self.memfree_min:
                self.memfree_min = memfree
        swaptotal = mem.get("meminfo_swaptotal_kb")
        if swaptotal is not None:
            self.swaptotal_kb = swaptotal
        swapfree = mem.get("meminfo_swapfree_kb")
        if swapfree is not None:
            if self.swapfree_min is None or swapfree < self.swapfree_min:
                self.swapfree_min = swapfree
        current = cgroup.get("cgroup_memory_current")
        if isinstance(current, int):
            if self.cgroup_memory_current_max is None or current > self.cgroup_memory_current_max:
                self.cgroup_memory_current_max = current
        peak = cgroup.get("cgroup_memory_peak")
        if isinstance(peak, int):
            if self.cgroup_memory_peak_max is None or peak > self.cgroup_memory_peak_max:
                self.cgroup_memory_peak_max = peak
        swap_current = cgroup.get("cgroup_memory_swap_current")
        if isinstance(swap_current, int):
            if (
                self.cgroup_memory_swap_current_max is None
                or swap_current > self.cgroup_memory_swap_current_max
            ):
                self.cgroup_memory_swap_current_max = swap_current
        swap_peak = cgroup.get("cgroup_memory_swap_peak")
        if isinstance(swap_peak, int):
            if self.cgroup_memory_swap_peak_max is None or swap_peak > self.cgroup_memory_swap_peak_max:
                self.cgroup_memory_swap_peak_max = swap_peak
        path = cgroup.get("cgroup_memory_path")
        if isinstance(path, str):
            self.cgroup_memory_path = path
        if "cgroup_memory_max" in cgroup:
            self.cgroup_memory_max = cgroup["cgroup_memory_max"]
        if "cgroup_memory_swap_max" in cgroup:
            self.cgroup_memory_swap_max = cgroup["cgroup_memory_swap_max"]
        load1 = load.get("load1")
        if isinstance(load1, float):
            if self.load1_max is None or load1 > self.load1_max:
                self.load1_max = load1
        running = load.get("procs_running")
        if isinstance(running, int):
            if self.procs_running_max is None or running > self.procs_running_max:
                self.procs_running_max = running
        blocked = load.get("procs_blocked")
        if isinstance(blocked, int):
            if self.procs_blocked_max is None or blocked > self.procs_blocked_max:
                self.procs_blocked_max = blocked

    def _sample_disk_locked(self, curr_disk: dict[str, int], now: float) -> None:
        elapsed = now - self.prev_disk_monotonic
        interval_metrics = disk_sample_metrics(self.prev_disk, curr_disk, elapsed)
        if interval_metrics:
            self.disk_samples += 1
            for key, value in interval_metrics.items():
                self.disk_metric_sums[key] = self.disk_metric_sums.get(key, 0.0) + value
                self.disk_metric_max[key] = max(self.disk_metric_max.get(key, 0.0), value)
        self.prev_disk = curr_disk
        self.prev_disk_monotonic = now

    def _sample_process_cpu_locked(
        self,
        curr: dict[tuple[int, int], ThreadCpuSample],
        current_processes: dict[int, ThreadCpuSample],
        now: float,
    ) -> None:
        if self.process_root_pid is None:
            return
        elapsed = (
            now - self.prev_process_sample_monotonic
            if self.prev_process_sample_monotonic is not None
            else self.interval
        )
        process_deltas: dict[int, tuple[int, ThreadCpuSample]] = {}
        if self.prev_thread_cpu is not None and elapsed > 0:
            for key, curr_sample in curr.items():
                prev_sample = self.prev_thread_cpu.get(key)
                if prev_sample is None:
                    continue
                ticks = curr_sample.cpu_ticks - prev_sample.cpu_ticks
                if ticks <= 0:
                    continue
                percent = (
                    100.0
                    * ticks
                    / max(1, os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
                    / elapsed
                )
                previous_thread = self.hot_threads.get(key)
                if previous_thread is None or percent > previous_thread[0]:
                    self.hot_threads[key] = (percent, curr_sample)
                process_ticks, _ = process_deltas.get(curr_sample.pid, (0, curr_sample))
                process_deltas[curr_sample.pid] = (process_ticks + ticks, curr_sample)
            for pid, (ticks, sample) in process_deltas.items():
                percent = (
                    100.0
                    * ticks
                    / max(1, os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
                    / elapsed
                )
                previous_process = self.hot_processes.get(pid)
                if previous_process is None or percent > previous_process[0]:
                    self.hot_processes[pid] = (percent, sample)
        self._update_tracked_processes(current_processes, process_deltas, elapsed, now)
        self.prev_thread_cpu = curr
        self.prev_process_sample_monotonic = now

    def _update_tracked_processes(
        self,
        current_processes: dict[int, ThreadCpuSample],
        process_deltas: dict[int, tuple[int, ThreadCpuSample]],
        elapsed: float,
        now: float,
    ) -> None:
        assert self.process_root_pid is not None
        matches_by_label: dict[str, list[ThreadCpuSample]] = {}
        for label, tracked in self.tracked_processes.items():
            matches = tracked_process_matches(label, self.process_root_pid, current_processes)
            matches_by_label[label] = matches
            if len(matches) > tracked.match_count:
                tracked.match_count = len(matches)
            if not matches:
                if tracked.status == "unseen":
                    tracked.status = "missing"
                continue
            match_pids = [sample.pid for sample in matches]
            tracked.pids = ",".join(str(pid) for pid in match_pids)
            if len(matches) > 1:
                tracked.status = "grouped"
                tracked.pid = None
                tracked.comm = ""
                tracked.cmd = ""
                tracked.exe = ""
                tracked.start_time_ticks = None
                continue
            match = matches[0]
            tracked.status = "ok"
            tracked.pid = match.pid
            tracked.comm = match.comm
            tracked.exe = match.exe
            tracked.start_time_ticks = match.start_time_ticks

        for label, tracked in self.tracked_processes.items():
            matches = matches_by_label[label]
            if not matches:
                continue
            match_pids = [sample.pid for sample in matches]
            rss_kb = sum(sample.rss_kb for sample in matches)
            tracked.rss_max_kb = max(tracked.rss_max_kb, rss_kb)
            memory_totals: dict[str, int] = {}
            for match in matches:
                for key, value in match.memory_kb.items():
                    memory_totals[key] = memory_totals.get(key, 0) + value
            for key, value in memory_totals.items():
                tracked.memory_max_kb[key] = max(tracked.memory_max_kb.get(key, 0), value)
            total_ticks = sum(process_deltas.get(pid, (0, matches[0]))[0] for pid in match_pids)
            if total_ticks > 0 and elapsed > 0:
                percent = (
                    100.0
                    * total_ticks
                    / max(1, os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
                    / elapsed
                )
                tracked.cpu_max_percent = max(tracked.cpu_max_percent, percent)

        cache_matches = matches_by_label.get("cache_process", [])
        if len(cache_matches) == 1:
            match = cache_matches[0]
            identity = DetailedProcessRequest(match.pid, match.start_time_ticks)
            if identity != self.detailed_identity:
                self.detailed_identity = identity
                self.next_detailed_memory_monotonic = 0.0
            if now >= self.next_detailed_memory_monotonic:
                self.detailed_collector.submit(identity)
                self.next_detailed_memory_monotonic = now + self.detailed_memory_interval

    def _drain_detailed_results_locked(self) -> None:
        while True:
            try:
                result = self.detailed_collector.results.get_nowait()
            except queue.Empty:
                return
            if result.get("status") != "ok":
                continue
            memory = result.get("memory_kb")
            mapping = result.get("mapping")
            pid = result.get("pid")
            start_time_ticks = result.get("expected_start_time_ticks")
            if not isinstance(pid, int) or not isinstance(start_time_ticks, int):
                continue
            tracked = self.tracked_processes["cache_process"]
            if isinstance(memory, dict):
                for key, value in memory.items():
                    if isinstance(key, str) and isinstance(value, int):
                        tracked.memory_max_kb[key] = max(tracked.memory_max_kb.get(key, 0), value)
            if isinstance(mapping, dict):
                for key, value in mapping.items():
                    if isinstance(key, str) and isinstance(value, (int, str)):
                        tracked.detail_values[key] = value
            cmdline = result.get("cmdline")
            if (
                isinstance(cmdline, str)
                and tracked.pid == pid
                and tracked.start_time_ticks == start_time_ticks
            ):
                tracked.cmd = cmdline
            completed = result.get("completed_monotonic")
            if isinstance(completed, float):
                self.detailed_last_success_monotonic = completed
            self.detailed_last_success_pid = pid
            self.detailed_last_success_start_time_ticks = start_time_ticks

    def _sample_row_locked(
        self,
        current: dict[int, ThreadCpuSample],
        mem: dict[str, int],
        cgroup: dict[str, int | str],
        monotonic_seconds: float,
    ) -> dict[str, int | float | str]:
        row: dict[str, int | float | str] = {
            "monotonic_seconds": monotonic_seconds,
            "sampler_interval_seconds": self.interval,
        }
        for key, value in mem.items():
            row[f"system_{key}"] = value
        row.update(cgroup)
        if self.process_root_pid is None:
            return row
        for label in TRACKED_PROCESS_LABELS:
            matches = tracked_process_matches(label, self.process_root_pid, current)
            prefix = f"tracked_{label}"
            row[f"{prefix}_match_count"] = len(matches)
            if not matches:
                continue
            row[f"{prefix}_pids"] = ",".join(str(sample.pid) for sample in matches)
            row[f"{prefix}_rss_kb"] = sum(sample.rss_kb for sample in matches)
            if len(matches) == 1:
                row[f"{prefix}_comm"] = matches[0].comm
                row[f"{prefix}_exe"] = matches[0].exe
                row[f"{prefix}_start_time_ticks"] = matches[0].start_time_ticks
            memory_totals: dict[str, int] = {}
            for match in matches:
                for key, value in match.memory_kb.items():
                    memory_totals[key] = memory_totals.get(key, 0) + value
            for key, value in memory_totals.items():
                row[f"{prefix}_{key}"] = value
            tracked = self.tracked_processes[label]
            if (
                label == "cache_process"
                and len(matches) == 1
                and tracked.pid == matches[0].pid
                and tracked.start_time_ticks == matches[0].start_time_ticks
            ):
                for key, value in tracked.detail_values.items():
                    row[f"{prefix}_{key}"] = value
        return row

    def _cadence_metrics_locked(self) -> dict[str, float | int | str]:
        end = self.stop_monotonic if self.stop_monotonic is not None else time.monotonic()
        duration = max(0.0, end - self.start_monotonic)
        expected = duration / self.interval if self.interval > 0 else 0.0
        ratio = self.samples / expected if expected > 0 else 1.0
        if self.sample_timestamps:
            gaps = [self.sample_timestamps[0]]
            gaps.extend(
                current - previous
                for previous, current in zip(self.sample_timestamps, self.sample_timestamps[1:])
            )
            gaps.append(max(0.0, duration - self.sample_timestamps[-1]))
            longest_gap = max(gaps)
        else:
            longest_gap = duration
        max_gap_threshold = max(
            self.CADENCE_MAX_GAP_INTERVALS * self.interval,
            self.CADENCE_MAX_GAP_FLOOR_SECONDS,
        )
        under_sampled = int(
            self.thread_stalled
            or bool(self.thread_error)
            or (expected >= 1.0 and ratio < self.CADENCE_MIN_RATIO)
            or (duration >= self.interval and longest_gap > max_gap_threshold)
        )
        if self.thread_stalled:
            status = "stalled"
        elif self.thread_error:
            status = "error"
        elif under_sampled:
            status = "under_sampled"
        elif self.stop_monotonic is None:
            status = "active"
        else:
            status = "ok"
        metrics: dict[str, float | int | str] = {
            "system_sampler_active_seconds": duration,
            "system_sampler_expected_samples": expected,
            "system_sampler_samples": self.samples,
            "system_sampler_cadence_ratio": ratio,
            "system_sampler_longest_gap_seconds": longest_gap,
            "system_sampler_min_cadence_ratio": self.CADENCE_MIN_RATIO,
            "system_sampler_max_gap_seconds": max_gap_threshold,
            "system_sampler_under_sampled": under_sampled,
            "system_sampler_thread_stalled": int(self.thread_stalled),
            "system_sampler_status": status,
        }
        if self.thread_error:
            metrics["system_sampler_error"] = metric_safe_text(self.thread_error)
        return metrics

    def metrics(self) -> dict[str, float | int | str]:
        with self.lock:
            self._drain_detailed_results_locked()
            cadence = self._cadence_metrics_locked()
            detail_metrics = self.detailed_collector.snapshot_metrics()
            cache = self.tracked_processes["cache_process"]
            end = self.stop_monotonic if self.stop_monotonic is not None else time.monotonic()
            detailed_last_success_age = (
                max(0.0, end - self.detailed_last_success_monotonic)
                if self.detailed_last_success_monotonic is not None
                else None
            )
            memory_valid = int(
                cadence["system_sampler_under_sampled"] == 0
                and cache.status == "ok"
                and detail_metrics["successes"] > 0
                and detail_metrics["abandoned_helpers"] == 0
                and detail_metrics["active_helpers"] == 0
                and self.detailed_last_success_pid is not None
                and self.detailed_last_success_start_time_ticks is not None
                and detailed_last_success_age is not None
            )
            metrics: dict[str, float | int | str] = {
                "system_sampler_interval_seconds": self.interval,
                "system_cpu_count": self.cpu_count,
                "system_detailed_memory_interval_seconds": self.detailed_memory_interval,
                "system_detailed_memory_timeout_seconds": self.detailed_memory_timeout,
                "system_memory_valid": memory_valid,
            }
            metrics.update(cadence)
            for key, value in detail_metrics.items():
                metrics[f"system_detailed_memory_{key}"] = value
            if memory_valid:
                metrics["system_memory_validity_reason"] = "ok"
            elif cadence["system_sampler_under_sampled"] != 0:
                metrics["system_memory_validity_reason"] = "sampler_under_sampled"
            elif cache.status != "ok":
                metrics["system_memory_validity_reason"] = "cache_process_provenance_missing"
            elif detail_metrics["abandoned_helpers"] > 0 or detail_metrics["active_helpers"] > 0:
                metrics["system_memory_validity_reason"] = "detailed_memory_helper_not_reaped"
            else:
                metrics["system_memory_validity_reason"] = "detailed_memory_missing"
            if self.detailed_last_success_pid is not None:
                metrics["system_detailed_memory_last_success_pid"] = self.detailed_last_success_pid
            if self.detailed_last_success_start_time_ticks is not None:
                metrics["system_detailed_memory_last_success_start_time_ticks"] = (
                    self.detailed_last_success_start_time_ticks
                )
            if detailed_last_success_age is not None:
                metrics["system_detailed_memory_last_success_age_seconds"] = (
                    detailed_last_success_age
                )
            if self.sample_path is not None:
                metrics["system_sampler_timeseries_path"] = str(self.sample_path)
            if self.cpu_samples > 0:
                metrics["system_cpu_busy_avg_percent"] = self.cpu_busy_sum / self.cpu_samples
                metrics["system_cpu_busy_max_percent"] = self.cpu_busy_max
                metrics["system_cpu_any_core_busy_max_percent"] = self.cpu_any_core_busy_max
                metrics["system_cpu_iowait_avg_percent"] = self.cpu_iowait_sum / self.cpu_samples
                metrics["system_cpu_iowait_max_percent"] = self.cpu_iowait_max
                metrics["system_cpu_steal_avg_percent"] = self.cpu_steal_sum / self.cpu_samples
                metrics["system_cpu_steal_max_percent"] = self.cpu_steal_max
            if self.memavailable_min is not None:
                metrics["system_memavailable_min_kb"] = self.memavailable_min
            if self.memtotal_kb is not None:
                metrics["system_memtotal_kb"] = self.memtotal_kb
            if self.memtotal_kb and self.memavailable_min is not None:
                metrics["system_memavailable_min_percent"] = (
                    100.0 * self.memavailable_min / self.memtotal_kb
                )
            if self.memavailable_before is not None and self.memavailable_min is not None:
                metrics["system_memavailable_drop_max_kb"] = max(
                    0, self.memavailable_before - self.memavailable_min
                )
                if self.memtotal_kb:
                    metrics["system_memavailable_drop_max_percent"] = (
                        100.0
                        * max(0, self.memavailable_before - self.memavailable_min)
                        / self.memtotal_kb
                    )
            if self.memfree_min is not None:
                metrics["system_memfree_min_kb"] = self.memfree_min
            if self.swaptotal_kb is not None:
                metrics["system_swaptotal_kb"] = self.swaptotal_kb
            if self.swapfree_min is not None:
                metrics["system_swapfree_min_kb"] = self.swapfree_min
            if self.swaptotal_kb and self.swapfree_min is not None:
                metrics["system_swap_used_max_kb"] = self.swaptotal_kb - self.swapfree_min
                metrics["system_swap_used_max_percent"] = (
                    100.0 * (self.swaptotal_kb - self.swapfree_min) / self.swaptotal_kb
                )
            if self.cgroup_memory_current_max is not None:
                metrics["system_cgroup_memory_current_max_bytes"] = self.cgroup_memory_current_max
            if self.cgroup_memory_peak_max is not None:
                metrics["system_cgroup_memory_peak_max_bytes"] = self.cgroup_memory_peak_max
            if self.cgroup_memory_swap_current_max is not None:
                metrics["system_cgroup_memory_swap_current_max_bytes"] = (
                    self.cgroup_memory_swap_current_max
                )
            if self.cgroup_memory_swap_peak_max is not None:
                metrics["system_cgroup_memory_swap_peak_max_bytes"] = self.cgroup_memory_swap_peak_max
            if self.cgroup_memory_path is not None:
                metrics["system_cgroup_memory_path"] = self.cgroup_memory_path
            if self.cgroup_memory_max is not None:
                metrics["system_cgroup_memory_max_bytes"] = self.cgroup_memory_max
            if self.cgroup_memory_swap_max is not None:
                metrics["system_cgroup_memory_swap_max_bytes"] = self.cgroup_memory_swap_max
            if self.load1_max is not None:
                metrics["system_load1_max"] = self.load1_max
                metrics["system_load1_per_cpu_max"] = self.load1_max / max(1, self.cpu_count)
            if self.procs_running_max is not None:
                metrics["system_procs_running_max"] = self.procs_running_max
            if self.procs_blocked_max is not None:
                metrics["system_procs_blocked_max"] = self.procs_blocked_max
            metrics.update(delta("system_disk", self.disk_before, self.disk_after))
            metrics["system_disk_sampler_samples"] = self.disk_samples
            for key, value in sorted(self.disk_metric_max.items()):
                metrics[f"system_disk_{key}_max"] = value
            if self.disk_samples > 0:
                for key, value in sorted(self.disk_metric_sums.items()):
                    metrics[f"system_disk_{key}_avg"] = value / self.disk_samples
            hot_threads = sorted(self.hot_threads.values(), key=lambda row: row[0], reverse=True)[:5]
            for index, (percent, sample) in enumerate(hot_threads, start=1):
                metrics[f"system_hot_thread_{index}_cpu_percent"] = percent
                metrics[f"system_hot_thread_{index}_pid"] = sample.pid
                metrics[f"system_hot_thread_{index}_tid"] = sample.tid
                metrics[f"system_hot_thread_{index}_comm"] = metric_safe_text(sample.comm, 80)
                metrics[f"system_hot_thread_{index}_cmd"] = metric_safe_text(sample.exe)
                metrics[f"system_hot_thread_{index}_exe"] = metric_safe_text(sample.exe)
            hot_processes = sorted(self.hot_processes.values(), key=lambda row: row[0], reverse=True)[:5]
            for index, (percent, sample) in enumerate(hot_processes, start=1):
                metrics[f"system_hot_process_{index}_cpu_percent"] = percent
                metrics[f"system_hot_process_{index}_pid"] = sample.pid
                metrics[f"system_hot_process_{index}_comm"] = metric_safe_text(sample.comm, 80)
                metrics[f"system_hot_process_{index}_cmd"] = metric_safe_text(sample.exe)
                metrics[f"system_hot_process_{index}_exe"] = metric_safe_text(sample.exe)
            for label, tracked in self.tracked_processes.items():
                prefix = f"system_tracked_{label}"
                metrics[f"{prefix}_source"] = tracked.source
                metrics[f"{prefix}_status"] = tracked.status
                metrics[f"{prefix}_match_count"] = tracked.match_count
                if tracked.pid is not None:
                    metrics[f"{prefix}_pid"] = tracked.pid
                if tracked.pids:
                    metrics[f"{prefix}_pids"] = tracked.pids
                if tracked.comm:
                    metrics[f"{prefix}_comm"] = metric_safe_text(tracked.comm, 80)
                if tracked.cmd:
                    metrics[f"{prefix}_cmd"] = metric_safe_text(tracked.cmd)
                if tracked.exe:
                    metrics[f"{prefix}_exe"] = metric_safe_text(tracked.exe)
                if tracked.start_time_ticks is not None:
                    metrics[f"{prefix}_start_time_ticks"] = tracked.start_time_ticks
                if tracked.cpu_max_percent > 0:
                    metrics[f"{prefix}_cpu_max_percent"] = tracked.cpu_max_percent
                if tracked.rss_max_kb > 0:
                    metrics[f"{prefix}_rss_max_kb"] = tracked.rss_max_kb
                for key, value in sorted(tracked.memory_max_kb.items()):
                    metrics[f"{prefix}_{key}_max"] = value
                for key, value in sorted(tracked.detail_values.items()):
                    metrics[f"{prefix}_{key}"] = value
            return metrics


class PerfCounterError(Exception):
    pass


class PerfEventAttr(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("config", ctypes.c_uint64),
        ("sample_period", ctypes.c_uint64),
        ("sample_type", ctypes.c_uint64),
        ("read_format", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("wakeup_events", ctypes.c_uint32),
        ("bp_type", ctypes.c_uint32),
        ("bp_addr", ctypes.c_uint64),
        ("bp_len", ctypes.c_uint64),
        ("branch_sample_type", ctypes.c_uint64),
        ("sample_regs_user", ctypes.c_uint64),
        ("sample_stack_user", ctypes.c_uint32),
        ("clockid", ctypes.c_int32),
        ("sample_regs_intr", ctypes.c_uint64),
        ("aux_watermark", ctypes.c_uint32),
        ("sample_max_stack", ctypes.c_uint16),
        ("__reserved_2", ctypes.c_uint16),
        ("aux_sample_size", ctypes.c_uint32),
        ("__reserved_3", ctypes.c_uint32),
        ("sig_data", ctypes.c_uint64),
    ]


class PerfCounters:
    PERF_TYPE_HARDWARE = 0
    PERF_TYPE_SOFTWARE = 1
    PERF_COUNT_HW_CPU_CYCLES = 0
    PERF_COUNT_HW_INSTRUCTIONS = 1
    PERF_COUNT_HW_CACHE_REFERENCES = 2
    PERF_COUNT_HW_CACHE_MISSES = 3
    PERF_COUNT_HW_BRANCH_INSTRUCTIONS = 4
    PERF_COUNT_HW_BRANCH_MISSES = 5
    PERF_COUNT_HW_BUS_CYCLES = 6
    PERF_COUNT_HW_STALLED_CYCLES_FRONTEND = 7
    PERF_COUNT_HW_STALLED_CYCLES_BACKEND = 8
    PERF_COUNT_HW_REF_CPU_CYCLES = 9
    PERF_COUNT_SW_PAGE_FAULTS = 2
    PERF_COUNT_SW_CONTEXT_SWITCHES = 3
    PERF_COUNT_SW_CPU_MIGRATIONS = 4
    PERF_EVENT_IOC_ENABLE = 0x2400
    PERF_EVENT_IOC_DISABLE = 0x2401
    PERF_EVENT_IOC_RESET = 0x2403

    # PERF_ATTR_FLAG_DISABLED | PERF_ATTR_FLAG_INHERIT
    PERF_ATTR_FLAGS = (1 << 0) | (1 << 1)

    def __init__(self) -> None:
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.syscall_no = self._syscall_number()
        self.fds: dict[str, int] = {}
        self.unavailable: dict[str, str] = {}
        counters = {
            "hardware_cycles": (self.PERF_TYPE_HARDWARE, self.PERF_COUNT_HW_CPU_CYCLES),
            "hardware_instructions": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_INSTRUCTIONS,
            ),
            "hardware_cache_references": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_CACHE_REFERENCES,
            ),
            "hardware_cache_misses": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_CACHE_MISSES,
            ),
            "hardware_branch_instructions": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_BRANCH_INSTRUCTIONS,
            ),
            "hardware_branch_misses": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_BRANCH_MISSES,
            ),
            "hardware_bus_cycles": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_BUS_CYCLES,
            ),
            "hardware_stalled_cycles_frontend": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_STALLED_CYCLES_FRONTEND,
            ),
            "hardware_stalled_cycles_backend": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_STALLED_CYCLES_BACKEND,
            ),
            "hardware_ref_cpu_cycles": (
                self.PERF_TYPE_HARDWARE,
                self.PERF_COUNT_HW_REF_CPU_CYCLES,
            ),
            "software_page_faults": (
                self.PERF_TYPE_SOFTWARE,
                self.PERF_COUNT_SW_PAGE_FAULTS,
            ),
            "software_context_switches": (
                self.PERF_TYPE_SOFTWARE,
                self.PERF_COUNT_SW_CONTEXT_SWITCHES,
            ),
            "software_cpu_migrations": (
                self.PERF_TYPE_SOFTWARE,
                self.PERF_COUNT_SW_CPU_MIGRATIONS,
            ),
        }
        for name, (perf_type, config) in counters.items():
            try:
                self.fds[name] = self._open(perf_type, config)
            except PerfCounterError as exc:
                self.unavailable[name] = str(exc)
        if not self.fds:
            raise PerfCounterError("no_perf_counters_available")

    @staticmethod
    def _syscall_number() -> int:
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            return 298
        if machine in {"aarch64", "arm64"}:
            return 241
        if machine.startswith("arm"):
            return 364
        raise PerfCounterError(f"unsupported_architecture:{machine}")

    def _open(self, perf_type: int, config: int) -> int:
        attr = PerfEventAttr()
        attr.type = perf_type
        attr.size = ctypes.sizeof(PerfEventAttr)
        attr.config = config
        attr.flags = self.PERF_ATTR_FLAGS
        fd = self.libc.syscall(
            self.syscall_no,
            ctypes.byref(attr),
            0,      # current process
            -1,     # any CPU
            -1,     # no group
            0,
        )
        if fd < 0:
            errno = ctypes.get_errno()
            raise PerfCounterError(f"{errno}:{os.strerror(errno)}")
        return fd

    def _ioctl(self, request: int) -> None:
        for fd in self.fds.values():
            if self.libc.ioctl(fd, request, 0) < 0:
                errno = ctypes.get_errno()
                raise PerfCounterError(f"{errno}:{os.strerror(errno)}")

    def reset(self) -> None:
        self._ioctl(self.PERF_EVENT_IOC_RESET)

    def enable(self) -> None:
        self._ioctl(self.PERF_EVENT_IOC_ENABLE)

    def disable(self) -> None:
        self._ioctl(self.PERF_EVENT_IOC_DISABLE)

    def read(self) -> dict[str, int]:
        values = {}
        for name, fd in self.fds.items():
            data = os.read(fd, 8)
            if len(data) != 8:
                raise PerfCounterError(f"short_read:{name}")
            values[name] = struct.unpack("Q", data)[0]
        return values

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


def perf_event_paranoid() -> str:
    path = Path("/proc/sys/kernel/perf_event_paranoid")
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as exc:
        return f"unreadable:{exc.errno}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument(
        "--system-sample-interval",
        type=float,
        default=float(os.environ.get("BENCH_SYSTEM_SAMPLE_INTERVAL", "1.0")),
        help="Sample host /proc utilisation every N seconds; use 0 to disable",
    )
    parser.add_argument(
        "--detailed-memory-interval",
        type=float,
        default=float(os.environ.get("BENCH_DETAILED_MEMORY_INTERVAL", "1.0")),
        help="Collect timeout-isolated cache-process smaps/maps/cmdline every N seconds",
    )
    parser.add_argument(
        "--detailed-memory-timeout",
        type=float,
        default=float(os.environ.get("BENCH_DETAILED_MEMORY_TIMEOUT", "0.5")),
        help="Terminate a blocked detailed-memory helper after N seconds",
    )
    parser.add_argument(
        "--perf",
        choices=("auto", "off", "required"),
        default="auto",
        help="Collect inherited perf_event counters when available",
    )
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("missing command")
    if args.system_sample_interval < 0:
        raise SystemExit("--system-sample-interval must be non-negative")
    if args.detailed_memory_interval <= 0:
        raise SystemExit("--detailed-memory-interval must be positive")
    if args.detailed_memory_timeout <= 0:
        raise SystemExit("--detailed-memory-timeout must be positive")

    perf = None
    perf_error = None
    if args.perf != "off":
        try:
            perf = PerfCounters()
            perf.reset()
        except PerfCounterError as exc:
            perf_error = str(exc)
            if args.perf == "required":
                raise SystemExit(f"perf_event_open unavailable: {perf_error}")

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    memory_before = system_memory_snapshot()
    sampler = None
    if args.system_sample_interval > 0:
        sampler = SystemSampler(
            args.system_sample_interval,
            Path(str(args.metrics) + ".samples.jsonl"),
            args.detailed_memory_interval,
            args.detailed_memory_timeout,
        )
        sampler.start()
    t0 = time.monotonic()
    if perf is not None:
        perf.enable()
    proc = subprocess.Popen(cmd)
    if sampler is not None:
        sampler.set_process_root(proc.pid)
    returncode = proc.wait()
    if perf is not None:
        perf.disable()
    if sampler is not None:
        sampler.stop()
    wall = time.monotonic() - t0
    memory_after = system_memory_snapshot()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)

    metrics = usage_delta(before, after)
    metrics.update(system_memory_delta(memory_before, memory_after))
    if sampler is not None:
        metrics.update(sampler.metrics())
    else:
        metrics["system_sampler_samples"] = 0
        metrics["system_sampler_status"] = "disabled"
        metrics["system_memory_valid"] = 0
        metrics["system_memory_validity_reason"] = "sampler_disabled"
    metrics["wall_seconds"] = wall
    metrics["exit_code"] = returncode
    metrics["perf_event_paranoid"] = perf_event_paranoid()
    if perf is not None:
        metrics["perf_event_status"] = "available"
        metrics.update(perf.read())
        if perf.unavailable:
            metrics["perf_event_unavailable_count"] = len(perf.unavailable)
            metrics["perf_event_unavailable"] = ",".join(sorted(perf.unavailable))
        else:
            metrics["perf_event_unavailable_count"] = 0
        perf.close()
    elif args.perf == "off":
        metrics["perf_event_status"] = "off"
    else:
        metrics["perf_event_status"] = "unavailable"
        metrics["perf_event_error"] = perf_error or "unknown"

    with args.metrics.open("w", encoding="ascii") as f:
        f.write("command=" + " ".join(cmd) + "\n")
        for key in sorted(metrics):
            value = metrics[key]
            if isinstance(value, float):
                f.write(f"{key}={value:.9f}\n")
            else:
                f.write(f"{key}={value}\n")

    return returncode


if __name__ == "__main__":
    sys.exit(main())
