#!/usr/bin/env python3
"""Summarize load-phase perf counters from benchmark .time/.driver artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from statistics import median


def parse_kv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def iter_time_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("*.run-*.time"))


def driver_path_for(time_path: Path) -> Path:
    name = time_path.name
    if ".run-" not in name:
        raise ValueError(f"unexpected time filename: {name}")
    stem = name[: name.index(".run-")]
    run_suffix = name[name.index(".run-") :]
    if stem == "noindex_load":
        return time_path.with_name(f"{stem}{run_suffix[:-5]}.driver")
    return time_path.with_name(f"{stem}_load{run_suffix[:-5]}.driver")


def safe_float(data: dict[str, str], key: str) -> float | None:
    raw = data.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def per_request(metric: float | None, requests: float | None) -> float | None:
    if metric is None or requests in (None, 0.0):
        return None
    return metric / requests


def metric_median(values: list[float | None]) -> float | None:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return median(filtered)


def fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    grouped: dict[str, list[dict[str, float | None]]] = {}
    for root in args.paths:
        for time_path in iter_time_files(root):
            driver_path = driver_path_for(time_path)
            if not driver_path.is_file():
                continue
            metrics = parse_kv(time_path)
            driver = parse_kv(driver_path)
            requests = safe_float(driver, "driver_load_requests")
            record = {
                "requests": requests,
                "rps": safe_float(driver, "driver_load_requests_per_second"),
                "cycles_per_req": per_request(safe_float(metrics, "hardware_cycles"), requests),
                "instr_per_req": per_request(
                    safe_float(metrics, "hardware_instructions"), requests
                ),
                "cache_misses_per_req": per_request(
                    safe_float(metrics, "hardware_cache_misses"), requests
                ),
                "cache_refs_per_req": per_request(
                    safe_float(metrics, "hardware_cache_references"), requests
                ),
                "branch_misses_per_req": per_request(
                    safe_float(metrics, "hardware_branch_misses"), requests
                ),
                "branch_instr_per_req": per_request(
                    safe_float(metrics, "hardware_branch_instructions"), requests
                ),
                "faults_per_req": per_request(
                    safe_float(metrics, "software_page_faults"), requests
                ),
                "ctxswitch_per_req": per_request(
                    safe_float(metrics, "software_context_switches"), requests
                ),
                "migrations_per_req": per_request(
                    safe_float(metrics, "software_cpu_migrations"), requests
                ),
            }
            cycles = record["cycles_per_req"]
            instr = record["instr_per_req"]
            record["cpi"] = cycles / instr if cycles and instr else None
            grouped.setdefault(time_path.stem.split(".run-")[0], []).append(record)

    print(
        "\t".join(
            [
                "workload",
                "median_rps",
                "cycles_per_req",
                "instr_per_req",
                "cpi",
                "cache_misses_per_req",
                "cache_refs_per_req",
                "branch_misses_per_req",
                "branch_instr_per_req",
                "faults_per_req",
                "ctxswitch_per_req",
                "migrations_per_req",
            ]
        )
    )
    for workload in sorted(grouped):
        rows = grouped[workload]
        print(
            "\t".join(
                [
                    workload,
                    fmt(metric_median([r["rps"] for r in rows])),
                    fmt(metric_median([r["cycles_per_req"] for r in rows])),
                    fmt(metric_median([r["instr_per_req"] for r in rows])),
                    fmt(metric_median([r["cpi"] for r in rows])),
                    fmt(metric_median([r["cache_misses_per_req"] for r in rows])),
                    fmt(metric_median([r["cache_refs_per_req"] for r in rows])),
                    fmt(metric_median([r["branch_misses_per_req"] for r in rows])),
                    fmt(metric_median([r["branch_instr_per_req"] for r in rows])),
                    fmt(metric_median([r["faults_per_req"] for r in rows])),
                    fmt(metric_median([r["ctxswitch_per_req"] for r in rows])),
                    fmt(metric_median([r["migrations_per_req"] for r in rows])),
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
