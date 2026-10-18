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


    def test_saturdays_and_sundays_split_the_weekends(self):
        # July 2026: the 4th is a Saturday, the 5th a Sunday.
        md = month_dates(2026, 7)
        self.assertEqual(md["saturdays"], [4, 11, 18, 25])
        self.assertEqual(md["sundays"], [5, 12, 19, 26])
        # `weekends` is still the union, in order, and nothing is dropped.
        self.assertEqual(md["weekends"], sorted(md["saturdays"] + md["sundays"]))
        for d in md["saturdays"]:
            self.assertEqual(calendar.weekday(2026, 7, d), 5)
        for d in md["sundays"]:
            self.assertEqual(calendar.weekday(2026, 7, d), 6)

    def test_month_dates_holiday_days(self):
        # April 2026: Chakri (6th, a Monday) and Songkran (13-15) are public
        # holidays on weekdays; the 4th/5th are a real Sat/Sun.
        md = month_dates(2026, 4, extra_holidays=[2, 99])
        public = {h["day"] for h in md["holidays"]}
        self.assertTrue(public)
        # holiday_days is exactly public holidays + (clamped) extras, sorted.
        self.assertEqual(md["holiday_days"], sorted(public | {2}))
        self.assertIn(2, md["holiday_days"])        # extra
        self.assertIn(6, md["holiday_days"])        # public holiday
        self.assertNotIn(99, md["holiday_days"])    # out-of-range extra clamped
        # Sat/Sun are NOT holidays: they live in `weekends` only.
        self.assertFalse(set(md["weekends"]) & set(md["holiday_days"]))
        # combined is still the full union, so nothing that reads it breaks.
        self.assertEqual(
            md["combined"],
            sorted(set(md["weekends"]) | set(md["holiday_days"])),
        )
        # And without extras, holiday_days is just the public holidays.
        self.assertEqual(month_dates(2026, 4)["holiday_days"], sorted(public))

    def test_holiday_on_a_weekend_is_in_both_lists(self):
        # 5 Dec 2026 is a Saturday AND a public holiday: it must appear in
        # weekends and holiday_days (the holiday band wins at solve time).
        md = month_dates(2026, 12)
        self.assertIn(5, md["weekends"])
        self.assertIn(5, md["holiday_days"])
        self.assertEqual(md["combined"].count(5), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
