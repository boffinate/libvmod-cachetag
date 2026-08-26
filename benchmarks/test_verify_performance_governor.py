#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "verify_performance_governor.sh"


def add_cpu(root: Path, cpu: int, governor: str | None, online: bool = True) -> None:
    cpu_dir = root / f"cpu{cpu}"
    cpu_dir.mkdir()
    if cpu != 0:
        (cpu_dir / "online").write_text("1\n" if online else "0\n", encoding="utf-8")
    if governor is not None:
        cpufreq = cpu_dir / "cpufreq"
        cpufreq.mkdir()
        (cpufreq / "scaling_governor").write_text(governor + "\n", encoding="utf-8")


class PerformanceGovernorTest(unittest.TestCase):
    def run_check(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(SCRIPT), str(root)], capture_output=True, text=True)

    def test_accepts_performance_on_every_online_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_cpu(root, 0, "performance")
            add_cpu(root, 1, "performance")
            result = self.run_check(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cpu_governor_gate=pass", result.stdout)
        self.assertIn("cpu_governor_gate_online_cpus=2", result.stdout)

    def test_rejects_non_performance_governor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_cpu(root, 0, "performance")
            add_cpu(root, 1, "powersave")
            result = self.run_check(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires performance governor on cpu1, found powersave", result.stderr)

    def test_rejects_missing_governor_for_online_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_cpu(root, 0, None)
            result = self.run_check(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a readable scaling governor for cpu0", result.stderr)

    def test_ignores_offline_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_cpu(root, 0, "performance")
            add_cpu(root, 1, None, online=False)
            result = self.run_check(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_root_without_cpu_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_check(Path(tmp))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not identify any online CPUs", result.stderr)


if __name__ == "__main__":
    unittest.main()
