#!/usr/bin/env python3
"""Unit checks for fail-closed controlled-perf attachment construction."""

from pathlib import Path
import unittest

from run_with_controlled_perf import perf_stat_command


class ControlledPerfCommandTest(unittest.TestCase):
    def test_uses_explicit_tid_attachment(self) -> None:
        command = perf_stat_command("instructions", [17, 23], Path("out"), Path("control"), Path("ack"))
        self.assertIn("-t", command)
        self.assertEqual(command[command.index("-t") + 1], "17,23")
        self.assertNotIn("-p", command)

    def test_rejects_empty_or_invalid_tids(self) -> None:
        with self.assertRaises(ValueError):
            perf_stat_command("instructions", [], Path("out"), Path("control"), Path("ack"))
        with self.assertRaises(ValueError):
            perf_stat_command("instructions", [0], Path("out"), Path("control"), Path("ack"))


if __name__ == "__main__":
    unittest.main()
