#!/usr/bin/env python3
"""Pure-Python regression tests for sampler phase/CPU JSONL schema."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_with_metrics import SystemSampler, ThreadCpuSample  # noqa: E402


class PhaseMarkerSamplingTests(unittest.TestCase):
    def test_markers_follow_embedded_time_not_filename_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            # Lexical order is end/start, deliberately the inverse of time.
            (directory / "work_load.load.end").write_text(
                "time_unix_nano=20\nphase=load\nevent=end\n", encoding="ascii"
            )
            (directory / "work_load.load.start").write_text(
                "time_unix_nano=10\nphase=load\nevent=start\n", encoding="ascii"
            )
            sampler = SystemSampler(phase_marker_dir=directory, phase_marker_prefix="work")
            events = sampler._read_phase_markers()

            self.assertEqual([event["event"] for event in events], ["start", "end"])
            self.assertEqual(sampler.active_phases, set())

    def test_sample_row_retains_process_cpu_delta_and_thread_count(self) -> None:
        sampler = SystemSampler()
        sampler.process_root_pid = 42
        sample = ThreadCpuSample(
            pid=42,
            tid=42,
            ppid=1,
            comm="vinyltest",
            exe="/work/vinyltest",
            start_time_ticks=7,
            cpu_ticks=100,
            rss_kb=64,
            memory_kb={"status_VmRSS_kb": 64},
        )
        sampler.latest_tracked_cpu["vinyltest"] = (25, 0.25, 50.0)

        row = sampler._sample_row_locked(
            {42: sample},
            {(42, 42): sample},
            {},
            {},
            1.0,
            [],
            {"system_cpu_busy_percent": 75.0},
        )

        self.assertEqual(row["tracked_vinyltest_thread_count"], 1)
        self.assertEqual(row["tracked_vinyltest_cpu_delta_ticks"], 25)
        self.assertEqual(row["tracked_vinyltest_cpu_delta_seconds"], 0.25)
        self.assertEqual(row["tracked_vinyltest_cpu_percent"], 50.0)
        self.assertEqual(row["system_cpu_busy_percent"], 75.0)


if __name__ == "__main__":
    unittest.main()
