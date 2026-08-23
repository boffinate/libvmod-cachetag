#!/usr/bin/env python3
"""Refuse a decision-campaign transition after an ineligible qualification row."""

from __future__ import annotations

import argparse
from statistics import median
from pathlib import Path

from summarize_results import result_data


def dispersion_percent(values: list[float]) -> float:
    if len(values) != 3 or any(value <= 0 for value in values):
        raise ValueError("qualification requires three positive observations")
    centre = median(values)
    return (max(values) - min(values)) / centre * 100.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--shared-load-budget-percent", type=float, required=True)
    parser.add_argument("--shared-warm-budget-percent", type=float, required=True)
    parser.add_argument("--unique-load-budget-percent", type=float, required=True)
    parser.add_argument("--unique-warm-budget-percent", type=float, required=True)
    args = parser.parse_args()
    data = result_data(args.result_dir)
    invalid = [
        f"{row['workload']}.run-{row['run']}: {row['overall_validity_reason']}"
        for row in data["workloads"]
        if row.get("overall_valid") != 1
    ]
    if invalid:
        raise SystemExit("decision qualification rejected: " + " | ".join(invalid))
    budgets = {
        "interning_shared_five": (args.shared_load_budget_percent, args.shared_warm_budget_percent),
        "interning_unique_five": (args.unique_load_budget_percent, args.unique_warm_budget_percent),
    }
    for profile, (load_budget, warm_budget) in budgets.items():
        rows = [row for row in data["workloads"] if row.get("profile") == profile and row.get("overall_valid") == 1]
        if len(rows) != 3:
            raise SystemExit(f"decision qualification rejected: {profile} eligible_repetitions={len(rows)} expected=3")
        for metric, budget in (("vinyld_load_instructions_per_object", load_budget), ("vinyld_warm_instructions_per_hit", warm_budget)):
            values = [row.get(metric) for row in rows]
            if any(value is None or float(value) <= 0 for value in values):
                raise SystemExit(f"decision qualification rejected: {profile} missing {metric}")
            dispersion = dispersion_percent([float(value) for value in values])
            if dispersion > budget:
                raise SystemExit(f"decision qualification rejected: {profile} {metric} dispersion_percent={dispersion:.6f} budget_percent={budget:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
