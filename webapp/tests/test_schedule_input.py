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


class ScheduleInputActiveTest(unittest.TestCase):

    def _three(self, active_flags):
        return ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7],
                      "head_nurse_special_shift": True},
            nurses=[Nurse(name=f"N{i}", type="senior", active=a)
                    for i, a in enumerate(active_flags)],
        )

    def test_active_round_trips(self):
        si = self._three([True, False, True])
        reloaded = ScheduleInput.from_dict(si.to_dict())
        self.assertEqual([n.active for n in reloaded.nurses], [True, False, True])

    def test_from_dict_defaults_active_true_for_old_data(self):
        # Data saved before the feature has no "active" key.
        data = {"ward_meta": {}, "settings": {"num_days": 7},
                "nurses": [{"name": "N", "type": "", "shifts": {}}]}
        si = ScheduleInput.from_dict(data)
        self.assertTrue(si.nurses[0].active)

    def test_to_solver_csv_excludes_disabled(self):
        si = self._three([True, False, True])          # middle nurse disabled
        reparsed = ScheduleInput.from_csv(si.to_solver_csv())
        self.assertEqual(len(reparsed.nurses), 2)      # only the 2 active ones
        self.assertEqual([n.name for n in reparsed.nurses], ["N0", "N2"])

    def test_validate_counts_active_not_total(self):
        # Two nurses but only one active -> senior rule needs 2 active -> raises.
        si = self._three([True, False])
        with self.assertRaises(ValueError):
            si.validate()
        # Re-enable the second -> valid.
        si.nurses[1].active = True
        si.validate()


class SoftRequestSettingsTest(unittest.TestCase):

    def test_settings_round_trip_and_consumable_by_importer(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7],
                      "min_request_percent": 70, "relax_days_off": True},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertEqual(si.settings["min_request_percent"], 70)
        self.assertTrue(si.settings["relax_days_off"])

        reparsed = ScheduleInput.from_csv(si.to_solver_csv())
        self.assertEqual(reparsed.settings["min_request_percent"], 70)
        self.assertTrue(reparsed.settings["relax_days_off"])

        # The solver's importer must read them from the emitted CSV.
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertEqual(di.settings["min_request_percent"], 70)
            self.assertTrue(di.settings["relax_days_off"])
        finally:
            os.unlink(path)

    def test_coverage_settings_round_trip_and_consumable(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 7, "coverage_weekday_day": 6,
                      "coverage_weekend_night": 1},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertEqual(si.settings["coverage_weekday_day"], 6)
        self.assertEqual(si.settings["coverage_weekend_night"], 1)
        # defaults fill the rest
        self.assertEqual(si.settings["coverage_weekday_night"], 3)

        reparsed = ScheduleInput.from_csv(si.to_solver_csv())
        self.assertEqual(reparsed.settings["coverage_weekday_day"], 6)
        self.assertEqual(reparsed.settings["coverage_weekend_night"], 1)

        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertEqual(di.settings["coverage_weekday_day"], 6)
            self.assertEqual(di.settings["coverage_weekend_night"], 1)
        finally:
            os.unlink(path)

    def test_extra_holidays_round_trip(self):
        si = ScheduleInput.from_grid(
            ward_meta={}, settings={"num_days": 30, "extra_holidays": [2, 5]},
            nurses=[Nurse(name="A")],
        )
        self.assertEqual(si.settings["extra_holidays"], [2, 5])
        self.assertEqual(
            ScheduleInput.from_csv(si.to_solver_csv()).settings["extra_holidays"], [2, 5])
        self.assertEqual(
            ScheduleInput.from_dict(si.to_dict()).settings["extra_holidays"], [2, 5])

    def test_percent_is_clamped(self):
        si = ScheduleInput.from_grid(
            ward_meta={}, settings={"num_days": 7, "min_request_percent": 150},
            nurses=[Nurse(name="A")],
        )
        self.assertEqual(si.settings["min_request_percent"], 100)


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


class VacationSettingsTest(unittest.TestCase):
    """The vacation shift type: the 'vac' token and the enforce_vacation flag
    must survive the grid -> CSV -> solver-importer round-trip."""

    def test_enforce_vacation_defaults_true_and_round_trips(self):
        # Default: absent from the grid settings -> True.
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertTrue(si.settings["enforce_vacation"],
                        "enforce_vacation should default to True")
        reparsed = ScheduleInput.from_csv(si.to_solver_csv())
        self.assertTrue(reparsed.settings["enforce_vacation"])

    def test_enforce_vacation_false_round_trips_to_importer(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7], "enforce_vacation": False},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertFalse(si.settings["enforce_vacation"])

        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertFalse(di.settings["enforce_vacation"])
        finally:
            os.unlink(path)

    def test_vac_token_survives_round_trip_to_reqvacations(self):
        # A nurse asks for a vacation on day 3 and an ordinary off on day 4.
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[
                Nurse(name="A", type="senior"),
                Nurse(name="B", type="senior"),
                Nurse(name="C", type="", shifts={3: "vac", 4: "off"}),
            ],
        )
        # Grid -> CSV keeps the raw 'vac' cell.
        self.assertIn(",vac,", si.to_solver_csv())

        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            di = DataImporter(path, 5, 5).transform()
            # 0-based: day 3 -> index 2 as a vacation; day 4 -> index 3 as an off.
            self.assertEqual(di.reqVacations[2], [2],
                             "nurse C's vacation day should land in reqVacations")
            self.assertEqual(di.reqDayOff[2], [3],
                             "nurse C's off day should stay in reqDayOff, separate from vac")
        finally:
            os.unlink(path)


class FromXlsxTests(unittest.TestCase):
    """from_xlsx parses the Excel roster template (option 2: no CSV round-trip)."""

    def _make_xlsx(self, *, tier="ward", num_days_cell=5, with_days_cell=True):
        import io, openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Roster"
        # WARD INFO card (labels col A, values col C)
        ws["A5"]="Ward name / ชื่อวอร์ด";   ws["C5"]="Test Ward"
        ws["A6"]="Hospital / โรงพยาบาล";     ws["C6"]="Test Hospital"
        ws["A7"]="Head nurse / หัวหน้าเวร";  ws["C7"]="Boss"
        ws["A9"]="Month / เดือน";            ws["C9"]=7
        ws["A10"]="Year / ปี";               ws["C10"]=2026
        ws["A11"]="Plan / แพ็กเกจ";          ws["C11"]=tier
        # SETTINGS card (labels col H, values col P)
        if with_days_cell:
            ws["H5"]="Days in month / จำนวนวัน"; ws["P5"]=num_days_cell
        ws["H6"]="Rest days / วันหยุด";              ws["P6"]="4 5"
        ws["H7"]="Allow Evening→Night / บ่ายต่อดึก"; ws["P7"]="No"
        ws["H8"]="Allow Night→Day / ดึกต่อเช้า";     ws["P8"]="Yes"
        ws["H9"]="Head-nurse special shift / พิเศษ"; ws["P9"]="No"
        ws["H10"]="Weekday need D / E / N";          ws["P10"]="2 / 1 / 1"
        ws["H11"]="Weekend need D / E / N";          ws["P11"]="1 / 1 / 1"
        # grid: header row 16 then nurses (day cols C..G = days 1..5)
        ws["A16"]="Nurse · พยาบาล"; ws["B16"]="Type · ระดับ"
        for i, d in enumerate(range(1, 6)):
            ws.cell(row=16, column=3+i, value=d)
        ws["A18"]="Alice"; ws["B18"]="senior"; ws["C18"]="ช"; ws["E18"]="vac"
        ws["A19"]="Bob";   ws["B19"]="new";    ws["D19"]="off"
        ws["A20"]="Cara";  ws["G20"]="ด"
        buf=io.BytesIO(); wb.save(buf); return buf.getvalue()

    def test_parses_cards_settings_and_roster(self):
        si = ScheduleInput.from_xlsx(self._make_xlsx())
        self.assertEqual(si.ward_meta["ward_name"], "Test Ward")
        self.assertEqual(si.ward_meta["tier"], "ward")
        self.assertEqual(si.ward_meta["month"], "7")
        self.assertEqual(si.ward_meta["year"], "2026")
        s = si.settings
        self.assertEqual(s["num_days"], 5)
        self.assertEqual(s["weekends"], [4, 5])
        self.assertFalse(s["allow_evening_to_night"])
        self.assertTrue(s["allow_night_to_day"])
        self.assertFalse(s["head_nurse_special_shift"])
        self.assertEqual((s["coverage_weekday_day"], s["coverage_weekday_evening"],
                          s["coverage_weekday_night"]), (2, 1, 1))
        self.assertEqual(s["coverage_weekend_day"], 1)
        # defaults for settings the template doesn't carry
        self.assertTrue(s["enforce_vacation"])
        self.assertEqual(s["min_request_percent"], 100)

    def test_nurse_rows_and_tokens(self):
        si = ScheduleInput.from_xlsx(self._make_xlsx())
        self.assertEqual([n.name for n in si.nurses], ["Alice", "Bob", "Cara"])
        alice, bob, cara = si.nurses
        self.assertEqual(alice.type, "senior")
        self.assertEqual(alice.shifts, {1: "ช", 3: "vac"})   # E18 -> day 3
        self.assertEqual(bob.type, "new")
        self.assertEqual(bob.shifts, {2: "off"})
        self.assertEqual(cara.shifts, {5: "ด"})

    def test_roundtrips_through_dataimporter(self):
        si = ScheduleInput.from_xlsx(self._make_xlsx())
        fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            di = DataImporter(path, 5, 5).transform()
            self.assertEqual(di.settings["num_days"], 5)
            # Alice's vacation on day 3 (0-based index 2) survives to the solver.
            self.assertIn(2, di.reqVacations[0])
        finally:
            os.unlink(path)

    def test_num_days_falls_back_to_header_when_card_missing(self):
        si = ScheduleInput.from_xlsx(self._make_xlsx(with_days_cell=False))
        self.assertEqual(si.settings["num_days"], 5)   # from the 1..5 header row

    def test_bad_bytes_raise_valueerror(self):
        with self.assertRaises(ValueError):
            ScheduleInput.from_xlsx(b"this is not a zip / xlsx")


if __name__ == "__main__":
    unittest.main()
