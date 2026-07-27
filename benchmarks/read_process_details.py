#!/usr/bin/env python3
"""Read mmap-sensitive process details for run_with_metrics.py.

This program is deliberately a separate process. The sampler may terminate it
without risking its cadence thread when a procfs read blocks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SMAPS_MEMORY_FIELDS = {
    "Rss",
    "Pss",
    "Private_Clean",
    "Private_Dirty",
    "Shared_Clean",
    "Shared_Dirty",
    "Anonymous",
    "Swap",
}


def proc_path(env_name: str, default: str, pid: int) -> Path:
    template = os.environ.get(env_name, default)
    return Path(template.format(pid=pid))


def start_time_ticks(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii")
    except OSError:
        return None
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 2 :].split()
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def read_smaps(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    with path.open(encoding="ascii") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 2:
                continue
            key = parts[0].rstrip(":")
            if key not in SMAPS_MEMORY_FIELDS:
                continue
            try:
                values[f"smaps_{key.lower()}_kb"] = int(parts[1])
            except ValueError:
                continue
    return values


def read_mapping_summary(path: Path) -> dict[str, int | str]:
    has_jemalloc = False
    has_tcmalloc = False
    has_libc = False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lower = line.lower()
            has_jemalloc = has_jemalloc or "jemalloc" in lower
            has_tcmalloc = has_tcmalloc or "tcmalloc" in lower
            has_libc = has_libc or "libc" in lower or "[heap]" in lower
    if has_jemalloc:
        allocator = "jemalloc"
    elif has_tcmalloc:
        allocator = "tcmalloc"
    elif has_libc:
        allocator = "libc-or-unknown"
    else:
        allocator = "unknown"
    return {
        "maps_has_jemalloc": int(has_jemalloc),
        "maps_has_tcmalloc": int(has_tcmalloc),
        "maps_has_libc": int(has_libc),
        "allocator_hint": allocator,
    }


def read_cmdline(path: Path) -> str:
    raw = path.read_bytes()
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--expected-start-time", required=True, type=int)
    args = parser.parse_args()

    stat_path = proc_path("BENCH_PROCESS_DETAILS_STAT_PATH_TEMPLATE", "/proc/{pid}/stat", args.pid)
    before = start_time_ticks(stat_path)
    result: dict[str, object] = {
        "pid": args.pid,
        "expected_start_time_ticks": args.expected_start_time,
        "observed_start_time_ticks": before,
    }
    if before != args.expected_start_time:
        result["status"] = "identity_mismatch"
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        memory = read_smaps(
            proc_path(
                "BENCH_PROCESS_DETAILS_SMAPS_PATH_TEMPLATE",
                "/proc/{pid}/smaps_rollup",
                args.pid,
            )
        )
        mapping = read_mapping_summary(
            proc_path("BENCH_PROCESS_DETAILS_MAPS_PATH_TEMPLATE", "/proc/{pid}/maps", args.pid)
        )
        cmdline = read_cmdline(
            proc_path(
                "BENCH_PROCESS_DETAILS_CMDLINE_PATH_TEMPLATE",
                "/proc/{pid}/cmdline",
                args.pid,
            )
        )
    except OSError as exc:
        result.update({"status": "read_error", "error": f"{exc.errno}:{exc.strerror}"})
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0

    after = start_time_ticks(stat_path)
    result["observed_start_time_ticks_after"] = after
    if after != args.expected_start_time:
        result["status"] = "identity_mismatch"
    else:
        result.update(
            {
                "status": "ok",
                "memory_kb": memory,
                "mapping": mapping,
                "cmdline": cmdline,
            }
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
