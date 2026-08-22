#!/usr/bin/env python3
"""Generator-level contract checks; execute in the benchmark Docker image."""

from __future__ import annotations

from io import StringIO
import unittest

from generate_cachetag_benchmark_vtc import ACTIVE_FIXTURE_CONTRACTS, write_cachetag_vcl, write_driver, write_xkey_vcl


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


if __name__ == "__main__":
    unittest.main()
