#!/usr/bin/env python3
"""Run a command and attach perf only between driver phase markers.

`--mode record` samples call stacks with `perf record`; `--mode stat`
counts inherited hardware events with `perf stat -x ,` into a CSV.
Both attach to the same discovered target pids and are stopped with
SIGINT at the phase end marker.
"""

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
        # A split v1.1 workload runs the warm phase in its own driver
        # invocation; the ordinary phased-purge workloads run it inside the
        # load invocation, which names its markers `<workload>_load`.
        prefixes.append(f"{marker_prefix}_warm")
        prefixes.append(f"{marker_prefix}_load")
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


def perf_stat_command(
    stat_output: Path,
    events: str,
    scope: str,
    pids: list[int],
) -> list[str]:
    """Build a CSV `perf stat` attach for the phase window.

    No workload is appended: `perf stat` with a target and no command counts
    until it is interrupted, which is how the phase end marker stops it.  The
    counters are inherited exactly as `perf record -p` inherits them, so the
    same `perf_event_paranoid` setting admits both.
    """
    command = ["perf", "stat", "-e", events]
    if scope == "system":
        command.append("-a")
    else:
        command.extend(["-p", ",".join(str(pid) for pid in pids)])
    command.extend(["-x", ",", "-o", str(stat_output)])
    return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("record", "stat"), default="record")
    parser.add_argument("--perf-data", type=Path)
    parser.add_argument("--stat-output", type=Path)
    parser.add_argument("--stat-events", default="instructions,cycles")
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

    if args.mode == "record" and args.perf_data is None:
        parser.error("--perf-data is required for --mode record")
    if args.mode == "stat" and args.stat_output is None:
        parser.error("--stat-output is required for --mode stat")
    output_path = args.perf_data if args.mode == "record" else args.stat_output

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
                        f"no target pids found for mode={args.mode} phase={args.phase} target={args.target}",
                        file=sys.stderr,
                    )
                    missing_marker = True
            if not missing_marker:
                if args.mode == "stat":
                    perf_cmd = perf_stat_command(
                        args.stat_output,
                        args.stat_events,
                        args.scope,
                        pids,
                    )
                    print(
                        "phase-perf-stat "
                        f"phase={args.phase} scope={args.scope} target={args.target} "
                        f"events={args.stat_events} output={args.stat_output}",
                        file=sys.stderr,
                    )
                else:
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
                try:
                    perf_proc = subprocess.Popen(perf_cmd)
                except OSError as exc:
                    print(f"perf could not be started: {exc}", file=sys.stderr)
                    perf_proc = None
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
    if args.mode == "stat":
        # `perf stat` is an optional measurement, not a gate. Hardware counters
        # can be unavailable (blocked PMU, paranoid setting) and that must not
        # fail the benchmark row; the caller decides from the CSV whether the
        # row actually measured anything.
        if perf_rc != 0:
            print(
                f"phase perf stat exited rc={perf_rc}; "
                f"check {args.stat_output} for counted events",
                file=sys.stderr,
            )
        return 0
    if perf_rc != 0 and output_path.exists() and output_path.stat().st_size > 0:
        return 0
    return perf_rc


if __name__ == "__main__":
    raise SystemExit(main())
