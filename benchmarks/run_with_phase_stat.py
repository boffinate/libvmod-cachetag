#!/usr/bin/env python3
"""Run a command and count one acknowledged driver phase on cache-main."""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from run_with_phase_perf import descendant_pids


def proc_identity(pid: int) -> tuple[int, str, str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        start_time_ticks = int(fields[19])
    except ValueError:
        return None
    return start_time_ticks, comm, exe


def cache_process_identity(root_pid: int) -> tuple[int, int, str, str]:
    matches: list[tuple[int, int, str, str]] = []
    for pid in descendant_pids(root_pid):
        identity = proc_identity(pid)
        if identity is None:
            continue
        start_time_ticks, comm, exe = identity
        if comm == "cache-main" and Path(exe).name == "vinyld":
            matches.append((pid, start_time_ticks, comm, exe))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one cache-main/vinyld descendant, found {len(matches)}"
        )
    return matches[0]


def open_control_writer(path: Path, proc: subprocess.Popen[bytes], timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"perf stat exited before control attach rc={proc.returncode}")
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno != errno.ENXIO:
                raise
        time.sleep(0.01)
    raise TimeoutError(f"timed out opening perf control FIFO {path}")


def send_control_and_wait_ack(
    control_fd: int,
    ack_fd: int,
    command: str,
    proc: subprocess.Popen[bytes],
    timeout: float,
) -> None:
    os.write(control_fd, f"{command}\n".encode("ascii"))
    deadline = time.monotonic() + timeout
    buffered = b""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"perf stat exited waiting for {command} ack rc={proc.returncode}")
        readable, _, _ = select.select([ack_fd], [], [], min(0.05, deadline - time.monotonic()))
        if not readable:
            continue
        chunk = os.read(ack_fd, 4096)
        if not chunk:
            time.sleep(0.01)
            continue
        buffered += chunk
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            if line.strip() == b"ack":
                return
    raise TimeoutError(f"timed out waiting for perf {command} acknowledgement")


def wait_for_end(
    path: Path,
    child: subprocess.Popen[bytes],
    perf: subprocess.Popen[bytes],
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if child.poll() is not None:
            raise RuntimeError(f"child exited before phase end rc={child.returncode}")
        if perf.poll() is not None:
            raise RuntimeError(f"perf stat exited before phase end rc={perf.returncode}")
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for phase end marker {path}")


def wait_for_start(
    path: Path, child: subprocess.Popen[bytes], timeout: float, poll_seconds: float
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if child.poll() is not None:
            raise RuntimeError(f"child exited before phase start rc={child.returncode}")
        time.sleep(poll_seconds)
    raise TimeoutError(f"timed out waiting for phase start marker {path}")


def stop_process(proc: subprocess.Popen[bytes], timeout: float = 10.0) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
    try:
        return proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


def write_meta(path: Path, values: dict[str, object]) -> None:
    lines = []
    for key, value in values.items():
        rendered = str(value).replace("\n", " ")
        lines.append(f"{key}={rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def parse_stat_rows(path: Path, expected_events: list[str]) -> list[dict[str, object]]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError("empty perf stat output")
    rows: list[dict[str, object]] = []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        rows = [payload]
    elif isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    else:
        for line in raw.splitlines():
            rendered = line.strip().rstrip(",")
            if not rendered or rendered in ("[", "]"):
                continue
            row = json.loads(rendered)
            if not isinstance(row, dict):
                raise ValueError("perf stat row is not an object")
            rows.append(row)

    by_event: dict[str, dict[str, object]] = {}
    for row in rows:
        event = str(row.get("event", row.get("event-name", ""))).strip()
        if event in expected_events:
            if event in by_event:
                raise ValueError(f"duplicate perf stat event: {event}")
            by_event[event] = row
    missing = [event for event in expected_events if event not in by_event]
    if missing:
        raise ValueError(f"missing perf stat events: {','.join(missing)}")
    for event in expected_events:
        value = str(by_event[event].get("counter-value", "")).strip().replace(",", "")
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError(f"invalid counter value for {event}: {value}") from exc
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"invalid counter value for {event}: {value}")
    return [by_event[event] for event in expected_events]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat-output", required=True, type=Path)
    parser.add_argument("--meta-output", required=True, type=Path)
    parser.add_argument("--marker-dir", required=True, type=Path)
    parser.add_argument("--marker-prefix", required=True)
    parser.add_argument("--phase", default="load")
    parser.add_argument(
        "--events", default="task-clock,instructions,cycles,ref-cycles"
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--ack-timeout", type=float, default=10.0)
    parser.add_argument("--poll-seconds", type=float, default=0.01)
    parser.add_argument("--driver-metrics", required=True, type=Path)
    parser.add_argument("--expected-requests", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")
    events = [event.strip() for event in args.events.split(",") if event.strip()]
    if not events:
        parser.error("at least one perf event is required")

    args.marker_dir.mkdir(parents=True, exist_ok=True)
    args.stat_output.parent.mkdir(parents=True, exist_ok=True)
    args.meta_output.parent.mkdir(parents=True, exist_ok=True)
    marker_base = args.marker_dir / f"{args.marker_prefix}.{args.phase}"
    start_marker = Path(f"{marker_base}.start")
    ready_marker = Path(f"{marker_base}.ready")
    end_marker = Path(f"{marker_base}.end")
    error_marker = Path(f"{marker_base}.error")
    control_fifo = Path(f"{marker_base}.perf-stat-control")
    ack_fifo = Path(f"{marker_base}.perf-stat-ack")
    for path in (
        start_marker,
        ready_marker,
        end_marker,
        error_marker,
        control_fifo,
        ack_fifo,
        args.stat_output,
        args.meta_output,
        args.driver_metrics,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    os.mkfifo(control_fifo, 0o600)
    os.mkfifo(ack_fifo, 0o600)

    env = os.environ.copy()
    env["BENCH_PHASE_MARKER_DIR"] = str(args.marker_dir)
    env["BENCH_PHASE_MARKER_PREFIX"] = args.marker_prefix
    env["BENCH_PHASE_REQUIRE_READY"] = "1"
    child = subprocess.Popen(command, env=env)
    perf: subprocess.Popen[bytes] | None = None
    control_fd = -1
    ack_fd = -1
    meta: dict[str, object] = {
        "phase_stat_version": 1,
        "phase": args.phase,
        "events": ",".join(events),
        "child_pid": child.pid,
        "command": " ".join(command),
        "start_marker": start_marker,
        "ready_marker": ready_marker,
        "end_marker": end_marker,
    }
    failure: Exception | None = None

    try:
        wait_for_start(start_marker, child, args.timeout, args.poll_seconds)
        meta["start_observed_unix_nano"] = time.time_ns()
        target_pid, target_start, target_comm, target_exe = cache_process_identity(child.pid)
        meta.update(
            {
                "target_pid": target_pid,
                "target_start_time_ticks": target_start,
                "target_comm": target_comm,
                "target_exe": target_exe,
            }
        )
        ack_fd = os.open(ack_fifo, os.O_RDONLY | os.O_NONBLOCK)
        perf_cmd = [
            "perf",
            "stat",
            "--json-output",
            "--no-big-num",
            "-D",
            "-1",
            f"--control=fifo:{control_fifo},{ack_fifo}",
            "-o",
            str(args.stat_output),
        ]
        for event in events:
            perf_cmd.extend(["-e", event])
        perf_cmd.extend(["-p", str(target_pid)])
        meta["perf_command"] = " ".join(perf_cmd)
        version = subprocess.run(
            ["perf", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        meta["perf_version"] = (version.stdout or version.stderr).strip()
        perf = subprocess.Popen(perf_cmd)
        control_fd = open_control_writer(control_fifo, perf, args.ack_timeout)
        send_control_and_wait_ack(control_fd, ack_fd, "enable", perf, args.ack_timeout)
        meta["enable_ack_unix_nano"] = time.time_ns()
        if proc_identity(target_pid) != (target_start, target_comm, target_exe):
            raise RuntimeError("cache process identity changed during counter attach")
        meta["target_identity_revalidated_after_attach"] = 1
        ready_marker.write_text(
            "\n".join(
                [
                    f"time_unix_nano={time.time_ns()}",
                    f"target_pid={target_pid}",
                    f"target_start_time_ticks={target_start}",
                    "event=ready",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        wait_for_end(end_marker, child, perf, args.timeout)
        meta["end_observed_unix_nano"] = time.time_ns()
        current_identity = proc_identity(target_pid)
        if current_identity != (target_start, target_comm, target_exe):
            raise RuntimeError("cache process identity changed during measured phase")
        send_control_and_wait_ack(control_fd, ack_fd, "disable", perf, args.ack_timeout)
        meta["disable_ack_unix_nano"] = time.time_ns()
    except Exception as exc:  # failure is recorded before the child is stopped
        failure = exc
        meta["error"] = str(exc)
        try:
            error_marker.write_text(f"error={exc}\n", encoding="utf-8")
        except OSError:
            pass
    finally:
        if perf is not None:
            meta["perf_returncode"] = stop_process(perf)
        if control_fd >= 0:
            os.close(control_fd)
        if ack_fd >= 0:
            os.close(ack_fd)
        for fifo in (control_fifo, ack_fifo):
            try:
                fifo.unlink()
            except FileNotFoundError:
                pass

    if failure is None:
        perf_returncode = int(meta.get("perf_returncode", -1))
        if perf_returncode not in (0, -signal.SIGINT):
            failure = RuntimeError(f"perf stat exited after collection rc={perf_returncode}")
            meta["error"] = str(failure)
        else:
            if perf_returncode == -signal.SIGINT:
                meta["perf_stopped_by_controller_sigint"] = 1
            if proc_identity(target_pid) != (target_start, target_comm, target_exe):
                failure = RuntimeError("cache process identity changed after counter stop")
                meta["error"] = str(failure)
            else:
                meta["target_identity_revalidated_after_stop"] = 1

    if failure is not None:
        meta["valid"] = 0
        try:
            error_marker.write_text(f"error={failure}\n", encoding="utf-8")
        except OSError:
            pass

    if failure is not None:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        meta["child_returncode"] = child.returncode
        write_meta(args.meta_output, meta)
        print(f"phase perf stat failed: {failure}", file=sys.stderr)
        return 2

    child_rc = child.wait()
    meta["child_returncode"] = child_rc
    meta["stat_output_bytes"] = (
        args.stat_output.stat().st_size if args.stat_output.exists() else 0
    )
    try:
        stat_rows = parse_stat_rows(args.stat_output, events)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        meta["error"] = f"unparseable perf stat output: {exc}"
        meta["valid"] = 0
        write_meta(args.meta_output, meta)
        return 2
    meta["stat_rows"] = len(stat_rows)
    try:
        driver = read_key_values(args.driver_metrics)
        driver_errors = int(driver.get("driver_errors", "-1"))
        driver_requests = int(driver.get("driver_load_requests", "-1"))
        backend_objects = int(driver.get("driver_load_backend_objects", "-1"))
        backend_expected = int(driver.get("driver_load_backend_objects_expected", "-1"))
    except (OSError, ValueError) as exc:
        meta["error"] = f"invalid driver metrics: {exc}"
        meta["valid"] = 0
        write_meta(args.meta_output, meta)
        return 2
    meta.update(
        {
            "driver_metrics": args.driver_metrics,
            "driver_errors": driver_errors,
            "driver_load_requests": driver_requests,
            "driver_load_backend_objects": backend_objects,
            "driver_load_backend_objects_expected": backend_expected,
        }
    )
    if child_rc != 0:
        meta["error"] = f"child command failed rc={child_rc}"
        meta["valid"] = 0
        write_meta(args.meta_output, meta)
        return child_rc
    if (
        driver_errors != 0
        or driver_requests != args.expected_requests
        or backend_objects != args.expected_requests
        or backend_expected != args.expected_requests
    ):
        print(
            "phase perf stat driver evidence mismatch "
            f"errors={driver_errors} requests={driver_requests} "
            f"backend={backend_objects}/{backend_expected} "
            f"expected={args.expected_requests}",
            file=sys.stderr,
        )
        meta["error"] = "driver evidence mismatch"
        meta["valid"] = 0
        write_meta(args.meta_output, meta)
        return 2
    meta["valid"] = 1
    write_meta(args.meta_output, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
