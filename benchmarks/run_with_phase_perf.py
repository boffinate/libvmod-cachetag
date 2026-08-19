#!/usr/bin/env python3
"""Run a command and record perf only between driver phase markers."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def parse_proc_stat(raw: str) -> tuple[int, str, int] | None:
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
    if len(parts) < 2:
        return None
    try:
        ppid = int(parts[1])
    except ValueError:
        return None
    return pid, comm, ppid


def read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def process_snapshot() -> tuple[dict[int, int], dict[int, str], dict[int, str]]:
    parents: dict[int, int] = {}
    comms: dict[int, str] = {}
    cmds: dict[int, str] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return parents, comms, cmds
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parsed = parse_proc_stat((entry / "stat").read_text(encoding="ascii"))
        except OSError:
            continue
        if parsed is None:
            continue
        pid, comm, ppid = parsed
        parents[pid] = ppid
        comms[pid] = comm
        cmds[pid] = read_cmdline(pid)
    return parents, comms, cmds


def descendant_pids(root_pid: int) -> set[int]:
    parents, _, _ = process_snapshot()
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if pid not in descendants and ppid in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def target_pids(root_pid: int, target: str) -> list[int]:
    descendants = descendant_pids(root_pid)
    _, comms, cmds = process_snapshot()
    if target == "descendants":
        return sorted(descendants)
    if target == "vinyld":
        result = []
        for pid in descendants:
            comm = comms.get(pid, "")
            cmd = cmds.get(pid, "")
            if comm == "vinyld" or "vinyld" in cmd:
                result.append(pid)
        return sorted(result)
    raise ValueError(f"unknown target: {target}")


def phase_marker_pairs(marker_dir: Path, marker_prefix: str, phase: str) -> list[tuple[Path, Path]]:
    prefixes = [marker_prefix]
    if phase == "warm":
        prefixes.append(f"{marker_prefix}_warm")
    return [
        (
            marker_dir / f"{prefix}.{phase}.start",
            marker_dir / f"{prefix}.{phase}.end",
        )
        for prefix in prefixes
    ]


def wait_for_any_marker(
    paths: list[Path], proc: subprocess.Popen[bytes], poll_seconds: float
) -> Path | None:
    while True:
        for path in paths:
            if path.exists():
                return path
        if proc.poll() is not None:
            return None
        time.sleep(poll_seconds)


def stop_perf(proc: subprocess.Popen[bytes]) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        return proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.terminate()
    try:
        return proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


def perf_record_command(
    perf_data: Path,
    freq: str,
    call_graph: str,
    scope: str,
    pids: list[int],
) -> list[str]:
    command = [
        "perf",
        "record",
        "-F",
        freq,
        "--call-graph",
        call_graph,
        "-o",
        str(perf_data),
    ]
    if scope == "system":
        command.append("-a")
    else:
        command.extend(["-p", ",".join(str(pid) for pid in pids)])
    command.extend(["--", "sleep", "86400"])
    return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--perf-data", required=True, type=Path)
    parser.add_argument("--marker-dir", required=True, type=Path)
    parser.add_argument("--marker-prefix", required=True)
    parser.add_argument("--phase", default="warm")
    parser.add_argument("--freq", default="99")
    parser.add_argument("--call-graph", choices=("fp", "dwarf"), default="fp")
    parser.add_argument("--scope", choices=("command", "system"), default="command")
    parser.add_argument("--target", choices=("vinyld", "descendants"), default="vinyld")
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    args.marker_dir.mkdir(parents=True, exist_ok=True)
    marker_pairs = phase_marker_pairs(args.marker_dir, args.marker_prefix, args.phase)
    for marker in (path for pair in marker_pairs for path in pair):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass

    env = os.environ.copy()
    env["BENCH_PHASE_MARKER_DIR"] = str(args.marker_dir)
    env["BENCH_PHASE_MARKER_PREFIX"] = args.marker_prefix

    child = subprocess.Popen(command, env=env)
    perf_proc: subprocess.Popen[bytes] | None = None
    perf_rc = 0
    missing_marker = False

    try:
        start_marker = wait_for_any_marker(
            [pair[0] for pair in marker_pairs], child, args.poll_seconds
        )
        if start_marker is None:
            missing_marker = True
        else:
            end_marker = next(end for start, end in marker_pairs if start == start_marker)
            pids: list[int] = []
            if args.scope != "system":
                pids = target_pids(child.pid, args.target)
                if not pids:
                    print(
                        f"no target pids found for BENCH_PERF_RECORD_PHASE={args.phase} target={args.target}",
                        file=sys.stderr,
                    )
                    missing_marker = True
            if not missing_marker:
                perf_cmd = perf_record_command(
                    args.perf_data,
                    args.freq,
                    args.call_graph,
                    args.scope,
                    pids,
                )
                print(
                    "phase-perf "
                    f"phase={args.phase} scope={args.scope} target={args.target} "
                    f"call_graph={args.call_graph} data={args.perf_data}",
                    file=sys.stderr,
                )
                perf_proc = subprocess.Popen(perf_cmd)
                wait_for_any_marker([end_marker], child, args.poll_seconds)
    finally:
        if perf_proc is not None:
            perf_rc = stop_perf(perf_proc)

    child_rc = child.wait()
    if child_rc != 0:
        return child_rc
    if missing_marker:
        print(
            "phase perf marker not observed: "
            + " ".join(f"start={start} end={end}" for start, end in marker_pairs),
            file=sys.stderr,
        )
        return 2
    if perf_rc != 0 and args.perf_data.exists() and args.perf_data.stat().st_size > 0:
        return 0
    return perf_rc


if __name__ == "__main__":
    raise SystemExit(main())
