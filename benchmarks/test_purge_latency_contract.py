#!/usr/bin/env python3
"""Fail-closed fixtures for the fixed-volume purge latency contract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from summarize_results import comparison_arm_cohort_validity, purge_latency_contract_validity


class PurgeLatencyContractTest(unittest.TestCase):
    digest = "a" * 64

    def valid_fixture(self, root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
        (root / "metadata.env").write_text(
            "\n".join(
                (
                    "benchmark_contract=persistent-purge-latency-screen-v1",
                    "bench_profile=bulk-purge-bursts",
                    "objects=10000",
                    "tags_per_object=4",
                    "bench_buckets=64",
                    "bench_purge_requests=100",
                    "bench_purge_keys_per_request=10",
                    "bench_purge_latency_expected_wal_records_per_header=10",
                    "bench_storage_kind=fellow",
                    "bench_cache_tag_persist=1",
                    "bench_cache_tag_wal_fsync=strict",
                    "run_xkey=0",
                    "run_noindex=0",
                    "runs=3",
                    "vinyl_build_profile=optimized",
                    "vinyl_build_cflags=-O2 -g -fno-omit-frame-pointer",
                    "bench_build_cflags=-O2 -g",
                    "purge_latency_cohort_fingerprint=" + "b" * 64,
                )
            ) + "\n",
            encoding="utf-8",
        )
        provenance = {
            "build_provenance_version": "6",
            "build_provenance_mode": "strict",
            "build_provenance_eligible": "1",
            "docker_image_id": "sha256:image",
            "harness_dirty_state": "clean",
            "cachetag_dirty_state": "clean",
            "vinyl_dirty_state": "clean",
            "slash_dirty_state": "clean",
        }
        for key in (
            "harness_input_sha256", "cachetag_build_input_sha256", "vinyl_build_input_sha256",
            "slash_build_input_sha256", "cachetag_binary_sha256", "vinyl_binary_sha256",
            "build_commands_sha256", "dockerfile_sha256",
        ):
            provenance[key] = self.digest
        provenance["slash_patch_set"] = "reference-fellow-14"
        (root / "build-provenance.env").write_text(
            "\n".join(f"{key}={value}" for key, value in provenance.items()) + "\n",
            encoding="utf-8",
        )
        driver = {
            "driver_errors": "0",
            "driver_load_requests": "10000",
            "driver_load_residency_validation": "full-hit",
            "driver_load_residency_requests": "10000",
            "driver_load_residency_hits": "10000",
            "driver_bulk_purge_requests": "100",
            "driver_bulk_purge_attempted_requests": "100",
            "driver_bulk_purge_completed_requests": "100",
            "driver_bulk_purge_published_requests": "100",
            "driver_bulk_purge_published": "true",
            "driver_bulk_purge_keys": "1000",
            "driver_bulk_purge_unique_keys": "64",
            "driver_bulk_purge_expected_deduplicated": "true",
            "driver_bulk_purge_validation": "sample-miss",
            "driver_bulk_purge_validation_requests": "960",
            "driver_bulk_purge_validation_hits": "0",
            "driver_bulk_purge_validation_misses": "960",
            "driver_bulk_purge_latency_sampling_method": "deterministic-reservoir-v1",
            "driver_bulk_purge_latency_sampling_limit": "100",
            "driver_bulk_purge_latency_sampling_seen": "100",
            "driver_bulk_purge_latency_sampling_dropped": "0",
            "driver_bulk_purge_latency_samples": "100",
            "driver_bulk_purge_latency_samples_path": "/results/cachetag_bulk_purge_bursts.driver_bulk_purge.latency_samples.tsv",
            "driver_bulk_purge_latency_p50_seconds": "0.001",
            "driver_bulk_purge_latency_p95_seconds": "0.001",
            "driver_bulk_purge_latency_p99_seconds": "0.001",
            "driver_bulk_purge_latency_max_seconds": "0.001",
        }
        (root / "cachetag_bulk_purge_bursts.run-1.driver_bulk_purge.latency_samples.tsv").write_text(
            "seconds\n" + "0.001000000\n" * 100, encoding="utf-8"
        )
        stats = {
            "CACHETAG.vcl1_tags_bench.purgemap_seq": 1000,
            "CACHETAG.vcl1_tags_bench.persist_wal_records": 1000,
            "CACHETAG.vcl1_tags_bench.persist_failures": 0,
            "CACHETAG.vcl1_tags_bench.persist_degraded": 0,
            "CACHETAG.vcl1_tags_bench.parse_errors": 0,
            "CACHETAG.vcl1_tags_bench.limit_rejections": 0,
        }
        time_values = {
            "swap_activity": "0",
            "system_sampler_status": "ok",
            "system_sampler_under_sampled": "0",
            "system_sampler_thread_stalled": "0",
        }
        return time_values, driver, stats

    def test_complete_contract_is_eligible_without_saturation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            time_values, driver, stats = self.valid_fixture(Path(temp))
            valid, reason = purge_latency_contract_validity(
                Path(temp), "cachetag_bulk_purge_bursts", 1, driver, stats, time_values
            )
        self.assertEqual((valid, reason), (1, "ok"))

    def test_missing_raw_sample_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            time_values, driver, stats = self.valid_fixture(root)
            (root / "cachetag_bulk_purge_bursts.run-1.driver_bulk_purge.latency_samples.tsv").unlink()
            valid, reason = purge_latency_contract_validity(root, "cachetag_bulk_purge_bursts", 1, driver, stats, time_values)
        self.assertEqual(valid, 0)
        self.assertIn("purge_latency_raw_samples_missing", reason)

    def test_wal_geometry_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            time_values, driver, stats = self.valid_fixture(root)
            stats["CACHETAG.vcl1_tags_bench.persist_wal_records"] = 100
            valid, reason = purge_latency_contract_validity(root, "cachetag_bulk_purge_bursts", 1, driver, stats, time_values)
        self.assertEqual(valid, 0)
        self.assertIn("purge_latency_wal_records_mismatch", reason)

    def test_scope_gates_reject_tampered_evidence(self) -> None:
        cases = (
            ("raw-count", lambda root, time, driver, stats: driver.__setitem__("driver_bulk_purge_latency_samples", "99"), "purge_latency_sampling_count_invalid"),
            ("dropped", lambda root, time, driver, stats: driver.__setitem__("driver_bulk_purge_latency_sampling_dropped", "1"), "purge_latency_sampling_count_invalid"),
            ("profile", lambda root, time, driver, stats: (root / "metadata.env").write_text((root / "metadata.env").read_text(encoding="utf-8").replace("bench_profile=bulk-purge-bursts", "bench_profile=uniform-tags"), encoding="utf-8"), "purge_latency_metadata_invalid:bench_profile"),
            ("fsync", lambda root, time, driver, stats: (root / "metadata.env").write_text((root / "metadata.env").read_text(encoding="utf-8").replace("bench_cache_tag_wal_fsync=strict", "bench_cache_tag_wal_fsync=grouped"), encoding="utf-8"), "purge_latency_metadata_invalid:bench_cache_tag_wal_fsync"),
            ("freshness", lambda root, time, driver, stats: driver.__setitem__("driver_bulk_purge_validation_hits", "1"), "purge_latency_freshness_invalid"),
            ("residency", lambda root, time, driver, stats: driver.__setitem__("driver_load_residency_hits", "9999"), "purge_latency_load_residency_invalid"),
            ("identities", lambda root, time, driver, stats: driver.__setitem__("driver_bulk_purge_unique_keys", "63"), "purge_latency_identity_set_invalid"),
            ("swap", lambda root, time, driver, stats: time.__setitem__("swap_activity", "1"), "purge_latency_swap_activity"),
            ("provenance", lambda root, time, driver, stats: (root / "build-provenance.env").write_text((root / "build-provenance.env").read_text(encoding="utf-8").replace("build_provenance_mode=strict", "build_provenance_mode=development"), encoding="utf-8"), "purge_latency_provenance_not_strict"),
        )
        for name, tamper, expected_reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                time_values, driver, stats = self.valid_fixture(root)
                tamper(root, time_values, driver, stats)
                valid, reason = purge_latency_contract_validity(root, "cachetag_bulk_purge_bursts", 1, driver, stats, time_values)
                self.assertEqual(valid, 0)
                self.assertIn(expected_reason, reason)

    def test_ordinary_contract_is_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "metadata.env").write_text("benchmark_contract=comparison-v1\n", encoding="utf-8")
            valid, reason = purge_latency_contract_validity(root, "cachetag_bulk_purge_bursts", 1, {}, {}, {})
        self.assertEqual((valid, reason), (1, "not_applicable"))

    def test_mismatched_latency_cohorts_reject_the_arm_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            for path in (left, right):
                (path / "metadata.env").write_text(
                    "benchmark_contract=persistent-purge-latency-screen-v1\n", encoding="utf-8"
                )
            arms = {
                "B": [{"path": str(left), "purge_latency_cohort_fingerprint": "a" * 64}],
                "P": [{"path": str(right), "purge_latency_cohort_fingerprint": "b" * 64}],
            }
            valid, reason = comparison_arm_cohort_validity(arms)
        self.assertEqual((valid, reason), (0, "purge_latency_cohort_fingerprint_changed_across_arms"))


if __name__ == "__main__":
    unittest.main()
