#!/usr/bin/env python3
"""Fail-closed comparison-v1 report fixtures; execute in benchmark Docker."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from summarize_results import (
    comparison_arm_cohort_validity,
    comparison_contract_validity,
    workload_driver_values,
)


class ComparisonContractTest(unittest.TestCase):
    fingerprint = "sha256:" + "a" * 64

    def write_valid_fixture(self, root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
        (root / "metadata.env").write_text(
            "\n".join(
                [
                    "benchmark_contract=comparison-v1",
                    "fixture_name=fixture",
                    f"fixture_fingerprint={self.fingerprint}",
                    "bench_driver_gomaxprocs=1",
                    "bench_driver_gogc=100",
                    "bench_driver_gomemlimit=off",
                    "benchmark_cohort_fingerprint=" + "b" * 64,
                    "required_cache_main_endpoints=post_load,post_load_confirmation",
                ]
            ) + "\n",
            encoding="utf-8",
        )
        (root / "system.env").write_text(
            "\n".join(
                [
                    "cpu_model=test-cpu", "cpu_topology=0,0,0,0,Y", "cpu_smt_siblings=cpu0:0",
                    "cpu_scaling_governors=performance", "cpu_frequency_state=cpu0:governor=performance",
                    "cpu_boost_state=cpufreq:1", "kernel=6.1", "nproc=1", "mem_total_kb=100000",
                ]
            ) + "\n",
            encoding="utf-8",
        )
        provenance = {
            "build_provenance_version": "3", "build_provenance_mode": "strict",
            "build_provenance_eligible": "1",
            "cachetag_dirty_state": "clean", "vinyl_dirty_state": "clean", "xkey_dirty_state": "clean",
            "docker_image_id": "sha256:image",
        }
        for key in (
            "vinyl_build_input_sha256", "cachetag_build_input_sha256", "xkey_build_input_sha256",
            "xkey_compat_artifact_sha256", "xkey_config_sha256", "vinyl_binary_sha256",
            "cachetag_binary_sha256", "xkey_binary_sha256", "build_commands_sha256", "dockerfile_sha256",
        ):
            provenance[key] = self.fingerprint
        (root / "build-provenance.env").write_text(
            "\n".join(f"{key}={value}" for key, value in provenance.items()) + "\n", encoding="utf-8"
        )
        (root / "fixtures").mkdir()
        (root / "fixtures" / "fixture.manifest.json").write_text(
            json.dumps({"expected": {"fixture_fingerprint": self.fingerprint, "objects": 3, "relationships": 6}}),
            encoding="utf-8",
        )
        driver = {
            "driver_fixture_name": "fixture", "driver_fixture_fingerprint": self.fingerprint,
            "driver_fixture_expected_objects": "3", "driver_fixture_expected_relationships": "6",
            "driver_load_requests": "3", "driver_load_backend_objects": "3",
            "driver_load_backend_objects_expected": "3", "driver_load_backend_objects_validation": "true",
            "driver_errors": "0", "driver_phase_telemetry_schema": "phase-aligned-v1",
            "driver_pacing_schema": "slot-skipping-v1", "driver_runtime_gomaxprocs": "1",
            "driver_runtime_gogc": "100", "driver_runtime_gomemlimit": "off",
            "driver_phase_scheduled_slots": "10", "driver_phase_executed_slots": "9",
            "driver_phase_skipped_slots": "1", "driver_phase_late_starts": "0",
            "driver_phase_scheduling_lag_seconds": "0", "driver_phase_scheduling_lag_max_seconds": "0",
            "driver_phase_offered_rps": "10", "driver_phase_achieved_rps": "9", "driver_phase_errors": "0",
            "driver_phase_latency_sampling_method": "deterministic-reservoir-v1",
            "driver_phase_latency_sampling_limit": "100", "driver_phase_latency_sampling_seen": "9",
            "driver_phase_latency_sampling_dropped": "0", "driver_phase_latency_samples": "9",
            "driver_phase_latency_p50_seconds": "0.001", "driver_phase_latency_p95_seconds": "0.002",
            "driver_phase_latency_p99_seconds": "0.003", "driver_phase_latency_max_seconds": "0.004",
            "driver_phase_latency_samples_path": "/results/fixture.driver_phase.latency_samples.tsv",
        }
        time_values = {
            "swap_activity": "0", "system_tracked_cache_process_cpus_allowed_list": "0",
            "system_tracked_driver_cpus_allowed_list": "1", "system_tracked_backend_cpus_allowed_list": "2",
            "system_phase_cpu_telemetry_schema": "phase-aligned-process-cpu-v1",
            "system_phase_load_samples": "3", "system_phase_load_wall_seconds": "1.0",
            "system_phase_warm_samples": "3", "system_phase_warm_wall_seconds": "1.0",
            "system_phase_load_cache_main_cpu_seconds": "0.4",
            "system_phase_load_driver_cpu_seconds": "0.2",
            "system_phase_load_backend_cpu_seconds": "0.3",
            "system_phase_warm_cache_main_cpu_seconds": "0.5",
            "system_phase_warm_driver_cpu_seconds": "0.2",
            "system_phase_warm_backend_cpu_seconds": "0.1",
        }
        stats = {"MAIN.n_lru_nuked": 0, "MAIN.n_expired": 0, "MAIN.threads": 1,
                 "MAIN.thread_queue_len": 0, "MAIN.threads_limited": 0, "MAIN.threads_failed": 0}
        (root / "fixture.run-1.driver_phase.latency_samples.tsv").write_text(
            "seconds\n" + "0.001000000\n" * 9, encoding="utf-8"
        )
        for endpoint, pid in (("post_load", "10"), ("post_load_confirmation", "10")):
            prefix = root / f"fixture.run-1.{endpoint}.cache-main"
            (Path(str(prefix) + ".identity")).write_text(
                f"schema=cache-main-memory-v1\nendpoint={endpoint}\nselected_comm=cache-main\n"
                f"identity_valid=1\nidentity_post_capture_valid=1\nselected_pid={pid}\n"
                "selected_starttime_ticks=100\nselected_exe=/usr/sbin/vinyld\nboot_id=boot\n",
                encoding="utf-8",
            )
            (Path(str(prefix) + ".smaps_rollup")).write_text("Pss: 100 kB\n", encoding="utf-8")
        return driver, time_values, stats

    def test_complete_contract_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual((valid, reason), (1, "ok"))

    def test_zero_driver_errors_survives_multi_phase_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for phase in ("load", "warm", "purge"):
                (root / f"fixture_{phase}.run-1.driver").write_text(
                    f"driver_phase={phase}\ndriver_errors=0\n", encoding="utf-8"
                )
            values = workload_driver_values(root, "fixture", 1)
        self.assertEqual(values["driver_errors"], "0")

    def test_missing_pacing_and_latency_telemetry_rejects_scoped_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            del driver["driver_phase_skipped_slots"]
            del driver["driver_phase_latency_sampling_method"]
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("pacing_metric_missing:driver_phase_skipped_slots", reason)
        self.assertIn("latency_sampling_telemetry_missing", reason)

    def test_phase_cpu_schema_and_process_fields_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            time_values["system_phase_cpu_telemetry_schema"] = "old-schema"
            del time_values["system_phase_load_backend_cpu_seconds"]
            del time_values["system_phase_warm_wall_seconds"]
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("phase_cpu_telemetry_schema_invalid", reason)
        self.assertIn("phase_cpu_metric_missing:load_backend_cpu_seconds", reason)
        self.assertIn("phase_cpu_metric_missing:warm_wall_seconds", reason)

    def test_capture_pid_drift_and_work_volume_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            driver["driver_load_backend_objects"] = "2"
            confirmation = root / "fixture.run-1.post_load_confirmation.cache-main.identity"
            confirmation.write_text(confirmation.read_text(encoding="utf-8").replace("selected_pid=10", "selected_pid=11"), encoding="utf-8")
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("backend_work_volume_invalid", reason)
        self.assertIn("capture_process_identity_changed", reason)

    def test_missing_raw_latency_artifact_rejects_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            (root / "fixture.run-1.driver_phase.latency_samples.tsv").unlink()
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("latency_samples_artifact_missing:driver_phase", reason)

    def test_provenance_fingerprint_residency_and_cohort_gates_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            provenance = root / "build-provenance.env"
            provenance.write_text(
                provenance.read_text(encoding="utf-8").replace("xkey_config_sha256=" + self.fingerprint, ""),
                encoding="utf-8",
            )
            driver["driver_fixture_fingerprint"] = "sha256:" + "c" * 64
            time_values["swap_activity"] = "1"
            stats["MAIN.n_lru_nuked"] = 1
            stats["MAIN.n_expired"] = 1
            system = root / "system.env"
            system.write_text(system.read_text(encoding="utf-8").replace("cpu_boost_state=cpufreq:1\n", ""), encoding="utf-8")
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        for expected in (
            "provenance_missing:xkey_config_sha256", "fixture_fingerprint_mismatch", "swap_activity",
            "unexpected_eviction", "unexpected_expiry", "cohort_field_missing:cpu_boost_state",
        ):
            self.assertIn(expected, reason)

    def test_arm_comparison_rejects_cross_cohort_rows(self) -> None:
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            left_path, right_path = Path(left), Path(right)
            (left_path / "metadata.env").write_text("benchmark_contract=comparison-v1\n", encoding="utf-8")
            (right_path / "metadata.env").write_text("benchmark_contract=comparison-v1\n", encoding="utf-8")
            arms = {
                "baseline": [{"path": str(left_path), "comparison_cohort_fingerprint": "a"}],
                "patched": [{"path": str(right_path), "comparison_cohort_fingerprint": "b"}],
            }
            valid, reason = comparison_arm_cohort_validity(arms)
        self.assertEqual((valid, reason), (0, "cohort_fingerprint_changed_across_arms"))

    def test_missing_inputs_are_retained_as_scoped_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "metadata.env").write_text("benchmark_contract=comparison-v1\n", encoding="utf-8")
            valid, reason = comparison_contract_validity(root, "xkey_fixture", 1, {}, {}, {})
        self.assertEqual(valid, 0)
        self.assertIn("provenance_missing:vinyl_build_input_sha256", reason)
        self.assertIn("fixture_fingerprint_missing", reason)
        self.assertIn("swap_telemetry_missing", reason)
        self.assertIn("eviction_telemetry_missing", reason)
        self.assertIn("expiry_telemetry_missing", reason)
        self.assertIn("cohort_fingerprint_missing", reason)

    def test_development_contract_remains_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "metadata.env").write_text("benchmark_contract=development-v1\n", encoding="utf-8")
            valid, reason = comparison_contract_validity(root, "development", 1, {}, {}, {})
        self.assertEqual((valid, reason), (1, "not_applicable"))


if __name__ == "__main__":
    unittest.main()
