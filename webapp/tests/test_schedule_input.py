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

from webapp.core.schedule_input import (  # noqa: E402
    ScheduleInput, Nurse, VALID_CELL_TOKENS,
)
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

    def _make_xlsx(self, *, tier="ward", num_days_cell=5, with_days_cell=True,
                   weekday_need="2 / 1 / 1", weekend_need="1 / 1 / 1",
                   extra_rows=()):
        """extra_rows: (label, value) pairs written to SETTINGS rows 12-15
        (value None leaves the cell blank). weekday_need / weekend_need=None
        omits that row entirely."""
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
        if weekday_need is not None:
            ws["H10"]="Weekday need D / E / N";      ws["P10"]=weekday_need
        if weekend_need is not None:
            ws["H11"]="Weekend need D / E / N";      ws["P11"]=weekend_need
        # Row 16 is the day-number header; overwriting it would corrupt the grid.
        assert len(extra_rows) <= 4, "extra_rows must fit SETTINGS rows 12-15"
        for i, (label, value) in enumerate(extra_rows):
            ws.cell(row=12+i, column=8, value=label)
            ws.cell(row=12+i, column=16, value=value)
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


    # -- Excel template parity (spec 2026-10-17-excel-template-parity) --------

    def _s(self, **kw):
        return ScheduleInput.from_xlsx(self._make_xlsx(**kw)).settings

    def test_special_holidays_parsed(self):
        s = self._s(extra_rows=[("Special holidays / วันหยุดพิเศษ", "3 4")])
        self.assertEqual(s["holidays"], [3, 4])

    def test_special_holidays_blank_means_empty(self):
        # Blank row present -> [] (NOT _DEFAULT_SETTINGS["weekends"], which is
        # what _parse_weekends_1based("") would return). Failure mode 11.
        s = self._s(extra_rows=[("Special holidays / วันหยุดพิเศษ", None)])
        self.assertEqual(s["holidays"], [])

    def test_holiday_need_parsed(self):
        s = self._s(extra_rows=[("Holiday need D / E / N", "4 / 2 / 2")])
        self.assertEqual((s["coverage_holiday_day"], s["coverage_holiday_evening"],
                          s["coverage_holiday_night"]), (4, 2, 2))

    def test_holiday_need_absent_inherits_weekend(self):
        s = self._s(weekend_need="1 / 1 / 1")
        self.assertEqual(s["coverage_holiday_day"], 1)
        # and a different weekend band is inherited, not a frozen default
        s = self._s(weekend_need="3 / 2 / 2")
        self.assertEqual((s["coverage_holiday_day"], s["coverage_holiday_evening"],
                          s["coverage_holiday_night"]), (3, 2, 2))

    def test_saturday_sunday_lists_and_needs_parsed(self):
        s = self._s(extra_rows=[("Saturdays / วันเสาร์", "4"),
                                ("Sundays / วันอาทิตย์", "5"),
                                ("Saturday need D / E / N", "3 / 2 / 1"),
                                ("Sunday need D / E / N", "6 / 5 / 4")])
        self.assertEqual((s["saturdays"], s["sundays"]), ([4], [5]))
        self.assertEqual((s["coverage_saturday_day"], s["coverage_saturday_evening"],
                          s["coverage_saturday_night"]), (3, 2, 1))
        self.assertEqual((s["coverage_sunday_day"], s["coverage_sunday_evening"],
                          s["coverage_sunday_night"]), (6, 5, 4))
        # RN inherits the legacy Saturday row (PN/NA override tested below).
        self.assertEqual(s["coverage_rn_saturday_day"], 3)

    def test_pnna_saturday_sunday_need_parsed(self):
        s = self._s(extra_rows=[("PN/NA saturday need D / E / N", "2 / 1 / 0"),
                                ("PN/NA sunday need D / E / N", "1 / 1 / 1")])
        self.assertEqual((s["coverage_pnna_saturday_day"],
                          s["coverage_pnna_saturday_night"]), (2, 0))
        self.assertEqual(s["coverage_pnna_sunday_day"], 1)

    def test_saturday_sunday_blank_lists_mean_empty_and_inherit_weekend(self):
        s = self._s(weekend_need="3 / 2 / 2", extra_rows=[
            ("Saturdays / วันเสาร์", None), ("Sundays / วันอาทิตย์", None)])
        self.assertEqual((s["saturdays"], s["sundays"]), ([], []))
        self.assertEqual(s["coverage_saturday_day"], 3)
        self.assertEqual(s["coverage_sunday_evening"], 2)

    def test_pnna_needs_parsed(self):
        s = self._s(weekday_need="2 / 1 / 1", extra_rows=[
            ("PN/NA weekday need D / E / N", "3 / 2 / 2")])
        self.assertEqual((s["coverage_pnna_weekday_day"],
                          s["coverage_pnna_weekday_evening"],
                          s["coverage_pnna_weekday_night"]), (3, 2, 2))
        # RN inherits the legacy Weekday need row (the "/" collision contract)
        self.assertEqual(s["coverage_rn_weekday_day"], 2)

    def test_pnna_dash_spelling_parsed(self):
        s = self._s(extra_rows=[("PN-NA weekend need D / E / N", "2 / 1 / 1")])
        self.assertEqual((s["coverage_pnna_weekend_day"],
                          s["coverage_pnna_weekend_evening"],
                          s["coverage_pnna_weekend_night"]), (2, 1, 1))
        # same result as the slash spelling
        s2 = self._s(extra_rows=[("PN/NA weekend need D / E / N", "2 / 1 / 1")])
        self.assertEqual(s["coverage_pnna_weekend_day"],
                         s2["coverage_pnna_weekend_day"])

    def test_label_key_keeps_english_slash(self):
        from webapp.core.schedule_input import _label_key
        self.assertEqual(_label_key("PN/NA weekday need D / E / N"),
                         "pn/na weekday need d / e / n")
        self.assertEqual(_label_key("Ward name / ชื่อวอร์ด"), "ward name")

    def test_existing_labels_unchanged(self):
        si = ScheduleInput.from_xlsx(self._make_xlsx())
        m, s = si.ward_meta, si.settings
        self.assertEqual(m["ward_name"], "Test Ward")
        self.assertEqual(m["hospital"], "Test Hospital")
        self.assertEqual(m["head_nurse"], "Boss")
        self.assertEqual(m["month"], "7")
        self.assertEqual(m["year"], "2026")
        self.assertEqual(m["tier"], "ward")
        self.assertEqual(s["num_days"], 5)
        self.assertEqual(s["weekends"], [4, 5])
        # line contact: the one exact label the default builder omits
        import io, openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(self._make_xlsx()))
        ws = wb["Roster"]
        ws["A8"] = "Line contact / ไลน์"; ws["C8"] = "@boss"
        buf = io.BytesIO(); wb.save(buf)
        si2 = ScheduleInput.from_xlsx(buf.getvalue())
        self.assertEqual(si2.ward_meta["line"], "@boss")


# -- Staff classes / off-ward duties / holiday band (regression cover) --------

def _schedule_lines(csv_text):
    """The rows of the [schedule] section (header first), as lists of cells."""
    import csv as _csv
    import io as _io
    body = csv_text.split("[schedule]\n", 1)[1]
    return list(_csv.reader(_io.StringIO(body)))


def _setting_lines(csv_text):
    """The raw lines of the [settings] section (between the two headers)."""
    block = csv_text.split("[settings]\n", 1)[1].split("\n[schedule]", 1)[0]
    return [ln for ln in block.splitlines() if ln]


def _classed_ward(rn, pn, na=0, settings=None):
    nurses = [Nurse(name=f"R{i}", staff_class="rn") for i in range(rn)]
    nurses += [Nurse(name=f"P{i}", staff_class="pn") for i in range(pn)]
    nurses += [Nurse(name=f"A{i}", staff_class="na") for i in range(na)]
    base = {"num_days": 7, "weekends": [6, 7]}
    base.update(settings or {})
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "W", "tier": "ward"}, settings=base, nurses=nurses)


class UngroupedEmissionTest(unittest.TestCase):

    _EXPECTED = (
        "[ward]\n"
        "ward_name,W\n"
        "tier,ward\n"
        "\n"
        "[settings]\n"
        "num_days,3\n"
        "weekends,2\n"
        "holidays,\n"
        "extra_holidays,\n"
        "saturdays,\n"
        "sundays,\n"
        "allow_evening_to_night,false\n"
        "allow_night_to_day,false\n"
        "head_nurse_special_shift,true\n"
        "relax_days_off,false\n"
        "enforce_vacation,true\n"
        "min_request_percent,100\n"
        "coverage_weekday_day,5\n"
        "coverage_weekday_evening,3\n"
        "coverage_weekday_night,3\n"
        "coverage_weekend_day,4\n"
        "coverage_weekend_evening,2\n"
        "coverage_weekend_night,2\n"
        "coverage_holiday_day,4\n"
        "coverage_holiday_evening,2\n"
        "coverage_holiday_night,2\n"
        "coverage_saturday_day,4\n"
        "coverage_saturday_evening,2\n"
        "coverage_saturday_night,2\n"
        "coverage_sunday_day,4\n"
        "coverage_sunday_evening,2\n"
        "coverage_sunday_night,2\n"
        "\n"
        "[schedule]\n"
        "Name,Type,1,2,3\n"
        "A,,,,\n"
        "B,,,,\n"
    )

    def test_ungrouped_solver_csv_is_exact(self):
        # A ward with no staff classes must emit the single-roster CSV: the
        # legacy fifteen coverage keys only, no group keys, and no Class column.
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 3, "weekends": [2]},
            nurses=[Nurse(name="A"), Nurse(name="B")],
        )
        out = si.to_solver_csv()
        self.assertEqual(out, self._EXPECTED)
        # Spelled out so a failure names the leak rather than just "differs".
        self.assertNotIn("coverage_rn_", out)
        self.assertNotIn("coverage_pnna_", out)
        self.assertNotIn("Class", out)
        self.assertIn("weekends,2", out.splitlines())
        self.assertIn("holidays,", out.splitlines())
        self.assertIn("coverage_weekday_day,5", out.splitlines())
        self.assertIn("coverage_holiday_day,4", out.splitlines())
        self.assertIn("coverage_saturday_day,4", out.splitlines())
        self.assertEqual(_schedule_lines(out)[0], ["Name", "Type", "1", "2", "3"])


class GroupEmissionTest(unittest.TestCase):

    def test_group_csv_contains_only_that_group(self):
        si = _classed_ward(rn=4, pn=3, na=1)
        rn_rows = _schedule_lines(si.to_solver_csv(group="rn"))
        pn_rows = _schedule_lines(si.to_solver_csv(group="pn_na"))
        self.assertEqual(rn_rows[0][:3], ["Name", "Type", "Class"])
        self.assertEqual(len(rn_rows) - 1, 4)
        self.assertEqual([r[0] for r in rn_rows[1:]], ["R0", "R1", "R2", "R3"])
        self.assertEqual(len(pn_rows) - 1, 4)
        self.assertEqual([r[0] for r in pn_rows[1:]], ["P0", "P1", "P2", "A0"])
        # Together they cover the whole active roster exactly once.
        self.assertEqual(
            [r[0] for r in rn_rows[1:] + pn_rows[1:]],
            [n.name for n in si.active_nurses()],
        )

    def test_group_csv_uses_group_coverage(self):
        si = _classed_ward(rn=4, pn=3, settings={
            "coverage_pnna_weekday_day": 2, "coverage_rn_weekday_day": 6})
        pn = _setting_lines(si.to_solver_csv(group="pn_na"))
        rn = _setting_lines(si.to_solver_csv(group="rn"))
        # The group's number is written under the solver-facing legacy name.
        self.assertIn("coverage_weekday_day,2", pn)
        self.assertIn("coverage_weekday_day,6", rn)
        # Head and deputy are RNs: the PN/NA solve must not run the senior rule.
        self.assertIn("head_nurse_special_shift,false", pn)
        self.assertIn("head_nurse_special_shift,true", rn)
        # The ward-wide setting itself is untouched by the override.
        self.assertTrue(si.settings["head_nurse_special_shift"])

    def test_group_csv_remaps_holiday_band(self):
        si = _classed_ward(rn=4, pn=3, settings={
            "coverage_pnna_holiday_night": 1, "coverage_rn_holiday_night": 3})
        self.assertIn("coverage_holiday_night,1",
                      _setting_lines(si.to_solver_csv(group="pn_na")))
        self.assertIn("coverage_holiday_night,3",
                      _setting_lines(si.to_solver_csv(group="rn")))

    def test_legacy_coverage_is_inherited(self):
        # A saved input that predates staff groups has only the legacy keys; the
        # group keys must take those numbers so its staffing does not change.
        s = ScheduleInput._coerce_settings({"coverage_weekday_night": 1})
        self.assertEqual(s["coverage_weekday_night"], 1)
        self.assertEqual(s["coverage_rn_weekday_night"], 1)
        self.assertEqual(s["coverage_pnna_weekday_night"], 1)
        # Unrelated keys keep their defaults.
        self.assertEqual(s["coverage_rn_weekday_day"], 5)

    def test_mixed_staff_class_is_rejected(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name="Rita", staff_class="rn"),
                    Nurse(name="Blanca")],
        )
        with self.assertRaises(ValueError) as cm:
            si.validate()
        self.assertIn("Blanca", str(cm.exception))
        # An INACTIVE blank nurse is exempt: she is never solved.
        si.nurses[1].active = False
        si.nurses.append(Nurse(name="Rita2", staff_class="rn"))  # senior rule needs 2
        si.validate()


class OffwardTokenTest(unittest.TestCase):

    def _ward(self, shifts):
        return ScheduleInput.from_grid(
            ward_meta={"ward_name": "W"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name="A", shifts=shifts), Nurse(name="B")],
        )

    def test_bare_flt_rejected(self):
        for bare in ("flt", "ref"):
            with self.subTest(token=bare):
                with self.assertRaises(ValueError) as cm:
                    self._ward({1: bare}).validate()
                # The message must tell the user what to write instead.
                self.assertIn(f"{bare}:ช", str(cm.exception))
        # A well-formed off-ward cell is accepted.
        self._ward({1: "flt:ช", 2: "ref:ด"}).validate()

    def test_offward_case_is_normalised(self):
        text = ("[ward]\nward_name,W\n\n[settings]\nnum_days,3\n\n[schedule]\n"
                "Name,Type,1,2,3\nA,,FLT:ด,REF:ช,FLT\nB,,,,\n")
        si = ScheduleInput.from_csv(text)
        self.assertEqual(si.nurses[0].shifts[1], "flt:ด")
        self.assertEqual(si.nurses[0].shifts[2], "ref:ช")
        # A bare kind is lower-cased but never guessed into a shift ...
        self.assertEqual(si.nurses[0].shifts[3], "flt")
        # ... and validate() then refuses it.
        with self.assertRaises(ValueError):
            si.validate()
        self.assertIn("flt:ด", VALID_CELL_TOKENS)


class NegativeRequestTokenTests(unittest.TestCase):
    """'no:บ' = anything except Evening (constraint.md section 23)."""

    def _csv(self, *cells):
        head = ",".join(str(i + 1) for i in range(len(cells)))
        return ("[ward]\nward_name,W\n\n[settings]\nnum_days,%d\n\n[schedule]\n"
                "Name,Type,%s\nA,,%s\nB,,%s\n"
                % (len(cells), head, ",".join(cells), ",".join([""] * len(cells))))

    def test_token_is_valid_and_normalised(self):
        for t in ("no:ช", "no:บ", "no:ด"):
            self.assertIn(t, VALID_CELL_TOKENS)
        si = ScheduleInput.from_csv(self._csv("NO:บ"))
        self.assertEqual(si.nurses[0].shifts[1], "no:บ")
        si.validate()

    def test_bare_no_and_no_off_are_rejected(self):
        for bad in ("no", "no:off", "no:vac", "no:ช/บ"):
            si = ScheduleInput.from_csv(self._csv(bad))
            with self.assertRaises(ValueError, msg=bad):
                si.validate()

    def test_token_survives_to_solver_csv_and_importer(self):
        si = ScheduleInput.from_csv(self._csv("no:บ", "ช"))
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            data = DataImporter(path, 5, 5).transform()
        finally:
            os.unlink(path)
        from constant import Shift
        self.assertEqual(data.reqNegShifts[0], {0: Shift.EVENING})
        self.assertEqual(data.reqShifts[0], {1: [Shift.DAY]})
        self.assertEqual(data.reqNegShifts[1], {})


class HolidayBandSettingsTest(unittest.TestCase):

    def _importer(self, si):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            return DataImporter(path, 5, 5).transform()
        finally:
            os.unlink(path)

    def test_holidays_setting_round_trips(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 14, "weekends": [6, 7], "holidays": [3, 10]},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertEqual(si.settings["holidays"], [3, 10])
        self.assertIn("holidays,3 10", si.to_solver_csv().splitlines())
        self.assertEqual(
            ScheduleInput.from_csv(si.to_solver_csv()).settings["holidays"], [3, 10])
        self.assertEqual(
            ScheduleInput.from_dict(si.to_dict()).settings["holidays"], [3, 10])
        # The solver's importer reads them as 0-based days.
        di = self._importer(si)
        self.assertEqual(di.settings["holidays"], [2, 9])
        self.assertEqual(di.settings["weekends"], [5, 6])

    def test_holidays_empty_means_empty(self):
        # Absent key and empty string both give [] - never the weekends default.
        self.assertEqual(ScheduleInput._coerce_settings({})["holidays"], [])
        self.assertEqual(ScheduleInput._coerce_settings({"holidays": ""})["holidays"], [])
        self.assertEqual(ScheduleInput._coerce_settings({"holidays": "  "})["holidays"], [])
        self.assertEqual(
            ScheduleInput._coerce_settings({"holidays": "3, 10"})["holidays"], [3, 10])
        # Through the solver boundary too: a ward with no holidays has none, even
        # though an empty `weekends` value would have meant "the default weekends".
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 14, "weekends": [6, 7]},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        self.assertEqual(ScheduleInput.from_csv(si.to_solver_csv()).settings["holidays"], [])
        self.assertEqual(self._importer(si).settings["holidays"], [])

    def test_holiday_coverage_inheritance_chain(self):
        c = ScheduleInput._coerce_settings

        # (a) a legacy weekend number is inherited by the legacy holiday key and
        # by both groups.
        s = c({"coverage_weekend_night": 1})
        self.assertEqual(s["coverage_holiday_night"], 1)
        self.assertEqual(s["coverage_rn_holiday_night"], 1)
        self.assertEqual(s["coverage_pnna_holiday_night"], 1)
        # only the night key was given; the other shifts stay at 4/2/2
        self.assertEqual(s["coverage_holiday_day"], 4)
        self.assertEqual(s["coverage_holiday_evening"], 2)

        # (b) a GROUP's weekend number is inherited by that group's holiday key
        # only; the other group falls through to the default.
        s = c({"coverage_rn_weekend_night": 1})
        self.assertEqual(s["coverage_rn_holiday_night"], 1)
        self.assertEqual(s["coverage_pnna_holiday_night"], 2)
        self.assertEqual(s["coverage_holiday_night"], 2)

        # (b2) ordering: the group's weekend beats the legacy holiday key ...
        s = c({"coverage_rn_weekend_night": 1, "coverage_holiday_night": 5,
               "coverage_weekend_night": 6})
        self.assertEqual(s["coverage_rn_holiday_night"], 1)
        # ... and the other group, having no weekend of its own, takes the
        # legacy HOLIDAY key before the legacy weekend key.
        self.assertEqual(s["coverage_pnna_holiday_night"], 5)
        self.assertEqual(s["coverage_holiday_night"], 5)

        # (b3) with only a legacy weekend given, the groups follow it.
        s = c({"coverage_weekend_night": 6})
        self.assertEqual(s["coverage_pnna_holiday_night"], 6)

        # (c) an explicit group holiday key beats every other source.
        s = c({"coverage_rn_holiday_night": 3, "coverage_rn_weekend_night": 1,
               "coverage_holiday_night": 5, "coverage_weekend_night": 6})
        self.assertEqual(s["coverage_rn_holiday_night"], 3)
        # the legacy holiday key beats the legacy weekend key
        self.assertEqual(s["coverage_holiday_night"], 5)
        # an explicit 0 is a value, not "absent"
        s = c({"coverage_rn_holiday_night": 0, "coverage_rn_weekend_night": 1})
        self.assertEqual(s["coverage_rn_holiday_night"], 0)

        # (d) nothing given: every holiday key is the 4/2/2 default.
        s = c({})
        for prefix in ("coverage_", "coverage_rn_", "coverage_pnna_"):
            self.assertEqual(
                [s[f"{prefix}holiday_{sh}"] for sh in ("day", "evening", "night")],
                [4, 2, 2], prefix)


class SaturdaySundayBandSettingsTest(unittest.TestCase):
    """constraint.md 22: Saturday / Sunday bands. Same shape as the holiday band;
    `weekends` is never touched."""

    def _importer(self, si):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        try:
            return DataImporter(path, 5, 5).transform()
        finally:
            os.unlink(path)

    def test_lists_default_empty_and_round_trip(self):
        c = ScheduleInput._coerce_settings
        self.assertEqual(c({})["saturdays"], [])
        self.assertEqual(c({})["sundays"], [])
        self.assertEqual(c({"saturdays": "", "sundays": " "})["sundays"], [])
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 14, "weekends": [6, 7, 13, 14],
                      "saturdays": [6, 13], "sundays": [7, 14]},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        lines = si.to_solver_csv().splitlines()
        self.assertIn("saturdays,6 13", lines)
        self.assertIn("sundays,7 14", lines)
        self.assertIn("weekends,6 7 13 14", lines)   # untouched union
        back = ScheduleInput.from_csv(si.to_solver_csv()).settings
        self.assertEqual((back["saturdays"], back["sundays"]), ([6, 13], [7, 14]))
        d = ScheduleInput.from_dict(si.to_dict()).settings
        self.assertEqual((d["saturdays"], d["sundays"]), ([6, 13], [7, 14]))
        di = self._importer(si)
        self.assertEqual((di.settings["saturdays"], di.settings["sundays"]),
                         ([5, 12], [6, 13]))
        self.assertEqual(di.settings["weekends"], [5, 6, 12, 13])

    def test_saved_input_without_the_keys_is_unchanged(self):
        # A pre-existing saved input: no saturdays/sundays, custom weekend need.
        s = ScheduleInput._coerce_settings({"weekends": [6, 7],
                                            "coverage_weekend_day": 7})
        for band in ("saturday", "sunday"):
            self.assertEqual(s[f"coverage_{band}_day"], 7)
            self.assertEqual(s[f"coverage_rn_{band}_day"], 7)
            self.assertEqual(s[f"coverage_pnna_{band}_day"], 7)
        self.assertEqual(s["weekends"], [6, 7])

    def test_coverage_inheritance_chain(self):
        c = ScheduleInput._coerce_settings
        for band in ("saturday", "sunday"):
            other = "sunday" if band == "saturday" else "saturday"
            # (a) legacy weekend -> legacy band and both groups
            s = c({"coverage_weekend_night": 1})
            self.assertEqual(s[f"coverage_{band}_night"], 1)
            self.assertEqual(s[f"coverage_rn_{band}_night"], 1)
            self.assertEqual(s[f"coverage_pnna_{band}_night"], 1)
            # (b) a group's weekend -> that group's band only
            s = c({"coverage_rn_weekend_night": 1})
            self.assertEqual(s[f"coverage_rn_{band}_night"], 1)
            self.assertEqual(s[f"coverage_pnna_{band}_night"], 2)
            # (b2) group weekend beats legacy band; other group takes legacy band
            s = c({"coverage_rn_weekend_night": 1, f"coverage_{band}_night": 5,
                   "coverage_weekend_night": 6})
            self.assertEqual(s[f"coverage_rn_{band}_night"], 1)
            self.assertEqual(s[f"coverage_pnna_{band}_night"], 5)
            # (c) explicit group band key beats everything; 0 is a value
            s = c({f"coverage_rn_{band}_night": 0, "coverage_rn_weekend_night": 1,
                   f"coverage_{band}_night": 5})
            self.assertEqual(s[f"coverage_rn_{band}_night"], 0)
            # Saturday and Sunday are independent of each other.
            s = c({f"coverage_{band}_day": 9})
            self.assertEqual(s[f"coverage_{other}_day"], 4)
            # (d) nothing given -> 4/2/2
            s = c({})
            for prefix in ("coverage_", "coverage_rn_", "coverage_pnna_"):
                self.assertEqual(
                    [s[f"{prefix}{band}_{sh}"] for sh in ("day", "evening", "night")],
                    [4, 2, 2])

    def test_solver_csv_and_importer_carry_the_band(self):
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"},
            settings={"num_days": 14, "weekends": [6, 7], "saturdays": [6],
                      "sundays": [7], "coverage_saturday_day": 6,
                      "coverage_sunday_night": 0},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        lines = si.to_solver_csv().splitlines()
        self.assertIn("coverage_saturday_day,6", lines)
        self.assertIn("coverage_sunday_night,0", lines)
        di = self._importer(si)
        self.assertEqual(di.settings["coverage_saturday_day"], 6)
        self.assertEqual(di.settings["coverage_sunday_night"], 0)

    def test_importer_falls_back_to_file_weekend_numbers(self):
        # A solver CSV written before the bands existed: no saturday/sunday keys.
        text = ("[ward]\nward_name,W\ntier,ward\n\n[settings]\nnum_days,3\n"
                "weekends,2\ncoverage_weekend_day,7\ncoverage_weekend_evening,6\n"
                "coverage_weekend_night,5\n\n[schedule]\nName,Type,1,2,3\nA,,,,\n")
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            s = DataImporter(path, 5, 5).transform().settings
        finally:
            os.unlink(path)
        self.assertEqual((s["saturdays"], s["sundays"]), ([], []))
        for band in ("saturday", "sunday"):
            self.assertEqual([s[f"coverage_{band}_{sh}"]
                              for sh in ("day", "evening", "night")], [7, 6, 5])

    def test_group_csv_remaps_saturday_band(self):
        si = _classed_ward(rn=4, pn=3, settings={
            "coverage_pnna_saturday_night": 1, "coverage_rn_saturday_night": 3,
            "coverage_pnna_sunday_day": 1, "coverage_rn_sunday_day": 6})
        pn = _setting_lines(si.to_solver_csv(group="pn_na"))
        rn = _setting_lines(si.to_solver_csv(group="rn"))
        self.assertIn("coverage_saturday_night,1", pn)
        self.assertIn("coverage_saturday_night,3", rn)
        self.assertIn("coverage_sunday_day,1", pn)
        self.assertIn("coverage_sunday_day,6", rn)


if __name__ == "__main__":
    unittest.main()
