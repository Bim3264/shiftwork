"""Tests for the ScheduleInput contract.

These check the behaviour the rest of the app depends on:
  - a real ward CSV parses into the expected structure,
  - the round-trip parse -> emit -> parse is lossless (upload and grid must
    agree on the same canonical form),
  - the emitted CSV is actually consumable by the existing DataImporter/solver,
  - validation rejects genuinely bad input.

Run from the project root:  python3 -m unittest webapp.tests.test_schedule_input
"""

import os
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from dataimporter import DataImporter  # noqa: E402

_REAL_CSV = os.path.join(_PROJECT_DIR, "input", "Schedule 1.csv")


class ScheduleInputParseTest(unittest.TestCase):

    def setUp(self):
        with open(_REAL_CSV, encoding="utf-8-sig") as f:
            self.text = f.read()
        self.parsed = ScheduleInput.from_csv(self.text)

    def test_parses_ward_and_settings(self):
        self.assertEqual(self.parsed.ward_meta.get("ward_name"), "Ward 4B")
        self.assertEqual(self.parsed.settings["num_days"], 31)
        self.assertEqual(
            self.parsed.settings["weekends"],
            [6, 7, 13, 14, 20, 21, 27, 28],
        )
        self.assertFalse(self.parsed.settings["allow_evening_to_night"])
        self.assertTrue(self.parsed.settings["head_nurse_special_shift"])
        # enable_meetings is tier-derived, not an input setting.
        self.assertNotIn("enable_meetings", self.parsed.settings)

    def test_parses_nurse_grid_with_raw_symbols(self):
        self.assertEqual(len(self.parsed.nurses), 12)
        nurse_a = self.parsed.nurses[0]
        self.assertEqual(nurse_a.name, "NurseA")
        self.assertEqual(nurse_a.type, "senior")
        # From the fixture: day 2 is a requested day off, day 3 is a DAY shift.
        self.assertEqual(nurse_a.shifts.get(2), "off")
        self.assertEqual(nurse_a.shifts.get(3), "ช")
        # Blank cells are simply absent.
        self.assertNotIn(1, nurse_a.shifts)

    def test_csv_round_trip_is_lossless(self):
        reparsed = ScheduleInput.from_csv(self.parsed.to_solver_csv())
        self.assertEqual(reparsed.settings, self.parsed.settings)
        self.assertEqual(reparsed.ward_meta, self.parsed.ward_meta)
        self.assertEqual(reparsed.nurses, self.parsed.nurses)

    def test_dict_round_trip_is_lossless(self):
        reloaded = ScheduleInput.from_dict(self.parsed.to_dict())
        self.assertEqual(reloaded.settings, self.parsed.settings)
        self.assertEqual(reloaded.ward_meta, self.parsed.ward_meta)
        self.assertEqual(reloaded.nurses, self.parsed.nurses)

    def test_emitted_csv_is_consumable_by_dataimporter(self):
        # The whole point of to_solver_csv() is that the solver's importer can
        # read it back. Feed it through DataImporter and confirm it parses.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(self.parsed.to_solver_csv())
            path = tmp.name
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertEqual(di.numberOfNurses(), 12)
        finally:
            os.unlink(path)


class ScheduleInputGridTest(unittest.TestCase):

    def test_grid_input_emits_consumable_csv(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "GridWard", "tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[
                Nurse(name="A", type="senior", shifts={2: "off"}),
                Nurse(name="B", type="new", shifts={1: "ช", 3: "บ"}),
            ],
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(si.to_solver_csv())
            path = tmp.name
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertEqual(di.numberOfNurses(), 2)
        finally:
            os.unlink(path)


class ScheduleInputValidationTest(unittest.TestCase):

    def _base(self, **over):
        # Two nurses so the baseline is genuinely valid (the senior-nurse rule
        # needs >=2); the rejection tests then mutate exactly one thing so each
        # failure is attributable to the property under test.
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name="A", shifts={1: "ช"}), Nurse(name="B")],
        )
        return si

    def test_valid_input_passes(self):
        self._base().validate()  # should not raise

    def test_rejects_day_out_of_range(self):
        si = self._base()
        si.nurses[0].shifts = {40: "ช"}  # num_days is 7
        with self.assertRaises(ValueError):
            si.validate()

    def test_rejects_unknown_token(self):
        si = self._base()
        si.nurses[0].shifts = {1: "X"}
        with self.assertRaises(ValueError):
            si.validate()

    def test_rejects_empty_roster(self):
        si = self._base()
        si.nurses = []
        with self.assertRaises(ValueError):
            si.validate()

    def test_rejects_single_nurse_when_senior_rule_on(self):
        # Guards the solver crash: head+deputy rule needs >=2 nurses.
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7],
                      "head_nurse_special_shift": True},
            nurses=[Nurse(name="Solo", shifts={1: "ช"})],
        )
        with self.assertRaises(ValueError):
            si.validate()

    def test_allows_single_nurse_when_senior_rule_off(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7],
                      "head_nurse_special_shift": False},
            nurses=[Nurse(name="Solo", shifts={1: "ช"})],
        )
        si.validate()  # should not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
