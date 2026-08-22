#!/usr/bin/env python3
"""Comparison-cohort material must ignore per-build and live sampler noise."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("comparison_cohort_material.sh")


class ComparisonCohortTest(unittest.TestCase):
    def material(
        self,
        system_extra: str = "",
        provenance_extra: str = "",
        cachetag_input: str = "c",
    ) -> str:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            system = root / "system.env"
            provenance = root / "provenance.env"
            system.write_text(
                "boot_id=boot\nhostname=host\ncpu_model=cpu\ncpu_topology=topology\n"
                "cpu_smt_siblings=siblings\ncpu_scaling_governors=performance\n"
                "cpu_boost_state=cpufreq:1\nkernel=kernel\nnproc=16\nmem_total_kb=64\n"
                + system_extra,
                encoding="utf-8",
            )
            provenance.write_text(
                f"vinyl_build_input_sha256=v\ncachetag_build_input_sha256={cachetag_input}\n"
                "xkey_build_input_sha256=x\nxkey_compat_artifact_sha256=compat\n"
                "xkey_config_sha256=config\ndockerfile_sha256=dockerfile\n"
                "docker_image_id=image\nbuild_cflags=-O2 -g\nbuild_cppflags=none\n"
                "build_ldflags=none\n"
                + provenance_extra,
                encoding="utf-8",
            )
            return subprocess.check_output(
                ["sh", str(SCRIPT), str(system), str(provenance)], text=True
            )

    def test_dynamic_frequency_and_binary_output_do_not_split_cohort(self) -> None:
        first = self.material(
            "cpu_frequency_state=cpu0:cur=3100000\n",
            "cachetag_binary_sha256=first\nbuild_commands_sha256=first\n",
        )
        second = self.material(
            "cpu_frequency_state=cpu0:cur=3200000\n",
            "cachetag_binary_sha256=second\nbuild_commands_sha256=second\n",
        )
        self.assertEqual(first, second)

    def test_invariant_source_input_changes_cohort(self) -> None:
        baseline = self.material()
        changed = self.material(cachetag_input="changed")
        self.assertNotEqual(baseline, changed)


if __name__ == "__main__":
    unittest.main()
