#!/usr/bin/env python3
import unittest

from verify_decision_row import dispersion_percent


class DecisionQualificationTest(unittest.TestCase):
    def test_dispersion_exceeds_a_prefrozen_budget(self) -> None:
        self.assertGreater(dispersion_percent([100.0, 100.0, 103.0]), 2.0)

    def test_requires_exactly_three_positive_observations(self) -> None:
        with self.assertRaises(ValueError):
            dispersion_percent([100.0, 101.0])
        with self.assertRaises(ValueError):
            dispersion_percent([100.0, 0.0, 101.0])


if __name__ == "__main__":
    unittest.main()
