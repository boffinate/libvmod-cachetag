#!/usr/bin/env python3
"""The benchmark container body is a quoted string; check it as bash.

`sh -n scripts/benchmark-cachetag-vmod.sh` only parses the outer script. The
whole in-container program is one single-quoted argument to `bash -lc`, so a
stray single quote inside it silently hands a fragment to the *outer* shell
instead of failing the syntax check. Extract that argument and parse it.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark-cachetag-vmod.sh"
OPEN = "bash -lc '"
CLOSE = "\n'\n\nrm -f \"$docker_cidfile\""
# The container body re-opens the quote to interpolate an outer variable:
#   printf "image=%s\n" "'"$image"'"
INTERPOLATION = re.compile(r"'\"\$[A-Za-z_][A-Za-z0-9_]*\"'")
ESCAPED_QUOTE = "'\"'\"'"


def container_body(source: str) -> str:
    start = source.index(OPEN) + len(OPEN)
    end = source.index(CLOSE, start)
    return source[start:end]


class ContainerScriptSyntaxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SCRIPT.read_text(encoding="utf-8")
        self.body = container_body(self.source)

    def test_body_has_no_unquoted_single_quote(self) -> None:
        stripped = INTERPOLATION.sub("", self.body.replace(ESCAPED_QUOTE, ""))
        offenders = [line for line in stripped.split("\n") if "'" in line]
        self.assertEqual(
            offenders,
            [],
            "single quote in the container body escapes to the outer shell; "
            "use double quotes with \\$ instead",
        )

    def test_body_parses_as_bash(self) -> None:
        body = INTERPOLATION.sub("OUTER", self.body.replace(ESCAPED_QUOTE, "'"))
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(body)
            path = handle.name
        result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        Path(path).unlink()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_perf_stat_has_an_independent_complete_repetition_contract(self) -> None:
        self.assertIn("bench_perf_stat_runs=${BENCH_PERF_STAT_RUNS:-all}", self.source)
        self.assertIn("bench_perf_stat_workload=${BENCH_PERF_STAT_WORKLOAD:-}", self.source)
        self.assertIn(
            "bench_perf_stat_events=${BENCH_PERF_STAT_EVENTS:-instructions,cycles,task-clock}",
            self.source,
        )
        self.assertIn("should_perf_stat_run()", self.body)
        self.assertIn("perf_stat_enabled && should_perf_stat_run", self.body)
        self.assertIn('--stat-events "$BENCH_PERF_STAT_EVENTS"', self.body)
        self.assertNotIn("perf_stat_enabled && should_perf_record_run", self.body)
        self.assertNotIn('$3 ~ /^instructions/', self.body)

    def test_perf_stat_contract_is_recorded_in_metadata(self) -> None:
        for field in (
            "bench_perf_stat_runs",
            "bench_perf_stat_workload",
            "bench_perf_stat_events",
        ):
            self.assertIn(f'printf "{field}=%s\\n"', self.body)
        self.assertIn(
            'printf "bench_perf_stat_contract=%s|%s|%s|%s\\n" "$BENCH_PERF_STAT" "$BENCH_PERF_STAT_RUNS" "$BENCH_PERF_STAT_WORKLOAD" "$BENCH_PERF_STAT_EVENTS"',
            self.body,
        )

    def test_perf_stat_counted_ignores_tool_duration_rows(self) -> None:
        start = self.body.index("perf_stat_counted() {")
        end = self.body.index("\n}\n", start) + len("\n}\n")
        function = self.body[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked.csv"
            blocked.write_text(
                "<not counted>,,cycles,0,0.00\n1000000000,ns,duration_time,100.00,100.00\n",
                encoding="utf-8",
            )
            counted = Path(tmp) / "counted.csv"
            counted.write_text(
                "1500000000,,cycles,1000000000,100.00\n1000000000,ns,duration_time,100.00,100.00\n",
                encoding="utf-8",
            )
            command = function + '\nBENCH_PERF_STAT_EVENTS=cycles perf_stat_counted "$1"'
            blocked_result = subprocess.run(
                ["bash", "-c", command, "bash", str(blocked)], capture_output=True, text=True
            )
            counted_result = subprocess.run(
                ["bash", "-c", command, "bash", str(counted)], capture_output=True, text=True
            )
        self.assertNotEqual(blocked_result.returncode, 0)
        self.assertEqual(counted_result.returncode, 0, counted_result.stderr)


if __name__ == "__main__":
    unittest.main()
