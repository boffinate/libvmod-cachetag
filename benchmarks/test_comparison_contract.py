#!/usr/bin/env python3
"""Fail-closed comparison-v1 report fixtures; execute in benchmark Docker."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from summarize_results import (
    aggregate_workload_rows,
    arm_workload_keys,
    cache_main_capture_pss_kb,
    comparison_arm_cohort_validity,
    comparison_contract_validity,
    fmt_vcl_shape,
    parse_perf_stat_csv,
    render_arm_comparison,
    sweep_configuration,
    workload_driver_values,
    workload_rows,
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
            "docker_image_id": "sha256:image", "bench_set_interning": "0",
            "cachetag_configure_args": "--disable-set-interning",
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
            "driver_load_fixed_work_seconds": "1.0", "driver_load_pending_drain_seconds": "0.1",
            "driver_warm_hits": "9",
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

    def test_capture_pss_and_campaign_arm_key_are_reportable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_valid_fixture(root)
            self.assertEqual(cache_main_capture_pss_kb(root, "fixture", 1, "post_load"), 100)
        self.assertEqual(
            arm_workload_keys(
                {
                    "implementation": "cachetag",
                    "workload": "cachetag_mostly_unique_bound",
                    "profile": "mostly_unique_bound",
                },
                {},
            ),
            ["mostly_unique_bound"],
        )
        self.assertEqual(
            arm_workload_keys(
                {
                    "implementation": "xkey",
                    "workload": "xkey_mostly_unique_bound",
                    "profile": "mostly_unique_bound",
                },
                {},
            ),
            ["mostly_unique_bound"],
        )
        for profile in ("mostly_shared_bound", "ordinary_body_4k"):
            for implementation in ("cachetag", "xkey"):
                self.assertEqual(
                    arm_workload_keys(
                        {
                            "implementation": implementation,
                            "workload": f"{implementation}_{profile}",
                            "profile": profile,
                        },
                        {},
                    ),
                    [profile],
                )

    def test_arm_comparison_excludes_invalid_repetitions(self) -> None:
        base = {
            "implementation": "cachetag",
            "workload": "cachetag_mostly_unique_bound",
            "profile": "mostly_unique_bound",
        }
        arms = {
            "C": [{
                "hardware": "test",
                "path": "/does/not/exist",
                "comparison_cohort_fingerprint": None,
                "workloads": [
                    {**base, "overall_valid": 1, "cache_main_post_load_pss_kb": 100},
                    {**base, "overall_valid": 0, "cache_main_post_load_pss_kb": 1000},
                ],
            }],
        }
        rendered = render_arm_comparison(arms)
        self.assertIn("workload_runs=2 valid_workload_runs=1", rendered)
        self.assertIn("cache_main_post_load_pss_kb (MiB): C=n=1/1 0.10 [0.10, 0.10]", rendered)

    def test_zero_pss_rejects_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            (root / "fixture.run-1.post_load.cache-main.smaps_rollup").write_text(
                "Pss: 0 kB\n", encoding="utf-8"
            )
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("post_load_confirmation_invalid", reason)

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

    def test_interning_screen_is_comparison_active_without_xkey_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            metadata = root / "metadata.env"
            metadata.write_text(
                metadata.read_text(encoding="utf-8")
                .replace("benchmark_contract=comparison-v1", "benchmark_contract=interning-screen-v1")
                + "run_xkey=0\nrun_noindex=0\nbench_set_interning=1\n"
                + "cachetag_configure_args=--enable-set-interning\n",
                encoding="utf-8",
            )
            provenance = root / "build-provenance.env"
            provenance.write_text(
                provenance.read_text(encoding="utf-8")
                .replace("build_provenance_version=3", "build_provenance_version=4")
                .replace("xkey_dirty_state=clean", "xkey_dirty_state=not-applicable")
                .replace("xkey_build_input_sha256=" + self.fingerprint, "xkey_build_input_sha256=none")
                .replace("bench_set_interning=0", "bench_set_interning=1")
                .replace("cachetag_configure_args=--disable-set-interning", "cachetag_configure_args=--enable-set-interning"),
                encoding="utf-8",
            )
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual((valid, reason), (1, "ok"))

    def test_interning_screen_rejects_xkey_and_noindex_arms(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver, time_values, stats = self.write_valid_fixture(root)
            metadata = root / "metadata.env"
            metadata.write_text(
                metadata.read_text(encoding="utf-8")
                .replace("benchmark_contract=comparison-v1", "benchmark_contract=interning-screen-v1")
                + "run_xkey=1\nrun_noindex=1\nbench_set_interning=0\n"
                + "cachetag_configure_args=--disable-set-interning\n",
                encoding="utf-8",
            )
            valid, reason = comparison_contract_validity(root, "fixture", 1, time_values, driver, stats)
        self.assertEqual(valid, 0)
        self.assertIn("interning_screen_xkey_arm_present", reason)
        self.assertIn("interning_screen_noindex_arm_present", reason)

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


class WarmPerfStatTest(unittest.TestCase):
    """BENCH_PERF_STAT is optional telemetry, never a gate."""

    # Borrow the complete result fixture without re-running its assertions.
    fingerprint = ComparisonContractTest.fingerprint
    write_valid_fixture = ComparisonContractTest.write_valid_fixture

    COUNTED = (
        "# started on Thu Aug 20 07:36:28 2026\n"
        "\n"
        "1200000000,,instructions,1000000000,100.00,0.80,insn per cycle\n"
        "1500000000,,cycles,1000000000,100.00,1.500,GHz\n"
        "22500.000,msec,task-clock,1000000000,99.50,0.375,CPUs utilized\n"
    )
    BLOCKED = (
        "# started on Thu Aug 20 07:36:28 2026\n"
        "\n"
        "<not supported>,,instructions,0,0.00\n"
        "<not counted>,,cycles,0,0.00\n"
    )

    def write_run(self, root: Path) -> None:
        driver, time_values, _ = self.write_valid_fixture(root)
        time_values["exit_code"] = "0"
        (root / "fixture.run-1.time").write_text(
            "\n".join(f"{k}={v}" for k, v in time_values.items()) + "\n", encoding="utf-8"
        )
        (root / "fixture.run-1.driver").write_text(
            "\n".join(f"{k}={v}" for k, v in driver.items()) + "\n", encoding="utf-8"
        )

    def row(self, root: Path) -> dict:
        rows = workload_rows(root)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_csv_header_and_blocked_counters_are_not_zeros(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "counted.csv").write_text(self.COUNTED, encoding="utf-8")
            (root / "blocked.csv").write_text(self.BLOCKED, encoding="utf-8")
            self.assertEqual(
                parse_perf_stat_csv(root / "counted.csv"),
                {"instructions": 1200000000, "cycles": 1500000000, "task-clock": 22500.0},
            )
            self.assertEqual(parse_perf_stat_csv(root / "blocked.csv"), {})
            self.assertEqual(parse_perf_stat_csv(root / "absent.csv"), {})

    def test_counted_events_divide_by_the_warm_hit_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_run(root)
            (root / "fixture.run-1.warm.perf-stat.csv").write_text(
                self.COUNTED, encoding="utf-8"
            )
            row = self.row(root)
        self.assertEqual(row["vinyld_warm_instructions"], 1200000000)
        self.assertEqual(row["vinyld_warm_cycles"], 1500000000)
        self.assertEqual(row["vinyld_warm_task_clock_seconds"], 22.5)
        # driver_warm_hits=9 is the same denominator the warm CPU metric uses.
        self.assertAlmostEqual(row["vinyld_warm_instructions_per_hit"], 1200000000 / 9)
        self.assertAlmostEqual(row["vinyld_warm_cycles_per_hit"], 1500000000 / 9)
        self.assertAlmostEqual(row["vinyld_warm_task_clock_seconds_per_hit"], 22.5 / 9)
        self.assertEqual(row["vinyld_warm_instructions_running_percent"], 100.0)
        self.assertEqual(row["vinyld_warm_cycles_running_percent"], 100.0)
        self.assertEqual(row["vinyld_warm_task_clock_running_percent"], 99.5)
        self.assertEqual(row["vinyld_warm_perf_stat_running_percent_min"], 99.5)
        self.assertAlmostEqual(row["cache_main_warm_cpu_seconds_per_hit"], 0.5 / 9)

    def test_absent_csv_leaves_the_metric_absent_and_the_row_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_run(root)
            absent = self.row(root)
            (root / "fixture.run-1.warm.perf-stat.csv").write_text(
                self.COUNTED, encoding="utf-8"
            )
            counted = self.row(root)
        self.assertIsNone(absent["vinyld_warm_instructions"])
        self.assertIsNone(absent["vinyld_warm_cycles"])
        self.assertIsNone(absent["vinyld_warm_instructions_per_hit"])
        # The knob is optional telemetry: present or absent, it must not move
        # the validity verdict in either direction.
        self.assertEqual(
            absent["comparison_contract_validity_reason"],
            counted["comparison_contract_validity_reason"],
        )
        self.assertEqual(absent["overall_valid"], counted["overall_valid"])
        self.assertNotIn("perf", absent["comparison_contract_validity_reason"])

    def test_counted_events_reach_the_workload_medians(self) -> None:
        rows = [
            {
                "workload": "cachetag_low_fanout_unique", "run": run, "overall_valid": 1,
                "implementation": "cachetag", "profile": "low_fanout_unique",
                "vinyld_warm_instructions": value,
                "vinyld_warm_cycles": value * 2,
                "vinyld_warm_instructions_per_hit": value / 10.0,
                "vinyld_warm_cycles_per_hit": value / 5.0,
                "vinyld_warm_task_clock_seconds_per_hit": value / 1000.0,
                "vinyld_warm_ipc": 0.5 + run / 10.0,
            }
            for run, value in ((1, 1000.0), (2, 2000.0), (3, 3000.0))
        ]
        summary = aggregate_workload_rows(rows)[0]
        self.assertEqual(summary["vinyld_warm_instructions_median"], 2000.0)
        self.assertEqual(summary["vinyld_warm_cycles_median"], 4000.0)
        self.assertEqual(summary["vinyld_warm_instructions_per_hit_median"], 200.0)
        self.assertEqual(summary["vinyld_warm_cycles_per_hit_median"], 400.0)
        self.assertEqual(summary["vinyld_warm_task_clock_seconds_per_hit_median"], 2.0)
        self.assertEqual(summary["vinyld_warm_ipc_median"], 0.7)
        self.assertEqual(summary["vinyld_warm_instructions_per_hit_observations"], 3)
        self.assertEqual(summary["vinyld_warm_cycles_per_hit_observations"], 3)
        self.assertEqual(summary["vinyld_warm_ipc_observations"], 3)
        self.assertEqual(summary["vinyld_warm_instructions_min"], 1000.0)
        self.assertEqual(summary["vinyld_warm_instructions_max"], 3000.0)

    def test_metric_observation_count_exposes_partial_perf_stat_repetition_coverage(self) -> None:
        rows = [
            {
                "workload": "cachetag_low_fanout_unique", "run": run, "overall_valid": 1,
                "implementation": "cachetag", "profile": "low_fanout_unique",
                "vinyld_warm_instructions_per_hit": 1000.0 if run == 1 else None,
                "cache_main_warm_cpu_seconds_per_hit": 0.1 + run,
            }
            for run in (1, 2, 3)
        ]
        summary = aggregate_workload_rows(rows)[0]
        self.assertEqual(summary["valid_runs"], 3)
        self.assertEqual(summary["vinyld_warm_instructions_per_hit_observations"], 1)
        self.assertEqual(summary["cache_main_warm_cpu_seconds_per_hit_observations"], 3)

    def test_absent_counters_leave_no_median(self) -> None:
        rows = [{
            "workload": "cachetag_low_fanout_unique", "run": 1, "overall_valid": 1,
            "implementation": "cachetag", "profile": "low_fanout_unique",
            "vinyld_warm_instructions": None,
            "vinyld_warm_cycles": None,
            "vinyld_warm_instructions_per_hit": None,
        }]
        summary = aggregate_workload_rows(rows)[0]
        self.assertIsNone(summary["vinyld_warm_instructions_median"])
        self.assertIsNone(summary["vinyld_warm_instructions_per_hit_median"])

    def test_arm_comparison_reports_instructions_per_hit(self) -> None:
        base = {
            "implementation": "cachetag",
            "workload": "cachetag_low_fanout_unique",
            "profile": "low_fanout_unique",
            "overall_valid": 1,
        }
        arms = {
            "baseline": [{
                "hardware": "test", "path": "/does/not/exist",
                "comparison_cohort_fingerprint": None, "bench_stale_deliver": "0",
                "workloads": [{**base, "vinyld_warm_instructions_per_hit": 1000.0}],
            }],
            "patched": [{
                "hardware": "test", "path": "/does/not/exist",
                "comparison_cohort_fingerprint": None, "bench_stale_deliver": "0",
                "workloads": [{**base, "vinyld_warm_instructions_per_hit": 1100.0}],
            }],
        }
        rendered = render_arm_comparison(arms)
        self.assertIn("vinyld_warm_instructions_per_hit (instructions/hit):", rendered)
        self.assertIn("baseline=n=1", rendered)
        self.assertIn("delta=10.00%", rendered)

    def test_judged_arm_comparison_withholds_partial_perf_stat_metric(self) -> None:
        base = {
            "implementation": "cachetag",
            "workload": "cachetag_low_fanout_unique",
            "profile": "low_fanout_unique",
            "overall_valid": 1,
        }
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            left_path, right_path = Path(left), Path(right)
            for path in (left_path, right_path):
                (path / "metadata.env").write_text(
                    "benchmark_contract=comparison-v1\n", encoding="utf-8"
                )
            arms = {
                "baseline": [{
                    "hardware": "test", "path": str(left_path),
                    "comparison_cohort_fingerprint": "same", "bench_stale_deliver": "1",
                    "workloads": [
                        {**base, "run": 1, "vinyld_warm_instructions_per_hit": 1000.0},
                        {**base, "run": 2, "vinyld_warm_instructions_per_hit": None},
                    ],
                }],
                "patched": [{
                    "hardware": "test", "path": str(right_path),
                    "comparison_cohort_fingerprint": "same", "bench_stale_deliver": "1",
                    "workloads": [
                        {**base, "run": 1, "vinyld_warm_instructions_per_hit": 900.0},
                        {**base, "run": 2, "vinyld_warm_instructions_per_hit": None},
                    ],
                }],
            }
            rendered = render_arm_comparison(arms)
        self.assertIn(
            "comparison withheld: judged perf stat requires every valid repetition",
            rendered,
        )
        self.assertIn("baseline=1/2", rendered)
        self.assertIn("patched=1/2", rendered)
        self.assertNotIn("vinyld_warm_instructions_per_hit (instructions/hit):", rendered)

    def test_judged_arm_comparison_checks_each_perf_event_coverage(self) -> None:
        base = {
            "implementation": "cachetag",
            "workload": "cachetag_low_fanout_unique",
            "profile": "low_fanout_unique",
            "overall_valid": 1,
        }
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            left_path, right_path = Path(left), Path(right)
            for path in (left_path, right_path):
                (path / "metadata.env").write_text(
                    "benchmark_contract=comparison-v1\n", encoding="utf-8"
                )
            arms = {
                arm: [{
                    "hardware": "test", "path": str(path),
                    "comparison_cohort_fingerprint": "same", "bench_stale_deliver": "1",
                    "workloads": [
                        {
                            **base,
                            "run": run,
                            "vinyld_warm_instructions_per_hit": instruction_base + run,
                            "vinyld_warm_cycles_per_hit": 2000.0 if run == 1 else None,
                        }
                        for run in (1, 2)
                    ],
                }]
                for arm, path, instruction_base in (
                    ("baseline", left_path, 1000.0),
                    ("patched", right_path, 900.0),
                )
            }
            rendered = render_arm_comparison(arms)
        self.assertIn(
            "vinyld_warm_instructions_per_hit (instructions/hit): baseline=n=2/2",
            rendered,
        )
        self.assertIn(
            "vinyld_warm_cycles_per_hit comparison withheld: judged perf stat requires every valid repetition",
            rendered,
        )
        self.assertIn("baseline=1/2", rendered)

    def test_blocked_counters_leave_the_metric_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_run(root)
            baseline = self.row(root)
            (root / "fixture.run-1.warm.perf-stat.csv").write_text(
                self.BLOCKED, encoding="utf-8"
            )
            blocked = self.row(root)
        self.assertIsNone(blocked["vinyld_warm_instructions"])
        self.assertIsNone(blocked["vinyld_warm_cycles"])
        self.assertEqual(
            blocked["comparison_contract_validity_reason"],
            baseline["comparison_contract_validity_reason"],
        )


class StaleDeliverReportingTest(unittest.TestCase):
    """The measured VCL shape must be readable, not only fingerprinted."""

    def test_shape_label_distinguishes_one_call_from_two_call(self) -> None:
        self.assertIn("two-call", fmt_vcl_shape("1"))
        self.assertIn("one-call", fmt_vcl_shape("0"))
        self.assertEqual(fmt_vcl_shape(""), "unrecorded")
        self.assertEqual(fmt_vcl_shape(None), "unrecorded")

    def test_shape_splits_the_sweep_match_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one_call = root / "one"
            two_call = root / "two"
            for directory, value in ((one_call, "0"), (two_call, "1")):
                directory.mkdir()
                (directory / "metadata.env").write_text(
                    f"bench_stale_deliver={value}\n", encoding="utf-8"
                )
            identities = {
                dict(sweep_configuration(directory)["match_identity"])["bench_stale_deliver"]
                for directory in (one_call, two_call)
            }
        self.assertEqual(identities, {"0", "1"})

    def test_arm_comparison_renders_each_arm_shape(self) -> None:
        arms = {
            "one-call": [{
                "hardware": "test", "path": "/does/not/exist",
                "comparison_cohort_fingerprint": None, "bench_stale_deliver": "0",
                "workloads": [],
            }],
            "two-call": [{
                "hardware": "test", "path": "/does/not/exist",
                "comparison_cohort_fingerprint": None, "bench_stale_deliver": "1",
                "workloads": [],
            }],
        }
        rendered = render_arm_comparison(arms)
        self.assertIn("one-call VCL shape: 0 (one-call: vcl_hit)", rendered)
        self.assertIn("two-call VCL shape: 1 (two-call: vcl_hit + vcl_deliver)", rendered)


if __name__ == "__main__":
    unittest.main()
