"""Tests for month/weekend/holiday auto-detection."""

import calendar
import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.calendar_util import month_dates  # noqa: E402


class MonthDatesTest(unittest.TestCase):

    def test_num_days_and_weekends(self):
        md = month_dates(2026, 4)          # April 2026 has 30 days
        self.assertEqual(md["num_days"], 30)
        # Every listed weekend day really is a Sat/Sun.
        for d in md["weekends"]:
            self.assertGreaterEqual(calendar.weekday(2026, 4, d), 5)
        # February leap-year length.
        self.assertEqual(month_dates(2024, 2)["num_days"], 29)
        self.assertEqual(month_dates(2026, 2)["num_days"], 28)

    def test_thai_public_holidays_detected(self):
        md = month_dates(2026, 4)          # Chakri (6th), Songkran (13–15)
        holiday_days = {h["day"] for h in md["holidays"]}
        self.assertIn(6, holiday_days)
        self.assertTrue(holiday_days & {13, 14, 15})
        # Holidays land in combined even when they fall on a weekday.
        self.assertIn(6, md["combined"])

    def test_extra_holidays_merged_and_clamped(self):
        md = month_dates(2026, 4, extra_holidays=[2, 99])  # 99 is out of range
        self.assertIn(2, md["combined"])
        self.assertNotIn(99, md["combined"])
        # combined is the union of weekends + holidays + extras, sorted unique.
        self.assertEqual(md["combined"], sorted(set(md["combined"])))
        self.assertTrue(set(md["weekends"]).issubset(set(md["combined"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
