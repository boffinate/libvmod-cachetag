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
            cpus_allowed_list="2-3",
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
        self.assertEqual(row["tracked_vinyltest_ppid"], 1)
        self.assertEqual(row["tracked_vinyltest_cpus_allowed_list"], "2-3")
        self.assertEqual(row["tracked_vinyltest_cpu_delta_ticks"], 25)
        self.assertEqual(row["tracked_vinyltest_cpu_delta_seconds"], 0.25)
        self.assertEqual(row["tracked_vinyltest_cpu_percent"], 50.0)
        self.assertEqual(row["system_cpu_busy_percent"], 75.0)

    def test_completed_markers_produce_phase_aligned_cpu_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "work_load.load.start").write_text(
                "time_unix_nano=110\nphase=load\nevent=start\n", encoding="ascii"
            )
            (directory / "work_load.load.end").write_text(
                "time_unix_nano=190\nphase=load\nevent=end\n", encoding="ascii"
            )
            sampler = SystemSampler(phase_marker_dir=directory, phase_marker_prefix="work")
            sampler.start_monotonic = 1.0
            sampler.start_wall_unix_nano = 100
            sampler.prev_sample_wall_unix_nano = 100
            sampler.latest_tracked_cpu = {
                "cache_process": (20, 0.2, 50.0),
                "driver": (10, 0.1, 25.0),
                "backend": (5, 0.05, 12.5),
            }
            sampler._read_phase_markers()
            sampler._record_phase_cpu_locked(200)
            sampler.stop_monotonic = 2.0
            metrics = sampler.metrics()

        self.assertEqual(metrics["system_phase_cpu_telemetry_schema"], "phase-aligned-process-cpu-v1")
        self.assertEqual(metrics["system_phase_load_samples"], 1)
        self.assertEqual(metrics["system_phase_load_tracked_driver_cpu_ticks"], 8.0)
        self.assertAlmostEqual(metrics["system_phase_load_tracked_driver_cpu_seconds"], 0.08)
        self.assertAlmostEqual(metrics["system_phase_load_cache_main_cpu_seconds"], 0.16)

    def test_active_phase_accumulates_before_end_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "work_warm.warm.start").write_text(
                "time_unix_nano=110\nphase=warm\nevent=start\n", encoding="ascii"
            )
            sampler = SystemSampler(phase_marker_dir=directory, phase_marker_prefix="work")
            sampler.start_wall_unix_nano = 100
            sampler.prev_sample_wall_unix_nano = 100
            sampler.latest_tracked_cpu = {"driver": (10, 0.1, 25.0)}
            sampler._read_phase_markers()
            sampler._record_phase_cpu_locked(200)

        accumulator = sampler.phase_cpu["warm"]
        self.assertEqual(accumulator.samples, 1)
        self.assertEqual(accumulator.cpu_ticks["driver"], 9.0)
        self.assertAlmostEqual(accumulator.wall_seconds, 0.00000009)


if __name__ == "__main__":
    unittest.main()
