#!/usr/bin/env python3
"""Count exact cache-main phase work through acknowledged FIFO controls."""

from __future__ import annotations

import argparse
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    comm: str
    start_time_ticks: int
    exe: str


def read_identity(pid: int) -> ProcessIdentity | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None
    left = raw.find("(")
    right = raw.rfind(")")
    if left < 0 or right <= left:
        return None
    fields = raw[right + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return ProcessIdentity(
            pid=int(raw[:left].strip()),
            ppid=int(fields[1]),
            comm=raw[left + 1 : right],
            start_time_ticks=int(fields[19]),
            exe=exe,
        )
    except ValueError:
        return None


def process_identities() -> dict[int, ProcessIdentity]:
    identities: dict[int, ProcessIdentity] = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            identity = read_identity(int(entry.name))
            if identity is not None:
                identities[identity.pid] = identity
    return identities


def descendants(root_pid: int, identities: dict[int, ProcessIdentity]) -> set[int]:
    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for identity in identities.values():
            if identity.pid not in result and identity.ppid in result:
                result.add(identity.pid)
                changed = True
    return result


def select_cache_main(root_pid: int, timeout: float) -> tuple[ProcessIdentity, list[int]]:
    deadline = time.monotonic() + timeout
    last: list[ProcessIdentity] = []
    while time.monotonic() < deadline:
        identities = process_identities()
        tree = descendants(root_pid, identities)
        last = [
            identity
            for pid, identity in identities.items()
            if pid in tree
            and identity.comm == "cache-main"
            and Path(identity.exe).name == "vinyld"
        ]
        if len(last) == 1:
            return last[0], lineage(last[0].pid, identities)
        time.sleep(0.01)
    rendered = ", ".join(f"{item.pid}:{item.comm}:{item.exe}" for item in last) or "none"
    raise RuntimeError(f"expected exactly one descendant cache-main/vinyld target; found {rendered}")


def lineage(pid: int, identities: dict[int, ProcessIdentity]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    while pid > 0 and pid not in seen:
        result.append(pid)
        seen.add(pid)
        identity = identities.get(pid)
        if identity is None:
            break
        pid = identity.ppid
    return result


def task_ids(pid: int) -> list[int]:
    try:
        return sorted(int(path.name) for path in Path(f"/proc/{pid}/task").iterdir())
    except OSError:
        return []


def perf_stat_command(events: str, task_ids: list[int], output: Path, control: Path, acknowledgement: Path) -> list[str]:
    if not task_ids or any(task_id <= 0 for task_id in task_ids):
        raise ValueError("perf stat requires a non-empty positive TID list")
    return [
        "perf", "stat", "--delay=-1", f"--control=fifo:{control},{acknowledgement}",
        "-e", events, "-t", ",".join(str(task_id) for task_id in task_ids),
        "-x", ",", "-o", str(output),
    ]


class LineReader:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buffer = b""

    def read(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for FIFO acknowledgement")
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                raise TimeoutError("timed out waiting for FIFO acknowledgement")
            chunk = os.read(self.fd, 4096)
            if chunk:
                self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line.decode("ascii", errors="strict").strip()


def write_line(fd: int, line: str) -> None:
    os.write(fd, (line + "\n").encode("ascii"))


def write_identity_artifact(
    path: Path,
    phase: str,
    identity: ProcessIdentity,
    lineage_pids: list[int],
    start_tasks: list[int],
    end_tasks: list[int],
) -> None:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    current = read_identity(identity.pid)
    identity_stable = int(current == identity)
    task_coverage_complete = int(
        identity_stable == 1 and bool(start_tasks) and start_tasks == end_tasks
    )
    path.write_text(
        "".join(
            (
                "identity_schema=cache-main-controlled-perf-v1\n",
                f"phase={phase}\n",
                f"selected_pid={identity.pid}\n",
                f"selected_comm={identity.comm}\n",
                f"selected_exe={identity.exe}\n",
                f"selected_start_time_ticks={identity.start_time_ticks}\n",
                f"boot_id={boot_id}\n",
                f"parent_lineage={','.join(str(pid) for pid in lineage_pids)}\n",
                f"start_task_ids={','.join(str(pid) for pid in start_tasks)}\n",
                f"end_task_ids={','.join(str(pid) for pid in end_tasks)}\n",
                f"perf_attached_task_ids={','.join(str(pid) for pid in start_tasks)}\n",
                f"start_task_count={len(start_tasks)}\n",
                f"end_task_count={len(end_tasks)}\n",
                f"identity_stable={identity_stable}\n",
                f"task_coverage_complete={task_coverage_complete}\n",
                f"task_set_changed={int(start_tasks != end_tasks)}\n",
            )
        ),
        encoding="ascii",
    )


class PerfPhase:
    def __init__(
        self,
        phase: str,
        output: Path,
        events: str,
        target: ProcessIdentity,
        lineage_pids: list[int],
        control_dir: Path,
        artifact_prefix: Path,
        timeout: float,
    ) -> None:
        self.phase = phase
        self.output = output
        self.target = target
        self.lineage_pids = lineage_pids
        self.artifact_prefix = artifact_prefix
        self.timeout = timeout
        self.start_tasks = task_ids(target.pid)
        if not self.start_tasks:
            raise RuntimeError(f"cache-main has no tasks during {phase}")
        self.control_fifo = control_dir / f"perf-{phase}.control"
        self.ack_fifo = control_dir / f"perf-{phase}.ack"
        os.mkfifo(self.control_fifo, 0o600)
        os.mkfifo(self.ack_fifo, 0o600)
        self.control_fd = os.open(self.control_fifo, os.O_RDWR | os.O_NONBLOCK)
        self.ack_fd = os.open(self.ack_fifo, os.O_RDWR | os.O_NONBLOCK)
        self.ack_reader = LineReader(self.ack_fd)
        command = perf_stat_command(events, self.start_tasks, output, self.control_fifo, self.ack_fifo)
        self.process = subprocess.Popen(command)

    def command(self, command: str) -> None:
        write_line(self.control_fd, command)
        acknowledgement = self.ack_reader.read(self.timeout)
        if acknowledgement != "ack":
            raise RuntimeError(
                f"perf {self.phase} {command} acknowledgement={acknowledgement!r}"
            )

    def enable(self) -> None:
        self.command("enable")

    def finish(self) -> None:
        self.command("disable")
        end_tasks = task_ids(self.target.pid)
        write_identity_artifact(
            Path(f"{self.artifact_prefix}.{self.phase}.target.env"),
            self.phase,
            self.target,
            self.lineage_pids,
            self.start_tasks,
            end_tasks,
        )
        if read_identity(self.target.pid) != self.target:
            raise RuntimeError(f"cache-main identity changed during {self.phase}")
        if self.start_tasks != end_tasks:
            raise RuntimeError(f"cache-main task coverage changed during {self.phase}")
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            return_code = self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return_code = self.process.wait()
        if return_code not in {0, -signal.SIGINT, 128 + signal.SIGINT}:
            raise RuntimeError(f"perf stat phase={self.phase} exited {return_code}")
        if not self.output.is_file() or self.output.stat().st_size == 0:
            raise RuntimeError(f"perf stat phase={self.phase} produced no artifact")
        Path(f"{self.artifact_prefix}.{self.phase}.handshake.env").write_text(
            "".join(
                (
                    "handshake_schema=driver-perf-fifo-v1\n",
                    f"phase={self.phase}\n",
                    "profiler_ready_ack=1\n",
                    "enable_ack=1\n",
                    "disable_ack=1\n",
                    "phase_complete_ack=1\n",
                )
            ),
            encoding="ascii",
        )
        os.close(self.control_fd)
        os.close(self.ack_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-prefix", required=True, type=Path)
    parser.add_argument("--stat-events", default="instructions,cycles,ref-cycles,task-clock")
    parser.add_argument("--phases", default="load,warm")
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing command after --")
    phases = tuple(phase for phase in args.phases.split(",") if phase)
    if phases != ("load", "warm"):
        parser.error("the decision protocol requires phases load,warm in that order")
    if "instructions" not in args.stat_events.split(","):
        parser.error("--stat-events must include instructions")
    shutil.rmtree(args.control_dir, ignore_errors=True)
    args.control_dir.mkdir(parents=True, mode=0o700)
    request_fifo = args.control_dir / "driver.request"
    ack_fifo = args.control_dir / "driver.ack"
    os.mkfifo(request_fifo, 0o600)
    os.mkfifo(ack_fifo, 0o600)
    request_fd = os.open(request_fifo, os.O_RDWR | os.O_NONBLOCK)
    ack_fd = os.open(ack_fifo, os.O_RDWR | os.O_NONBLOCK)
    request_reader = LineReader(request_fd)
    env = os.environ.copy()
    env.update(
        {
            "BENCH_PHASE_CONTROL_REQUIRED": "1",
            "BENCH_PHASE_CONTROL_REQUEST_FIFO": str(request_fifo),
            "BENCH_PHASE_CONTROL_ACK_FIFO": str(ack_fifo),
        }
    )
    child = subprocess.Popen(command, env=env)
    active: PerfPhase | None = None
    phase_index = 0
    try:
        while phase_index < len(phases):
            line = request_reader.read(args.timeout)
            parts = line.split()
            if len(parts) != 3 or not parts[2].isdigit():
                raise RuntimeError(f"malformed driver phase-control message: {line!r}")
            event, phase, _sender_pid = parts
            expected_phase = phases[phase_index]
            if phase != expected_phase:
                raise RuntimeError(f"phase-control phase={phase!r} expected={expected_phase!r}")
            if event == "READY":
                if active is not None:
                    raise RuntimeError(f"duplicate READY for {phase}")
                target, lineage_pids = select_cache_main(child.pid, args.timeout)
                output = Path(f"{args.artifact_prefix}.{phase}.perf-stat.csv")
                active = PerfPhase(
                    phase,
                    output,
                    args.stat_events,
                target,
                    lineage_pids,
                    args.control_dir,
                    args.artifact_prefix,
                    args.timeout,
        )
                active.enable()
                write_line(ack_fd, f"GO {phase}")
            elif event == "WORK_DONE":
                if active is None:
                    raise RuntimeError(f"WORK_DONE without READY for {phase}")
                if phase == "warm":
                    active.finish()
                    active = None
                    write_line(ack_fd, f"DONE {phase}")
                    phase_index += 1
                else:
                    write_line(ack_fd, f"ACK {phase}")
            elif event == "STOP":
                if phase != "load" or active is None:
                    raise RuntimeError(f"unexpected STOP for {phase}")
                active.finish()
                active = None
                write_line(ack_fd, f"DONE {phase}")
                phase_index += 1
            else:
                raise RuntimeError(f"unknown phase-control event: {event}")
        return_code = child.wait()
        if return_code != 0:
            return return_code
        return 0
    except Exception as exc:
        print(f"controlled perf failure: {exc}", file=sys.stderr)
        if active is not None and active.process.poll() is None:
            active.process.kill()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        return 2
    finally:
        os.close(request_fd)
        os.close(ack_fd)


if __name__ == "__main__":
    raise SystemExit(main())
