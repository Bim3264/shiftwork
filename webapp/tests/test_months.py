"""Tests for webapp.core.months (month grouping helpers).

Run from the project root:
  python3 -m unittest webapp.tests.test_months
"""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.months import EN_MONTHS, THAI_MONTHS, parse_month_year  # noqa: E402


class ParseMonthYearTests(unittest.TestCase):

    def test_valid_and_zero_padded(self):
        self.assertEqual(parse_month_year({"month": "9", "year": "2026"}), (2026, 9))
        self.assertEqual(parse_month_year({"month": "09", "year": " 2026 "}), (2026, 9))
        self.assertEqual(parse_month_year({"month": 12, "year": 2026}), (2026, 12))

    def test_invalid_month_or_year_is_none(self):
        self.assertIsNone(parse_month_year({"month": "13", "year": "2026"}))
        self.assertIsNone(parse_month_year({"month": "0", "year": "2026"}))
        self.assertIsNone(parse_month_year({"month": "9", "year": "0"}))
        self.assertIsNone(parse_month_year({"month": "Sep", "year": "2026"}))

    def test_missing_is_none(self):
        self.assertIsNone(parse_month_year({}))
        self.assertIsNone(parse_month_year(None))
        self.assertIsNone(parse_month_year({"month": "9"}))

    def test_month_name_tables(self):
        self.assertEqual(len(THAI_MONTHS), 13)
        self.assertEqual(THAI_MONTHS[9], "กันยายน")
        self.assertEqual(EN_MONTHS[9], "September")


if __name__ == "__main__":
    unittest.main()
