#!/usr/bin/env python3
"""Summarize acknowledged load-phase perf-stat artifacts."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

from run_with_phase_stat import parse_stat_rows, read_key_values


EVENTS = ["task-clock", "instructions", "cycles", "ref-cycles"]
NAME_RE = re.compile(r"^(?P<workload>.+)\.run-(?P<run>[0-9]+)\.load\.perf-stat\.meta$")


def counter_value(row: dict[str, object]) -> float:
    return float(str(row["counter-value"]).replace(",", ""))


def find_driver(meta_path: Path, workload: str, run: int) -> Path:
    candidates = [
        meta_path.parent / f"{workload}_load.run-{run}.driver",
        meta_path.parent / f"{workload}.run-{run}.driver",
        meta_path.parent / f"{workload}_load.driver",
        meta_path.parent / f"{workload}.driver",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"driver artifact not found for {workload} run {run}")


def summarize(label: str, root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for meta_path in sorted(root.rglob("*.load.perf-stat.meta")):
        match = NAME_RE.match(meta_path.name)
        if match is None:
            continue
        workload = match.group("workload")
        run = int(match.group("run"))
        meta = read_key_values(meta_path)
        if meta.get("valid") != "1":
            raise ValueError(f"invalid phase stat metadata: {meta_path}")
        stat_path = meta_path.with_suffix(".json")
        rows = parse_stat_rows(stat_path, EVENTS)
        values = {event: counter_value(row) for event, row in zip(EVENTS, rows)}
        driver = read_key_values(find_driver(meta_path, workload, run))
        objects = int(driver["driver_load_backend_objects"])
        if objects <= 0 or int(driver["driver_load_requests"]) != objects:
            raise ValueError(f"invalid fixed-work evidence: {meta_path}")
        results.append(
            {
                "label": label,
                "workload": workload,
                "run": run,
                "objects": objects,
                "task_clock_usec_per_object": values["task-clock"] * 1000.0 / objects,
                "instructions_per_object": values["instructions"] / objects,
                "cycles_per_object": values["cycles"] / objects,
                "ref_cycles_per_object": values["ref-cycles"] / objects,
                "driver_load_wall_seconds": float(driver["driver_load_wall_seconds"]),
                "meta": str(meta_path),
            }
        )
    if not results:
        raise ValueError(f"no valid phase-stat artifacts under {root}")
    return results


def aggregate(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row["label"]), str(row["workload"])), []).append(row)
    result = []
    metrics = (
        "task_clock_usec_per_object",
        "instructions_per_object",
        "cycles_per_object",
        "ref_cycles_per_object",
        "driver_load_wall_seconds",
    )
    for (label, workload), group in sorted(grouped.items()):
        summary: dict[str, object] = {"label": label, "workload": workload, "runs": len(group)}
        for metric in metrics:
            values = [float(row[metric]) for row in group]
            summary[f"{metric}_median"] = statistics.median(values)
            summary[f"{metric}_spread_percent"] = (
                0.0 if min(values) == 0 else (max(values) / min(values) - 1.0) * 100.0
            )
        result.append(summary)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", metavar="LABEL=DIR")
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    try:
        for rendered in args.inputs:
            if "=" not in rendered:
                parser.error(f"input must be LABEL=DIR: {rendered}")
            label, path = rendered.split("=", 1)
            rows.extend(summarize(label, Path(path)))
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"phase-stat summary failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"rows": rows, "summary": aggregate(rows)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
