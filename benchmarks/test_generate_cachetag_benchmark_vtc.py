#!/usr/bin/env python3
"""Generator-level contract checks; execute in the benchmark Docker image."""

from __future__ import annotations

from io import StringIO
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from generate_cachetag_benchmark_vtc import (
    ACTIVE_FIXTURE_CONTRACTS,
    VSC_FLUSH_CLIENT_NAME_MAX,
    write_cachetag_vcl,
    write_driver,
    write_noindex_vcl,
    write_stats_counter_unchanged,
    write_xkey_vcl,
)

GENERATOR = Path(__file__).with_name("generate_cachetag_benchmark_vtc.py")
STALE_DELIVER_BLOCK = "\tsub vcl_deliver {\n\t\tif (tags.stale()) {\n\t\t\treturn (restart);\n\t\t}\n"


def cachetag_vcl(implementation: str = "cachetag") -> str:
    out = StringIO()
    write_cachetag_vcl(
        out, implementation, "explicit-purge", "256m", "24h", 4, "127.0.0.1", 18080,
        "default", "1G", "1M", "64K", "1G", 0, "", "", "", False, "strict",
    )
    return out.getvalue()


def generate_workloads(directory: Path, env: dict[str, str], profiles: str) -> None:
    child_env = dict(os.environ)
    child_env.update(env)
    subprocess.run(
        [
            sys.executable, str(GENERATOR),
            "--out-dir", str(directory),
            "--objects", "16",
            "--profile", profiles,
            "--include-xkey",
        ],
        check=True,
        env=child_env,
    )


def generate_sparse_restart(directory: Path, runtime_interning: str = "1") -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.update(
        {
            "BENCH_CODE_GENERATION": "runtime",
            "BENCH_RUNTIME_SET_INTERNING": runtime_interning,
            "BENCH_RESTART_TAG_PROFILE": "interning-unique-five",
            "BENCH_RESTART_TOUCH_PERCENT": "1",
        }
    )
    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--out-dir",
            str(directory),
            "--objects",
            "100",
            "--tags-per-object",
            "5",
            "--profile",
            "fellow-restart-sparse-interning",
            "--storage-kind",
            "fellow",
            "--cachetag-persist",
            "--slash-vmod-path",
            "/work/libvmod_slash.so",
            "--skip-noindex",
        ],
        check=False,
        env=child_env,
        text=True,
        capture_output=True,
    )


class MatchedArmVclTest(unittest.TestCase):
    def test_xkey_stores_but_never_delivers_the_canonical_header(self) -> None:
        out = StringIO()
        write_xkey_vcl(out, "256m", "24h", 4, "127.0.0.1", 18080, "default", "1G", "1M", "64K", "1G", 0, "", "", "")
        vcl = out.getvalue()
        self.assertIn("set beresp.http.xkey = bereq.http.X-Cache-Tags;", vcl)
        self.assertIn("unset resp.http.xkey;", vcl)
        self.assertIn("xkey.softpurge(req.http.Key)", vcl)
        self.assertIn("xkey.purge(req.http.Key)", vcl)
        self.assertIn("set beresp.ttl = 24h;", vcl)
        self.assertIn("set beresp.grace = 0s;", vcl)
        self.assertIn("set beresp.keep = 0s;", vcl)

    def test_trace_access_lanes_are_explicit(self) -> None:
        for profile, expected in (
            ("cms-trace-static-v1", "BENCH_ACCESS_PATTERN=uniform-cyclic"),
            ("cms-trace-hot-set-v1", "BENCH_ACCESS_PATTERN=hot-set"),
            ("ordinary-body-4k", "BENCH_ACCESS_PATTERN=uniform-cyclic"),
        ):
            with self.subTest(profile=profile):
                out = StringIO()
                write_driver(out, 16, "xkey-load", profile, 4, "trace", "/driver")
                command = out.getvalue()
                self.assertIn(expected, command)
                if profile == "cms-trace-hot-set-v1":
                    self.assertIn("BENCH_HOT_SET_OBJECTS=0", command)
                    self.assertNotIn("${BENCH_HOT_SET_OBJECTS}", command)

    def test_fixture_contract_selects_a_real_canonical_purge_key(self) -> None:
        ACTIVE_FIXTURE_CONTRACTS["hot-set"] = {
            "name": "hot-set",
            "fixture_fingerprint": "sha256:" + "a" * 64,
            "objects": 2,
            "relationships": 4,
            "payload": "/results/fixtures/hot-set.jsonl",
            "purge_key": "hot:0",
        }
        try:
            out = StringIO()
            write_driver(out, 2, "xkey-purge", "hot-set", 2, "fixture", "/driver")
            self.assertIn(" hot:0 /results/fixture.driver", out.getvalue())
            self.assertNotIn(" z:1 /results/fixture.driver", out.getvalue())
        finally:
            ACTIVE_FIXTURE_CONTRACTS.pop("hot-set", None)

    def test_cachetag_has_the_same_expiry_lifecycle(self) -> None:
        out = StringIO()
        write_cachetag_vcl(out, "cachetag", "explicit-purge", "256m", "24h", 4, "127.0.0.1", 18080, "default", "1G", "1M", "64K", "1G", 0, "", "", "", False, "strict")
        vcl = out.getvalue()
        self.assertIn("tags.add_header(bereq.http.X-Cache-Tags, sep = \" \");", vcl)
        self.assertIn("set beresp.ttl = 24h;", vcl)
        self.assertIn("set beresp.grace = 0s;", vcl)
        self.assertIn("set beresp.keep = 0s;", vcl)

    def test_resident_probe_converts_a_miss_to_a_non_success_response_in_all_arms(self) -> None:
        cachetag = cachetag_vcl()
        xkey = StringIO()
        write_xkey_vcl(
            xkey, "256m", "24h", 4, "127.0.0.1", 18080, "default",
            "1G", "1M", "64K", "1G", 0, "", "", "",
        )
        noindex = StringIO()
        write_noindex_vcl(
            noindex, "256m", 4, "127.0.0.1", 18080, "default",
            "1G", "1M", "64K", "1G", 0, "", "", "",
        )
        expected = 'if (req.http.X-Bench-Resident-Probe == "1") {\n\t\t\treturn (synth(503));'
        for arm, vcl in (("cachetag", cachetag), ("xkey", xkey.getvalue()), ("noindex", noindex.getvalue())):
            with self.subTest(arm=arm):
                self.assertIn(expected, vcl)


class StaleDeliverKnobTest(unittest.TestCase):
    """BENCH_STALE_DELIVER selects the documented two-call `stale()` shape."""

    def setUp(self) -> None:
        self.previous = os.environ.pop("BENCH_STALE_DELIVER", None)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        os.environ.pop("BENCH_STALE_DELIVER", None)
        if self.previous is not None:
            os.environ["BENCH_STALE_DELIVER"] = self.previous

    def test_default_is_the_one_call_shape(self) -> None:
        vcl = cachetag_vcl()
        self.assertIn("\tsub vcl_hit {\n\t\tif (tags.stale()) {\n", vcl)
        self.assertNotIn(STALE_DELIVER_BLOCK, vcl)
        self.assertEqual(vcl.count("tags.stale()"), 1)

    def test_knob_adds_the_usage_md_deliver_check(self) -> None:
        os.environ["BENCH_STALE_DELIVER"] = "1"
        vcl = cachetag_vcl()
        # USAGE.md places the same check in vcl_hit and vcl_deliver and bounds
        # restarts with Vinyl's max_restarts default, adding no guard of its own.
        self.assertIn(STALE_DELIVER_BLOCK, vcl)
        self.assertEqual(vcl.count("tags.stale()"), 2)
        self.assertEqual(vcl.count("return (restart);"), 2)

    def test_matched_arms_never_acquire_the_deliver_check(self) -> None:
        os.environ["BENCH_STALE_DELIVER"] = "1"
        xkey = StringIO()
        write_xkey_vcl(
            xkey, "256m", "24h", 4, "127.0.0.1", 18080, "default",
            "1G", "1M", "64K", "1G", 0, "", "", "",
        )
        noindex = StringIO()
        write_noindex_vcl(
            noindex, "256m", 4, "127.0.0.1", 18080, "default",
            "1G", "1M", "64K", "1G", 0, "", "", "",
        )
        self.assertNotIn("stale()", xkey.getvalue())
        self.assertNotIn("return (restart)", xkey.getvalue())
        self.assertNotIn("stale()", noindex.getvalue())
        self.assertNotIn("return (restart)", noindex.getvalue())


class StatsCaptureFlushTest(unittest.TestCase):
    """Every cachetag capture publishes before the single-shot vinylstat read."""

    PROFILES = (
        "low-fanout-unique,explicit-purge,populated-map-warm,"
        "phase4-sweep-latency,phase5-held-short,eviction"
    )

    def assert_captures_are_flushed(self, path: Path) -> None:
        lines = path.read_text(encoding="ascii").splitlines()
        captures = [
            n
            for n, line in enumerate(lines)
            if "vinylstat -1 -n ${v1_name} -f " in line and line.rstrip().endswith('.stats"')
        ]
        self.assertTrue(captures, f"{path.name} has no stats capture")
        for index in captures:
            window = "\n".join(lines[max(0, index - 5) : index])
            self.assertIn(
                "txreq -url /__bench_objects",
                window,
                f"{path.name}:{index + 1} captures without a VSC flush",
            )

    def test_cachetag_workloads_flush_before_every_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            generate_workloads(directory, {}, self.PROFILES)
            cachetag = sorted(directory.glob("cachetag_*.vtc"))
            self.assertTrue(cachetag)
            for path in cachetag:
                with self.subTest(workload=path.name):
                    self.assert_captures_are_flushed(path)

    def test_flush_client_names_fit_the_vtc_dump_prefix_buffer(self) -> None:
        # vtc_dump() formats "<lead> <id> <pfx>|" into char buf[64] with a pfx
        # as wide as "http[%2d] ". A long client name makes vinyltest assert
        # mid-run (SIGABRT) instead of failing the workload cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            generate_workloads(directory, {}, self.PROFILES)
            names = set()
            for path in sorted(directory.glob("*.vtc")):
                for line in path.read_text(encoding="ascii").splitlines():
                    if line.startswith("client "):
                        names.add(line.split()[1])
        self.assertTrue(names)
        for name in sorted(names):
            with self.subTest(client=name):
                self.assertLessEqual(len(name), VSC_FLUSH_CLIENT_NAME_MAX)

    def test_flush_client_names_restart_in_every_workload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            generate_workloads(directory, {}, self.PROFILES)
            for path in sorted(directory.glob("cachetag_*.vtc")):
                text = path.read_text(encoding="ascii")
                with self.subTest(workload=path.name):
                    if "c_vscflush_" in text:
                        self.assertIn("client c_vscflush_01 {", text)

    def test_matched_arms_do_not_probe_a_namespace_they_do_not_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            generate_workloads(directory, {}, self.PROFILES)
            for path in sorted(directory.glob("xkey_*.vtc")) + sorted(
                directory.glob("noindex_*.vtc")
            ):
                with self.subTest(workload=path.name):
                    self.assertNotIn(
                        "/__bench_objects", path.read_text(encoding="ascii")
                    )


class SparseFellowRestartTest(unittest.TestCase):
    def test_counter_comparison_uses_a_vtc_shell_block(self) -> None:
        out = StringIO()
        write_stats_counter_unchanged(
            out,
            "/results/before.stats",
            "/results/after.stats",
            "c_dsk_obj_get",
        )
        block = out.getvalue()
        self.assertTrue(block.startswith("shell {\n"))
        self.assertTrue(block.endswith("}\n"))
        self.assertNotIn('shell "', block)
        self.assertNotIn(r"\.", block)
        self.assertIn("$1 ~ /[.]c_dsk_obj_get$/", block)
        self.assertIn("if (!found) exit 2", block)
        self.assertIn(
            "counter c_dsk_obj_get absent from /results/before.stats",
            block,
        )
        self.assertIn('test "$before" = "$after" || {', block)

    def test_sparse_restart_proves_hit_purge_and_miss_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = generate_sparse_restart(directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            workload = (
                directory / "cachetag_fellow_restart_sparse_interning.vtc"
            ).read_text(encoding="ascii")

        self.assertIn("interning = true", workload)
        self.assertIn(" 1 cachetag-sparse-hit-probe interning-unique-five 5 ", workload)
        self.assertIn(
            "BENCH_OBJECT_START=1 "
            "/work/cachetag-http-workload-driver ${v1_addr} ${v1_port} 1 "
            "cachetag-sparse-purge interning-unique-five 5",
            workload,
        )
        self.assertIn("cachetag-sparse-miss-probe interning-unique-five 5", workload)
        self.assertIn("vinyl v1 -expect MAIN.backend_req == 0", workload)
        self.assertIn("vinyl v1 -expect MAIN.backend_req == 1", workload)
        self.assertIn("vinyl v1 -expect MAIN.n_vampireobject == 100", workload)
        self.assertEqual(workload.count("vinyl v1 -expect MAIN.n_vampireobject"), 1)
        self.assertIn("vinyl v1 -expect FELLOW.fellow.c_dsk_obj_get == 0", workload)
        self.assertIn("vinyl v1 -expect FELLOW.fellow.c_dsk_obj_get == 1", workload)
        self.assertIn("vinyl v1 -expect FELLOW.fellow.c_dsk_obj_get == 2", workload)
        self.assertIn("purgemap_fellow_direct_probes == 2", workload)
        self.assertIn("counter c_dsk_obj_get changed", workload)
        self.assertGreaterEqual(
            workload.count("volatile_interned_table_bytes == 0"), 4
        )

    def test_sparse_restart_refuses_disabled_interning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_sparse_restart(Path(tmp), runtime_interning="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "requires runtime interning enabled", result.stderr
        )


if __name__ == "__main__":
    unittest.main()
