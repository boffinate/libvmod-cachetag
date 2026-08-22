#!/usr/bin/env python3
"""Deterministic fixture-loader checks; run in the benchmark Docker image."""

from __future__ import annotations

import unittest
from pathlib import Path

from fixture_loader import FixtureError, fixture_metrics, load_manifest, validate_fixture
from generate_benchmark_fixture import SCENARIOS, records_for


ROOT = Path(__file__).parent / "fixtures"


class FixtureLoaderTest(unittest.TestCase):
    def test_versioned_test_fixture_has_frozen_metrics(self) -> None:
        metrics = validate_fixture(ROOT / "cms-trace-static-v1-test.manifest.json")
        self.assertEqual(metrics["objects"], 3)
        self.assertEqual(metrics["relationships"], 6)
        self.assertEqual(metrics["max_fanout"], 2)

    def test_production_name_fails_closed_without_public_payload(self) -> None:
        with self.assertRaisesRegex(FixtureError, "not executable"):
            validate_fixture(ROOT / "cms-trace-static-v1.manifest.json")

    def test_production_manifest_records_no_access_or_purge_history(self) -> None:
        manifest = load_manifest(ROOT / "cms-trace-static-v1.manifest.json")
        self.assertIn("not be fabricated", str(manifest["payload_blocker"]))

    def test_synthetic_bounds_remain_valid_at_tiny_smoke_scale(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                rows = records_for(scenario, objects=2, tags_per_object=4)
                for row in rows:
                    self.assertEqual(len(row.tags), len(set(row.tags)))
                self.assertEqual(fixture_metrics(rows)["relationships"], 8)

    def test_bound_tags_match_the_declared_default_length_class(self) -> None:
        for scenario in ("mostly-unique-bound", "mostly-shared-bound"):
            with self.subTest(scenario=scenario):
                rows = records_for(scenario, objects=2, tags_per_object=4)
                lengths = [len(tag) for row in rows for tag in row.tags]
                self.assertGreaterEqual(min(lengths), 20)
                self.assertLessEqual(max(lengths), 50)


if __name__ == "__main__":
    unittest.main()
