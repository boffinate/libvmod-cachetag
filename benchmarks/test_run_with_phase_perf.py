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
