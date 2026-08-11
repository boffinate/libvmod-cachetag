#!/usr/bin/env python3
"""Docker-only regression tests for acknowledged load-phase perf stat."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "benchmarks" / "run_with_phase_stat.py"
sys.path.insert(0, str(REPO / "benchmarks"))
from run_with_phase_stat import parse_stat_rows


class PhaseStatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "linux":
            raise unittest.SkipTest("authoritative phase-stat tests require Linux")
        cls.tempdir = Path(tempfile.mkdtemp(prefix="cachetag-phase-stat-test-"))
        source = cls.tempdir / "vinyld.c"
        source.write_text(
            textwrap.dedent(
                r"""
                #include <errno.h>
                #include <signal.h>
                #include <stdio.h>
                #include <stdlib.h>
                #include <string.h>
                #include <sys/prctl.h>
                #include <sys/stat.h>
                #include <sys/types.h>
                #include <unistd.h>

                static int exists(const char *path) { return access(path, F_OK) == 0; }
                static int write_file(const char *path, const char *body) {
                    FILE *file = fopen(path, "w");
                    if (file == NULL) return 20;
                    fputs(body, file);
                    return fclose(file) == 0 ? 0 : 21;
                }
                int main(void) {
                    const char *dir = getenv("BENCH_PHASE_MARKER_DIR");
                    const char *prefix = getenv("BENCH_PHASE_MARKER_PREFIX");
                    const char *metrics = getenv("TEST_DRIVER_METRICS");
                    const char *mode = getenv("TEST_HELPER_MODE");
                    char start[1024], ready[1024], end[1024], request[1024];
                    if (!dir || !prefix || !metrics) return 2;
                    if (prctl(PR_SET_NAME,
                        mode && strcmp(mode, "missing") == 0 ? "not-cache" : "cache-main",
                        0, 0, 0) != 0) return 3;
                    snprintf(start, sizeof(start), "%s/%s.load.start", dir, prefix);
                    snprintf(ready, sizeof(ready), "%s/%s.load.ready", dir, prefix);
                    snprintf(end, sizeof(end), "%s/%s.load.end", dir, prefix);
                    snprintf(request, sizeof(request), "%s/%s.load.request-started", dir, prefix);
                    if (exists(ready)) return 4;
                    if (mode && strcmp(mode, "multiple") == 0) {
                        pid_t child = fork();
                        if (child == 0) { close(1); close(2); sleep(10); return 0; }
                    }
                    if (write_file(start, "event=start\n") != 0) return 5;
                    for (int n = 0; n < 500 && !exists(ready); n++) usleep(10000);
                    if (!exists(ready)) return 6;
                    if (write_file(request, "event=request-started\n") != 0) return 7;
                    if (write_file(metrics,
                        "driver_errors=0\n"
                        "driver_load_requests=7\n"
                        "driver_load_backend_objects=7\n"
                        "driver_load_backend_objects_expected=7\n") != 0) return 8;
                    if (mode && strcmp(mode, "noend") == 0) { sleep(10); return 0; }
                    if (write_file(end, "event=end\n") != 0) return 9;
                    if (mode && strcmp(mode, "identity") == 0) {
                        execl("/bin/sleep", "sleep", "10", (char *)NULL);
                        return 10;
                    }
                    usleep(100000);
                    return 0;
                }
                """
            ),
            encoding="utf-8",
        )
        subprocess.run(["cc", "-O2", "-o", str(cls.tempdir / "vinyld"), str(source)], check=True)
        fake_perf = cls.tempdir / "perf"
        fake_perf.write_text(
            textwrap.dedent(
                r"""
                #!/usr/bin/env python3
                import json, os, signal, sys, time
                if "--version" in sys.argv:
                    print("perf version fake")
                    raise SystemExit(0)
                behavior = os.environ.get("TEST_PERF_MODE", "ok")
                if behavior == "exit":
                    raise SystemExit(1)
                args = sys.argv[1:]
                output = args[args.index("-o") + 1]
                control = next(arg for arg in args if arg.startswith("--control=fifo:"))
                ctl, ack = control.split("fifo:", 1)[1].split(",", 1)
                signal.signal(signal.SIGINT, lambda *_: raise_exit())
                def rows():
                    events = [args[n + 1] for n, arg in enumerate(args) if arg == "-e"]
                    with open(output, "w", encoding="utf-8") as file:
                        for n, event in enumerate(events, 1):
                            file.write(json.dumps({"counter-value": str(n * 100), "unit": "", "event": event}) + ",\n")
                def raise_exit():
                    raise SystemExit(0)
                with open(ctl, "r", encoding="ascii") as control_file, open(ack, "w", encoding="ascii", buffering=1) as ack_file:
                    for line in control_file:
                        command = line.strip()
                        if behavior == "noack":
                            time.sleep(10)
                        if command == "enable":
                            ack_file.write("ack\n")
                        elif command == "disable":
                            rows()
                            ack_file.write("ack\n")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        fake_perf.chmod(0o755)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tempdir)

    def run_case(self, name: str, *, helper: str = "ok", perf: str = "ok", stale: bool = False) -> tuple[subprocess.CompletedProcess[str], Path]:
        case = self.tempdir / name
        markers = case / "markers"
        markers.mkdir(parents=True)
        if stale:
            (markers / "case.load.ready").write_text("stale\n", encoding="utf-8")
        metrics = case / "driver.metrics"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.tempdir}:{env['PATH']}",
                "TEST_DRIVER_METRICS": str(metrics),
                "TEST_HELPER_MODE": helper,
                "TEST_PERF_MODE": perf,
            }
        )
        command = [
            sys.executable, str(RUNNER),
            "--stat-output", str(case / "stat.json"),
            "--meta-output", str(case / "stat.meta"),
            "--marker-dir", str(markers),
            "--marker-prefix", "case",
            "--timeout", "0.5",
            "--ack-timeout", "0.3",
            "--driver-metrics", str(metrics),
            "--expected-requests", "7",
            "--", str(self.tempdir / "vinyld"),
        ]
        return subprocess.run(command, env=env, text=True, capture_output=True, timeout=4), case

    def test_acknowledged_success_and_stale_ready_removal(self) -> None:
        completed, case = self.run_case("success", stale=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((case / "markers/case.load.request-started").exists())
        meta = (case / "stat.meta").read_text(encoding="utf-8")
        self.assertIn("target_comm=cache-main", meta)
        self.assertIn("stat_rows=4", meta)
        self.assertIn("driver_load_requests=7", meta)

    def test_perf_attach_failure_is_fail_closed(self) -> None:
        completed, case = self.run_case("attach-failure", perf="exit")
        self.assertEqual(completed.returncode, 2)
        self.assertFalse((case / "markers/case.load.request-started").exists())

    def test_ack_timeout_is_fail_closed(self) -> None:
        completed, case = self.run_case("ack-timeout", perf="noack")
        self.assertEqual(completed.returncode, 2)
        self.assertFalse((case / "markers/case.load.request-started").exists())

    def test_multiple_targets_fail(self) -> None:
        completed, _ = self.run_case("multiple", helper="multiple")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("exactly one", completed.stderr)

    def test_missing_target_fails(self) -> None:
        completed, _ = self.run_case("missing", helper="missing")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("found 0", completed.stderr)

    def test_missing_end_fails(self) -> None:
        completed, _ = self.run_case("missing-end", helper="noend")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("phase end", completed.stderr)

    def test_identity_change_fails(self) -> None:
        completed, _ = self.run_case("identity", helper="identity")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("identity changed", completed.stderr)

    def test_counter_parser_rejects_duplicate_unsupported_and_nonfinite_values(self) -> None:
        events = ["task-clock", "instructions"]
        fixtures = {
            "duplicate": [
                {"event": "task-clock", "counter-value": "1"},
                {"event": "task-clock", "counter-value": "2"},
                {"event": "instructions", "counter-value": "3"},
            ],
            "unsupported": [
                {"event": "task-clock", "counter-value": "<not supported>"},
                {"event": "instructions", "counter-value": "3"},
            ],
            "nonfinite": [
                {"event": "task-clock", "counter-value": "NaN"},
                {"event": "instructions", "counter-value": "3"},
            ],
        }
        import json
        for name, rows in fixtures.items():
            with self.subTest(name=name):
                path = self.tempdir / f"parser-{name}.json"
                path.write_text(json.dumps(rows), encoding="utf-8")
                with self.assertRaises(ValueError):
                    parse_stat_rows(path, events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
