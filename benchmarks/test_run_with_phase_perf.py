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
    @staticmethod
    def python_perf_program(body: str) -> str:
        return f"#!{sys.executable}\n" + body

    def run_record_fixture(
        self,
        perf_program: str,
        child_exit: int = 0,
        wait_for_perf_start: bool = False,
        metrics_wrapper: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], bool, bytes, str]:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake_bin = directory / "fake-bin"
            fake_bin.mkdir()
            fake_perf = fake_bin / "perf"
            fake_perf.write_text(perf_program, encoding="ascii")
            fake_perf.chmod(0o755)
            child = directory / "marker_child.py"
            child_program = (
                "import os\n"
                "import time\n"
                "from pathlib import Path\n"
                "markers = Path(os.environ['BENCH_PHASE_MARKER_DIR'])\n"
                "prefix = os.environ['BENCH_PHASE_MARKER_PREFIX']\n"
                "markers.mkdir(parents=True, exist_ok=True)\n"
                "(markers / f'{prefix}.warm.start').write_text('start\\n', encoding='ascii')\n"
            )
            if wait_for_perf_start:
                child_program += (
                    "ready = Path(os.environ['PERF_FIXTURE_READY'])\n"
                    "deadline = time.monotonic() + 2\n"
                    "while not ready.exists():\n"
                    "    if time.monotonic() >= deadline:\n"
                    "        raise SystemExit(8)\n"
                    "    time.sleep(0.01)\n"
                )
            child_program += (
                "time.sleep(0.2)\n"
                "(markers / f'{prefix}.warm.end').write_text('end\\n', encoding='ascii')\n"
            )
            if child_exit:
                child_program += f"raise SystemExit({child_exit})\n"
            child.write_text(child_program, encoding="ascii")
            markers = directory / "markers"
            perf_data = directory / "profile.perf.data"
            env = {"PATH": str(fake_bin)}
            if wait_for_perf_start:
                env["PERF_FIXTURE_READY"] = str(directory / "perf-ready")
            command = [
                sys.executable,
                str(Path(__file__).with_name("run_with_phase_perf.py")),
                "--perf-data",
                str(perf_data),
                "--marker-dir",
                str(markers),
                "--marker-prefix",
                "fixture",
                "--phase",
                "warm",
                "--scope",
                "system",
                "--",
                sys.executable,
                str(child),
            ]
            metrics = directory / "metrics"
            if metrics_wrapper:
                command = [
                    sys.executable,
                    str(Path(__file__).with_name("run_with_metrics.py")),
                    "--metrics",
                    str(metrics),
                    "--system-sample-interval",
                    "0",
                    "--perf",
                    "off",
                    "--",
                    *command,
                ]
            result = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            artifact_exists = perf_data.is_file()
            artifact = perf_data.read_bytes() if artifact_exists else b""
            metrics_text = metrics.read_text(encoding="ascii") if metrics.is_file() else ""

        return result, artifact_exists, artifact, metrics_text

    def test_record_mode_rejects_successful_perf_without_an_artifact(self) -> None:
        result, artifact_exists, _, _ = self.run_record_fixture("#!/bin/sh\nexit 0\n")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(artifact_exists)

    def test_record_mode_rejects_a_perf_spawn_failure(self) -> None:
        result, artifact_exists, _, _ = self.run_record_fixture("#!/does/not/exist\n")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("perf could not be started", result.stderr)
        self.assertFalse(artifact_exists)

    def test_record_mode_accepts_a_nonempty_artifact_after_sigint(self) -> None:
        result, artifact_exists, artifact, _ = self.run_record_fixture(
            self.python_perf_program(
                "import os\n"
                "import signal\n"
                "import sys\n"
                "from pathlib import Path\n"
                "out = Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "out.write_bytes(b'profile')\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
                "Path(os.environ['PERF_FIXTURE_READY']).touch()\n"
                "signal.pause()\n"
            ),
            wait_for_perf_start=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(artifact_exists)
        self.assertEqual(artifact, b"profile")

    def test_record_mode_propagates_the_workload_failure(self) -> None:
        result, artifact_exists, artifact, _ = self.run_record_fixture(
            self.python_perf_program(
                "import os\n"
                "import signal\n"
                "import sys\n"
                "from pathlib import Path\n"
                "out = Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "out.write_bytes(b'profile')\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
                "Path(os.environ['PERF_FIXTURE_READY']).touch()\n"
                "signal.pause()\n"
            ),
            child_exit=7,
            wait_for_perf_start=True,
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue(artifact_exists)
        self.assertEqual(artifact, b"profile")

    def test_record_mode_rejects_a_zero_byte_artifact(self) -> None:
        result, artifact_exists, artifact, _ = self.run_record_fixture(
            self.python_perf_program(
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('-o') + 1]).touch()\n"
            )
        )

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue(artifact_exists)
        self.assertEqual(artifact, b"")

    def test_record_mode_rejects_a_non_sigint_failure_with_an_artifact(self) -> None:
        result, artifact_exists, artifact, _ = self.run_record_fixture(
            self.python_perf_program(
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'partial')\n"
                "raise SystemExit(7)\n"
            )
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue(artifact_exists)
        self.assertEqual(artifact, b"partial")

    def test_metrics_wrapper_propagates_a_capture_failure(self) -> None:
        result, artifact_exists, _, metrics = self.run_record_fixture(
            "#!/bin/sh\nexit 0\n", metrics_wrapper=True
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(artifact_exists)
        self.assertIn("exit_code=3", metrics)

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

    def test_load_phase_accepts_the_load_driver_marker_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pairs = phase_marker_pairs(directory, "cachetag_mostly_unique_bound", "load")

        self.assertEqual(
            pairs,
            [
                (
                    directory / "cachetag_mostly_unique_bound.load.start",
                    directory / "cachetag_mostly_unique_bound.load.end",
                ),
                (
                    directory / "cachetag_mostly_unique_bound_load.load.start",
                    directory / "cachetag_mostly_unique_bound_load.load.end",
                ),
            ],
        )

    def test_client_sweep_phase_accepts_the_load_driver_marker_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pairs = phase_marker_pairs(
                directory, "cachetag_mostly_unique_bound", "warm_sweep_clients_64"
            )

        self.assertEqual(
            pairs[-1],
            (
                directory / "cachetag_mostly_unique_bound_load.warm_sweep_clients_64.start",
                directory / "cachetag_mostly_unique_bound_load.warm_sweep_clients_64.end",
            ),
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
