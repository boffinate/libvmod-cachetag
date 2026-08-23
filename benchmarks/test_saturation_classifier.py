#!/usr/bin/env python3
"""Focused regression tripwires for the benchmark saturation classifier."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SUMMARIZER_PATH = REPO / "benchmarks" / "summarize_results.py"
FIXTURE = REPO / "benchmarks" / "fixtures" / "saturation_classifier" / "repeated-rollover.json"
SPEC = importlib.util.spec_from_file_location("summarize_results", SUMMARIZER_PATH)
assert SPEC is not None and SPEC.loader is not None
SUMMARIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARIZER)


def campaign_results(*, worker_cap: int = 16) -> list[dict[str, object]]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    for clients, rates in fixture["points"].items():
        classifications = SUMMARIZER.workload_phase_classifications(
            [
                {
                    "workload": fixture["workload"],
                    "run": index + 1,
                    "overall_valid": 1,
                    "driver_load_requests_per_second": rate,
                    "resource_signature": "none observed",
                    "attribution": "unresolved",
                }
                for index, rate in enumerate(rates)
            ]
        )
        results.append(
            {
                "path": f"/{clients}",
                "hardware": fixture["hardware"],
                "sweep_configuration": {
                    "client_count": int(clients),
                    "worker_cap": worker_cap,
                    "thread_pools": fixture["thread_pools"],
                    "build": tuple(fixture["build"]),
                    "match_identity": tuple(tuple(item) for item in fixture["match_identity"]),
                    "cachetag_source_identity_recorded": True,
                },
                "workload_phase_classifications": classifications,
            }
        )
    return results


class SaturationClassifierTest(unittest.TestCase):
    def test_single_core_signature_is_not_hidden_by_aggregate_cpu(self) -> None:
        self.assertEqual(
            SUMMARIZER.classify_resource_signature(
                [{
                    "system_cpu_busy_max_percent": "82.65",
                    "system_cpu_any_core_busy_max_percent": "100",
                    "system_cpu_iowait_max_percent": "0",
                    "system_disk_util_percent_max": "0",
                    "system_memavailable_min_percent": "60",
                }]
            ),
            "single-core",
        )

    def test_repeated_matched_rollover_is_a_knee_without_resource_threshold(self) -> None:
        analysis = SUMMARIZER.analyze_campaign_sweeps(campaign_results())
        self.assertEqual(len(analysis), 1)
        self.assertEqual(analysis[0]["validation"], "ok")
        self.assertEqual(analysis[0]["load_state"], "rollover")
        self.assertEqual([point["repetitions"] for point in analysis[0]["points"]], [3, 3])

    def test_worker_cap_change_rejects_client_sweep(self) -> None:
        results = campaign_results()
        results[-1]["sweep_configuration"]["worker_cap"] = 48
        analysis = SUMMARIZER.analyze_campaign_sweeps(results)
        self.assertEqual(analysis[0]["validation"], "failed")
        self.assertIn("worker_configuration_changed", analysis[0]["validation_errors"])

    def test_source_identity_is_required_only_when_revision_and_hash_are_absent(self) -> None:
        results = campaign_results()
        for result in results:
            result["sweep_configuration"]["cachetag_source_identity_recorded"] = False
        analysis = SUMMARIZER.analyze_campaign_sweeps(results)
        self.assertEqual(analysis[0]["validation"], "failed")
        self.assertIn("cachetag_source_identity_missing", analysis[0]["validation_errors"])

    def test_configuration_identity_keeps_different_shapes_out_of_one_sweep(self) -> None:
        results = campaign_results()
        changed_identity = list(results[-1]["sweep_configuration"]["match_identity"])
        changed_identity[2] = ("objects", "2000000")
        results[-1]["sweep_configuration"]["match_identity"] = tuple(changed_identity)
        analysis = SUMMARIZER.analyze_campaign_sweeps(results)
        self.assertEqual(len(analysis), 2)
        self.assertTrue(all(item["validation"] == "failed" for item in analysis))

    def test_workloads_and_phases_remain_separate(self) -> None:
        classifications = SUMMARIZER.workload_phase_classifications(
            [
                {
                    "workload": "cachetag_shared",
                    "run": 1,
                    "overall_valid": 1,
                    "driver_load_requests_per_second": 100.0,
                    "resource_signature": "none observed",
                    "attribution": "unresolved",
                },
                {
                    "workload": "cachetag_unique",
                    "run": 1,
                    "overall_valid": 1,
                    "driver_warm_requests_per_second": 200.0,
                    "resource_signature": "single-core",
                    "attribution": "driver",
                },
            ]
        )
        self.assertEqual(
            [(item["workload"], item["phase"]) for item in classifications],
            [("cachetag_shared", "load"), ("cachetag_unique", "warm")],
        )
        self.assertTrue(all(item["load_state"] == "inconclusive" for item in classifications))
        self.assertEqual(
            classifications[0]["run_requests_per_second"],
            [{"run": 1, "requests_per_second": 100.0}],
        )

    def test_phase_classification_uses_only_phase_aligned_process_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            time_path = Path(temporary) / "work.run-1.time"
            Path(str(time_path) + ".samples.jsonl").write_text(
                "\n".join(
                    (
                        json.dumps(
                            {
                                "phase_active": "load",
                                "system_cpu_busy_percent": 70.0,
                                "system_cpu_any_core_busy_percent": 100.0,
                                "tracked_cache_process_cpu_percent": 250.0,
                                "tracked_driver_cpu_percent": 420.0,
                                "tracked_backend_cpu_percent": 100.0,
                            }
                        ),
                        json.dumps(
                            {
                                "phase_active": "warm",
                                "system_cpu_busy_percent": 40.0,
                                "system_cpu_any_core_busy_percent": 60.0,
                                "tracked_cache_process_cpu_percent": 180.0,
                                "tracked_driver_cpu_percent": 20.0,
                                "tracked_backend_cpu_percent": 0.0,
                            }
                        ),
                        "",
                    )
                ),
                encoding="utf-8",
            )
            phases = SUMMARIZER.phase_sample_classifications(time_path)

        self.assertEqual(phases["load"]["resource_signature"], "single-core")
        self.assertEqual(phases["load"]["attribution"], "driver")
        self.assertEqual(phases["warm"]["attribution"], "vinyld")

    def test_result_has_only_the_deliberately_mixed_rollup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = Path(temporary)
            (result_dir / "cachetag_shared.run-1.time").write_text(
                "exit_code=0\n", encoding="ascii"
            )
            data = SUMMARIZER.result_data(result_dir)
        self.assertNotIn("likely_limit", data)
        self.assertEqual(data["classification_rollup"], "mixed")

    def test_sweep_configuration_uses_recorded_pool_names_and_shape_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = Path(temporary)
            (result_dir / "metadata.env").write_text(
                "\n".join(
                    (
                        "bench_clients=24",
                        "bench_vinyl_thread_pool_max=16",
                        "bench_vinyl_thread_pools=2",
                        "cachetag_revision=",
                        "cachetag_configure_args=--enable-demo-diagnostics",
                        "bench_code_generation=runtime",
                        "bench_runtime_set_interning_requested=1",
                        "bench_effective_set_interning=1",
                        "objects=1000000",
                        "bench_storage_kind=default",
                        "bench_storage=4g",
                        "",
                    )
                ),
                encoding="ascii",
            )
            (result_dir / "build-provenance.env").write_text(
                "\n".join(
                    (
                        "cachetag_build_input_sha256=cachetag-dirty-tree-hash",
                        "vinyl_build_input_sha256=vinyl-dirty-tree-hash",
                        "slash_build_input_sha256=none",
                        "",
                    )
                ),
                encoding="ascii",
            )
            config = SUMMARIZER.sweep_configuration(result_dir)
        self.assertEqual(config["worker_cap"], 16)
        self.assertEqual(config["thread_pools"], 2)
        self.assertTrue(config["cachetag_source_identity_recorded"])
        self.assertIn(("cachetag_build_input_sha256", "cachetag-dirty-tree-hash"), config["build"])
        self.assertIn(("vinyl_build_input_sha256", "vinyl-dirty-tree-hash"), config["build"])
        self.assertIn(("bench_effective_set_interning", "1"), config["match_identity"])
        self.assertIn(("objects", "1000000"), config["match_identity"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
