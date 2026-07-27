#!/usr/bin/env python3
"""Docker-only regression tests for benchmark sampler liveness and validity."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
RUN_WITH_METRICS = REPO / "benchmarks" / "run_with_metrics.py"
SUMMARIZER = REPO / "benchmarks" / "summarize_results.py"


def parse_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


class SamplerLivenessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "linux":
            raise unittest.SkipTest("authoritative sampler tests require Linux")
        cls.tempdir = Path(tempfile.mkdtemp(prefix="cachetag-sampler-test-"))
        source = cls.tempdir / "vinyld.c"
        source.write_text(
            textwrap.dedent(
                """
                #include <sys/prctl.h>
                #include <stdlib.h>
                #include <unistd.h>

                int main(int argc, char **argv) {
                    unsigned int seconds = argc > 1 ? (unsigned int)atoi(argv[1]) : 1;
                    if (prctl(PR_SET_NAME, "cache-main", 0, 0, 0) != 0) {
                        return 2;
                    }
                    sleep(seconds);
                    return 0;
                }
                """
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["cc", "-O2", "-o", str(cls.tempdir / "vinyld"), str(source)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tempdir)

    def run_sampler(
        self,
        name: str,
        *,
        env_overrides: dict[str, str] | None = None,
        duration: int = 1,
        sample_interval: float = 0.05,
        detailed_interval: float = 0.10,
        detailed_timeout: float = 0.10,
    ) -> tuple[Path, dict[str, str], float]:
        result_dir = self.tempdir / name
        result_dir.mkdir()
        metrics = result_dir / "cachetag_sampler_liveness.run-1.time"
        env = os.environ.copy()
        env.update(env_overrides or {})
        command = [
            sys.executable,
            str(RUN_WITH_METRICS),
            "--metrics",
            str(metrics),
            "--system-sample-interval",
            str(sample_interval),
            "--detailed-memory-interval",
            str(detailed_interval),
            "--detailed-memory-timeout",
            str(detailed_timeout),
            "--perf",
            "off",
            "--",
            str(self.tempdir / "vinyld"),
            str(duration),
        ]
        started = time.monotonic()
        completed = subprocess.run(command, env=env, timeout=duration + 3, check=False)
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 0)
        return result_dir, parse_kv(metrics), elapsed

    def summarize(self, result_dir: Path) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(SUMMARIZER), "--format", "json", str(result_dir)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        payload = json.loads(completed.stdout)
        return payload["results"][0]

    def test_blocked_detailed_read_cannot_stall_cadence_or_stop(self) -> None:
        fifo = self.tempdir / "blocked-smaps"
        os.mkfifo(fifo)
        result_dir, metrics, elapsed = self.run_sampler(
            "blocked",
            env_overrides={"BENCH_PROCESS_DETAILS_SMAPS_PATH_TEMPLATE": str(fifo)},
        )

        self.assertLess(elapsed, 3.0)
        self.assertEqual(metrics["system_sampler_status"], "ok")
        self.assertGreaterEqual(int(metrics["system_sampler_samples"]), 12)
        self.assertGreaterEqual(float(metrics["system_sampler_cadence_ratio"]), 0.80)
        self.assertEqual(metrics["system_sampler_under_sampled"], "0")
        self.assertGreaterEqual(int(metrics["system_detailed_memory_timeouts"]), 1)
        self.assertEqual(metrics["system_detailed_memory_successes"], "0")
        self.assertEqual(metrics["system_detailed_memory_active_helpers"], "0")
        self.assertEqual(metrics["system_detailed_memory_abandoned_helpers"], "0")
        self.assertEqual(metrics["system_detailed_memory_max_concurrent_helpers"], "1")
        self.assertEqual(metrics["system_tracked_cache_process_status"], "ok")
        self.assertTrue(metrics["system_tracked_cache_process_exe"].endswith("/vinyld"))

        rows = [
            json.loads(line)
            for line in Path(str(result_dir / "cachetag_sampler_liveness.run-1.time") + ".samples.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertGreaterEqual(len(rows), 12)
        self.assertTrue(any(row.get("tracked_cache_process_rss_kb", 0) > 0 for row in rows))

    def test_normal_detailed_read_succeeds_with_identity(self) -> None:
        result_dir, metrics, _ = self.run_sampler("normal")

        self.assertEqual(metrics["system_sampler_status"], "ok")
        self.assertGreaterEqual(int(metrics["system_detailed_memory_successes"]), 1)
        self.assertGreater(int(metrics["system_tracked_cache_process_smaps_pss_kb_max"]), 0)
        self.assertEqual(
            metrics["system_detailed_memory_last_success_start_time_ticks"],
            metrics["system_tracked_cache_process_start_time_ticks"],
        )
        summary = self.summarize(result_dir)
        self.assertEqual(summary["runs"]["valid"], 1)
        row = summary["workloads"][0]
        self.assertEqual(row["workload_valid"], 1)
        self.assertEqual(row["system_sampling_valid"], 1)
        self.assertEqual(row["system_memory_valid"], 1)
        self.assertEqual(row["overall_valid"], 1)

    def test_pid_start_time_mismatch_is_rejected_before_detail_reads(self) -> None:
        raw = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii")
        fields = raw[raw.rfind(")") + 2 :].split()
        start_time = int(fields[19])
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO / "benchmarks" / "read_process_details.py"),
                "--pid",
                str(os.getpid()),
                "--expected-start-time",
                str(start_time + 1),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(json.loads(completed.stdout)["status"], "identity_mismatch")

    def test_frozen_thresholds_pass_at_campaign_cadences(self) -> None:
        for name, interval, duration in (("cadence-01", 0.1, 2), ("cadence-1", 1.0, 6)):
            with self.subTest(interval=interval):
                result_dir, metrics, _ = self.run_sampler(
                    name,
                    duration=duration,
                    sample_interval=interval,
                    detailed_interval=1.0,
                    detailed_timeout=0.5,
                )
                self.assertEqual(metrics["system_sampler_status"], "ok")
                self.assertGreaterEqual(float(metrics["system_sampler_cadence_ratio"]), 0.80)
                self.assertEqual(self.summarize(result_dir)["runs"]["valid"], 1)

    def test_sparse_artifact_is_loudly_invalid_without_rejecting_independent_scopes(self) -> None:
        result_dir = self.tempdir / "sparse"
        result_dir.mkdir()
        metrics = result_dir / "cachetag_sparse.run-1.time"
        metrics.write_text(
            "\n".join(
                (
                    "exit_code=0",
                    "wall_seconds=10.0",
                    "system_sampler_interval_seconds=0.1",
                    "system_sampler_status=active",
                    "system_sampler_samples=3",
                    "system_tracked_cache_process_status=missing",
                    "",
                )
            ),
            encoding="ascii",
        )
        samples = Path(str(metrics) + ".samples.jsonl")
        samples.write_text(
            "\n".join(
                json.dumps({"monotonic_seconds": value, "sampler_interval_seconds": 0.1})
                for value in (0.1, 0.2, 0.3)
            )
            + "\n",
            encoding="utf-8",
        )
        (result_dir / "cachetag_sparse.run-1.driver").write_text(
            "\n".join(
                (
                    "driver_stream1_overlap_reads=2",
                    "driver_stream1_overlap_purge_start_unix_ns=150",
                    "driver_stream1_overlap_purge_end_unix_ns=250",
                    "driver_stream1_overlap_reads_during_purge=1",
                    "driver_stream1_overlap_reads_over_15ms=0",
                    "driver_stream1_overlap_reads_during_purge_over_15ms=0",
                    "driver_errors=0",
                    "",
                )
            ),
            encoding="ascii",
        )
        (result_dir / "cachetag_sparse.run-1.stream1_overlap_reads.latency_samples.tsv").write_text(
            "\n".join(
                (
                    "request_index\tstart_unix_ns\tend_unix_ns\tseconds\trelation\tcache_state",
                    "1\t100\t120\t0.001\tbefore\thit",
                    "2\t200\t220\t0.002\tduring\thit",
                    "",
                )
            ),
            encoding="ascii",
        )

        summary = self.summarize(result_dir)
        self.assertEqual(summary["runs"]["process_pass"], 1)
        self.assertEqual(summary["runs"]["valid"], 0)
        self.assertEqual(summary["runs"]["invalid"], 1)
        row = summary["workloads"][0]
        self.assertEqual(row["workload_valid"], 1)
        self.assertEqual(row["raw_latency_valid"], 1)
        self.assertEqual(row["raw_latency_validity_reason"], "ok")
        self.assertEqual(row["system_sampling_valid"], 0)
        self.assertEqual(row["system_memory_valid"], 0)
        self.assertEqual(row["overall_valid"], 0)
        self.assertIn("cadence_ratio", row["system_sampling_validity_reason"])

    def test_missing_provenance_rejects_memory_with_healthy_cadence(self) -> None:
        result_dir = self.tempdir / "missing-provenance"
        result_dir.mkdir()
        metrics = result_dir / "cachetag_missing_provenance.run-1.time"
        metrics.write_text(
            "\n".join(
                (
                    "exit_code=0",
                    "wall_seconds=2.0",
                    "system_sampler_interval_seconds=0.1",
                    "system_sampler_status=active",
                    "system_sampler_samples=19",
                    "system_tracked_cache_process_status=missing",
                    "",
                )
            ),
            encoding="ascii",
        )
        Path(str(metrics) + ".samples.jsonl").write_text(
            "\n".join(
                json.dumps({"monotonic_seconds": value / 10, "sampler_interval_seconds": 0.1})
                for value in range(1, 20)
            )
            + "\n",
            encoding="utf-8",
        )

        summary = self.summarize(result_dir)
        row = summary["workloads"][0]
        self.assertEqual(row["workload_valid"], 1)
        self.assertEqual(row["system_sampling_valid"], 1)
        self.assertEqual(row["system_memory_valid"], 0)
        self.assertEqual(row["system_memory_validity_reason"], "cache_process_provenance_missing")
        self.assertEqual(row["overall_valid"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
