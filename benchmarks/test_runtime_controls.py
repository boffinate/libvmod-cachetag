#!/usr/bin/env python3
"""Deterministic checks for benchmark CPU/cohort metadata."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks"))
import run_with_metrics as RUN_WITH_METRICS  # noqa: E402


class RuntimeControlTests(unittest.TestCase):
    def test_current_process_has_recordable_cpu_affinity(self) -> None:
        allowed = RUN_WITH_METRICS.read_process_cpus_allowed_list(os.getpid())
        self.assertRegex(allowed, r"^[0-9,-]+$")

    def test_process_snapshot_carries_parent_and_affinity(self) -> None:
        samples = RUN_WITH_METRICS.process_thread_snapshot(os.getpid())
        process_samples = [sample for sample in samples.values() if sample.pid == os.getpid()]
        self.assertTrue(process_samples)
        representative = process_samples[0]
        # A directly-invoked Docker command is PID 1 and legitimately has
        # PPid 0; descendants must still retain their concrete parent ID.
        self.assertGreaterEqual(representative.ppid, 0)
        self.assertRegex(representative.cpus_allowed_list, r"^[0-9,-]+$")

    def test_system_metadata_includes_cohort_and_topology_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "system.env"
            subprocess.run(
                ["sh", str(REPO / "benchmarks" / "capture_system_metadata.sh"), str(output)],
                check=True,
            )
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
        for key in (
            "boot_id",
            "cpu_frequency_state",
            "cpu_scaling_governors",
            "cpu_smt_siblings",
            "cpu_topology",
            "kernel",
            "self_cpus_allowed_list",
        ):
            self.assertIn(key, values)
            self.assertTrue(values[key])


if __name__ == "__main__":
    unittest.main()
