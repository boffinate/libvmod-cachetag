#!/usr/bin/env python3
"""Contract checks for the interned-set lifecycle workload generator."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


GENERATOR = Path(__file__).with_name("generate_intern_lifecycle_vtc.py")


class InternLifecycleGeneratorTest(unittest.TestCase):
    def generate(
        self,
        directory: Path,
        folds: str = "5,64,256",
        set_header_bytes: int = 24,
    ) -> None:
        subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--out-dir",
                str(directory),
                "--objects",
                "16",
                "--folds",
                folds,
                "--set-header-bytes",
                str(set_header_bytes),
                "--expect-benchmark-timing",
            ],
            check=True,
        )

    def test_unique_growth_and_final_reference_drain_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.generate(directory)
            text = (directory / "cachetag_intern_lifecycle.vtc").read_text(encoding="ascii")
            for folds in (5, 64, 256):
                with self.subTest(folds=folds):
                    self.assertIn("-p debug=none", text)
                    self.assertIn("interning = true", text)
                    self.assertIn("sweep_interval = 0s", text)
                    self.assertIn(f"low-fanout-unique {folds - 1} intern:lifecycle:drain", text)
                    self.assertIn("BENCH_VALIDATE_TAG_SHAPE=1", text)
                    self.assertIn("volatile_interned_table_grow_calls > 0", text)
                    self.assertIn(f"volatile_edges == {16 * folds}", text)
                    self.assertIn(f"volatile_interned_set_bytes == {16 * (24 + 8 * folds)}", text)
                    self.assertIn("expect resp.http.Compacted == 16", text)
                    self.assertIn("volatile_interned_set_bytes == 0", text)
                    self.assertIn(".drain.start", text)
                    self.assertIn(".drain.end", text)
            self.assertIn("interning-shared-five 5 intern:lifecycle:drain", text)
            self.assertIn("volatile_interned_sets == 1", text)
            self.assertIn("volatile_interned_set_refs == 16", text)
            self.assertIn("volatile_interned_set_hits == 15", text)

    def test_each_stage_restarts_with_its_own_growth_and_miss_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.generate(directory)
            workload = (directory / "cachetag_intern_lifecycle.vtc").read_text(encoding="ascii")
        restart = (
            "vinyl v1 -stop\n"
            'shell "vinyladm -n ${v1_name} panic.clear || true"\n'
            "vinyl v1 -start\n"
        )
        self.assertEqual(workload.count(restart), 3)
        self.assertEqual(workload.count("volatile_interned_table_grow_calls > 0"), 3)
        self.assertEqual(workload.count("volatile_interned_set_misses == 16"), 3)
        self.assertIn("volatile_interned_set_misses == 1", workload)
        self.assertNotIn("volatile_interned_set_misses == 32", workload)
        self.assertNotIn("volatile_interned_set_misses == 48", workload)

    def test_manifest_marks_the_scope_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.generate(directory, "5")
            manifest = (directory / "intern-lifecycle.env").read_text(encoding="ascii")
            self.assertIn("workload_class=development-screen", manifest)
            self.assertIn("excluded_storage=fellow", manifest)
            self.assertIn("excluded_behavior=concurrent_warm_hits_during_drain", manifest)

    def test_header_geometry_distinguishes_baseline_and_candidate(self) -> None:
        for set_header_bytes in (32, 24):
            with self.subTest(set_header_bytes=set_header_bytes):
                with tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    self.generate(
                        directory,
                        folds="5",
                        set_header_bytes=set_header_bytes,
                    )
                    workload = (
                        directory / "cachetag_intern_lifecycle.vtc"
                    ).read_text(encoding="ascii")
                    manifest = (
                        directory / "intern-lifecycle.env"
                    ).read_text(encoding="ascii")
                expected_bytes = 16 * (set_header_bytes + 8 * 5)
                self.assertIn(
                    f"volatile_interned_set_bytes == {expected_bytes}",
                    workload,
                )
                self.assertIn(
                    f"set_header_bytes={set_header_bytes}\n",
                    manifest,
                )

    def test_manifest_hashes_the_rendered_workload_with_nul_framing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.generate(directory, folds="5")
            workload = directory / "cachetag_intern_lifecycle.vtc"
            manifest = dict(
                line.split("=", 1)
                for line in (directory / "intern-lifecycle.env")
                .read_text(encoding="ascii")
                .splitlines()
            )
            workload_digest = hashlib.sha256(workload.read_bytes()).hexdigest()
            material = f"{workload.name}\0{workload_digest}\0".encode("ascii")
            expected_digest = hashlib.sha256(material).hexdigest()
        self.assertEqual(
            manifest["rendered_workloads_sha256"],
            expected_digest,
        )

    def test_invalid_fold_count_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(GENERATOR), "--out-dir", tmp, "--folds", "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("[2, 512]", result.stderr)


if __name__ == "__main__":
    unittest.main()
