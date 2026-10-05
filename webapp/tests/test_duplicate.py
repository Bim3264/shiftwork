"""Tests for webapp.core.duplicate (next_month_input — "next month from this").

Run from the project root:
  python3 -m unittest webapp.tests.test_duplicate
"""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.calendar_util import month_dates  # noqa: E402
from webapp.core.duplicate import next_month_input  # noqa: E402
from webapp.core.schedule_input import Nurse, ScheduleInput  # noqa: E402


def _si(ward_meta=None, settings=None, nurses=None):
    return ScheduleInput(
        ward_meta=dict(ward_meta or {}),
        settings=ScheduleInput._coerce_settings(dict(settings or {})),
        nurses=list(nurses or []),
    )


class NextMonthInputTests(unittest.TestCase):

    def test_next_month_advances_and_recomputes_dates(self):
        si = _si(
            ward_meta={"month": "9", "year": "2026"},
            settings={"num_days": 30, "extra_holidays": [3]},
        )
        new = next_month_input(si)
        self.assertEqual(new.ward_meta["month"], "10")
        self.assertEqual(new.ward_meta["year"], "2026")
        self.assertEqual(new.settings["num_days"], 31)
        self.assertEqual(new.settings["weekends"], month_dates(2026, 10, [])["combined"])
        self.assertEqual(new.settings["extra_holidays"], [])

    def test_next_month_rolls_year(self):
        si = _si(ward_meta={"month": "12", "year": "2026"})
        new = next_month_input(si)
        self.assertEqual(new.ward_meta["month"], "1")
        self.assertEqual(new.ward_meta["year"], "2027")
        self.assertEqual(new.settings["num_days"], 31)

    def test_next_month_clears_requests_keeps_roster(self):
        nurses = [
            Nurse(name="A", type="senior", shifts={1: "ช"}, active=True),
            Nurse(name="B", type="new", shifts={2: "vac"}, active=False),
        ]
        si = _si(ward_meta={"month": "9", "year": "2026"}, nurses=nurses)
        new = next_month_input(si)
        self.assertEqual([n.name for n in new.nurses], ["A", "B"])
        self.assertEqual([n.type for n in new.nurses], ["senior", "new"])
        self.assertEqual([n.active for n in new.nurses], [True, False])
        self.assertTrue(all(n.shifts == {} for n in new.nurses))

    def test_next_month_keeps_settings(self):
        si = _si(
            ward_meta={"month": "9", "year": "2026"},
            settings={
                "coverage_weekday_day": 7,
                "min_request_percent": 80,
                "head_nurse_special_shift": False,
            },
        )
        new = next_month_input(si)
        self.assertEqual(new.settings["coverage_weekday_day"], 7)
        self.assertEqual(new.settings["min_request_percent"], 80)
        self.assertFalse(new.settings["head_nurse_special_shift"])

    def test_next_month_undated_copies_unchanged(self):
        si = _si(
            ward_meta={"ward_name": "Ward 4B"},
            settings={"num_days": 15, "weekends": [1, 2]},
            nurses=[Nurse(name="A", shifts={1: "off"})],
        )
        new = next_month_input(si)
        self.assertEqual(new.ward_meta, si.ward_meta)
        self.assertEqual(new.settings["num_days"], 15)
        self.assertEqual(new.settings["weekends"], [1, 2])
        self.assertEqual(new.nurses[0].shifts, {})

    def test_next_month_does_not_mutate_source(self):
        nurses = [Nurse(name="A", type="senior", shifts={1: "ช"}, active=True)]
        si = _si(
            ward_meta={"month": "9", "year": "2026"},
            settings={"num_days": 30, "extra_holidays": [3]},
            nurses=nurses,
        )
        before_ward_meta = dict(si.ward_meta)
        before_settings = dict(si.settings)
        before_shifts = dict(si.nurses[0].shifts)
        next_month_input(si)
        self.assertEqual(si.ward_meta, before_ward_meta)
        self.assertEqual(si.settings, before_settings)
        self.assertEqual(si.nurses[0].shifts, before_shifts)

    def test_next_month_round_trips_through_solver_csv(self):
        # Known failure mode 6 (input contract): the new input must round-trip.
        si = _si(
            ward_meta={"month": "9", "year": "2026", "ward_name": "Ward 4B"},
            settings={"num_days": 30},
            nurses=[Nurse(name="A", type="senior", shifts={1: "ช"}, active=True)],
        )
        new = next_month_input(si)
        reparsed = ScheduleInput.from_dict(new.to_dict())
        reparsed.to_solver_csv()  # must not raise


if __name__ == "__main__":
    unittest.main()
