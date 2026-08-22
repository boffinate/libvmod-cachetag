#!/usr/bin/env python3
"""Docker-only regression tests for phase-perf marker selection."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_with_phase_perf import (  # noqa: E402
    perf_record_command,
    perf_stat_command,
    phase_marker_pairs,
    wait_for_any_marker,
)


class PhasePerfMarkerTests(unittest.TestCase):
    def test_dwarf_call_graph_is_passed_to_perf_record(self) -> None:
        command = perf_record_command(
            Path("/results/profile.perf.data"), "99", "dwarf", "command", [123, 456]
        )

        self.assertEqual(
            command,
            [
                "perf",
                "record",
                "-F",
                "99",
                "--call-graph",
                "dwarf",
                "-o",
                "/results/profile.perf.data",
                "-p",
                "123,456",
                "--",
                "sleep",
                "86400",
            ],
        )

    def test_record_mode_command_is_unchanged_by_the_stat_option(self) -> None:
        command = perf_record_command(
            Path("/results/profile.perf.data"), "99", "fp", "command", [7]
        )

        self.assertEqual(
            command,
            [
                "perf",
                "record",
                "-F",
                "99",
                "--call-graph",
                "fp",
                "-o",
                "/results/profile.perf.data",
                "-p",
                "7",
                "--",
                "sleep",
                "86400",
            ],
        )

    def test_stat_mode_attaches_csv_counters_to_the_target_pids(self) -> None:
        command = perf_stat_command(
            Path("/results/w.run-1.warm.perf-stat.csv"),
            "instructions,cycles",
            "command",
            [123, 456],
        )

        self.assertEqual(
            command,
            [
                "perf",
                "stat",
                "-e",
                "instructions,cycles",
                "-p",
                "123,456",
                "-x",
                ",",
                "-o",
                "/results/w.run-1.warm.perf-stat.csv",
            ],
        )
        # A workload argument would make perf fork and time the window itself.
        # The phase end marker owns the window, so there must not be one.
        self.assertNotIn("--", command)
        self.assertNotIn("sleep", command)

    def test_stat_mode_system_scope_drops_the_pid_attach(self) -> None:
        command = perf_stat_command(
            Path("/results/system.csv"), "instructions", "system", [123]
        )

        self.assertEqual(
            command,
            ["perf", "stat", "-e", "instructions", "-a", "-x", ",", "-o", "/results/system.csv"],
        )
        self.assertNotIn("-p", command)

    def test_warm_phase_accepts_split_v1_1_workload_marker_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pairs = phase_marker_pairs(directory, "cachetag_mostly_unique_bound", "warm")
            split_start = directory / "cachetag_mostly_unique_bound_warm.warm.start"
            split_start.touch()
            child = subprocess.Popen(["sleep", "1"])
            try:
                selected = wait_for_any_marker([start for start, _ in pairs], child, 0.01)
            finally:
                child.terminate()
                child.wait()

        self.assertEqual(selected, split_start)
        self.assertEqual(
            dict(pairs)[selected],
            directory / "cachetag_mostly_unique_bound_warm.warm.end",
        )

    def test_warm_phase_accepts_the_phased_purge_load_marker_prefix(self) -> None:
        # low-fanout-unique and the other phased-purge workloads run the warm
        # phase inside the load driver invocation, so its markers are named
        # <workload>_load.warm.*, not <workload>.warm.*.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pairs = phase_marker_pairs(directory, "cachetag_low_fanout_unique", "warm")
            load_start = directory / "cachetag_low_fanout_unique_load.warm.start"
            load_start.touch()
            child = subprocess.Popen(["sleep", "1"])
            try:
                selected = wait_for_any_marker([start for start, _ in pairs], child, 0.01)
            finally:
                child.terminate()
                child.wait()

        self.assertEqual(selected, load_start)
        self.assertEqual(
            dict(pairs)[selected],
            directory / "cachetag_low_fanout_unique_load.warm.end",
        )

    def test_non_warm_phase_does_not_invent_a_split_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pairs = phase_marker_pairs(directory, "cachetag_concurrent", "concurrent")

        self.assertEqual(
            pairs,
            [
                (
                    directory / "cachetag_concurrent.concurrent.start",
                    directory / "cachetag_concurrent.concurrent.end",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
