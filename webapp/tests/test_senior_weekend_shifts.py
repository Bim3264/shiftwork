"""Senior weekend shifts (constraint.md 17a): the per-nurse `weekend_shifts` count
through every layer that carries it -- CSV, dict, Excel template, grid form,
validate(), presolve -- plus an end-to-end solve of the shipped example workbook.

Solver behaviour itself is tested in test_shiftwork.SeniorWeekendShiftTest.
"""
import io
import os
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from dataimporter import DataImporter  # noqa: E402
from webapp.core import presolve  # noqa: E402
from webapp.core.schedule_input import Nurse, ScheduleInput  # noqa: E402

try:
    import openpyxl  # noqa: F401
    from tools.gen_templates import TEMPLATES, build_workbook
    _XLSX = True
except ImportError:  # pragma: no cover
    _XLSX = False

# Ward 15 dates: Saturdays 7 14 21 28, Sundays 1 8 15 22 29 (Sunday 1 is an orphan).
_SETTINGS = {"num_days": 29, "weekends": [1, 7, 8, 14, 15, 21, 22, 28, 29],
             "saturdays": [7, 14, 21, 28], "sundays": [1, 8, 15, 22, 29]}


def _si(*nurses, **settings):
    return ScheduleInput.from_grid(ward_meta={"ward_name": "W", "tier": "ward"},
                                   settings={**_SETTINGS, **settings},
                                   nurses=list(nurses))


def _import(text):
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
    try:
        data = DataImporter(f.name, 5, 5)
        data.transform()
        return data
    finally:
        os.unlink(f.name)


class WeekendShiftsFieldTests(unittest.TestCase):

    def test_field_normalises_junk_to_zero(self):
        self.assertEqual(Nurse(name="A", weekend_shifts="2").weekend_shifts, 2)
        self.assertEqual(Nurse(name="A", weekend_shifts="").weekend_shifts, 0)
        self.assertEqual(Nurse(name="A", weekend_shifts="x").weekend_shifts, 0)
        self.assertEqual(Nurse(name="A", weekend_shifts=-3).weekend_shifts, 0)

    def test_dict_round_trip_and_old_rows_default_to_zero(self):
        si = _si(Nurse(name="Head", type="senior", weekend_shifts=2), Nurse(name="B"))
        back = ScheduleInput.from_dict(si.to_dict())
        self.assertEqual([n.weekend_shifts for n in back.nurses], [2, 0])
        old = si.to_dict()
        for n in old["nurses"]:
            del n["weekend_shifts"]
        self.assertEqual(
            [n.weekend_shifts for n in ScheduleInput.from_dict(old).nurses], [0, 0])

    def test_csv_round_trip_and_importer_reads_the_column(self):
        si = _si(Nurse(name="Head", type="senior", weekend_shifts=2),
                 Nurse(name="Aide", type="senior"), Nurse(name="B"))
        text = si.to_solver_csv()
        self.assertIn("Name,Type,Weekend Shifts,1,2,", text)
        back = ScheduleInput.from_csv(text)
        self.assertEqual([n.weekend_shifts for n in back.nurses], [2, 0, 0])
        self.assertEqual(_import(text).seniorWeekendShifts, {0: 2})

    def test_no_opt_in_leaves_the_csv_without_the_column(self):
        text = _si(Nurse(name="Head", type="senior"), Nurse(name="B")).to_solver_csv()
        self.assertNotIn("Weekend Shifts", text)

    def test_importer_ignores_the_count_on_a_non_senior(self):
        text = _si(Nurse(name="Head", type="senior", weekend_shifts=1),
                   Nurse(name="B", weekend_shifts=3)).to_solver_csv()
        self.assertEqual(_import(text).seniorWeekendShifts, {0: 1})   # B (idx 1) dropped

    def test_inactive_senior_is_not_emitted(self):
        s = _si(Nurse(name="Head", type="senior", weekend_shifts=2, active=False),
                Nurse(name="B"))
        self.assertNotIn("Weekend Shifts", s.to_solver_csv())


class WeekendShiftsValidationTests(unittest.TestCase):

    def test_too_many_weekends_is_rejected_naming_the_nurse(self):
        si = _si(Nurse(name="Head", type="senior", weekend_shifts=6))   # 5 units
        with self.assertRaises(ValueError) as cm:
            si.validate()
        self.assertIn("Head", str(cm.exception))

    def test_all_weekends_is_allowed(self):
        _si(Nurse(name="Head", type="senior", weekend_shifts=5)).validate()

    def test_a_count_on_a_non_senior_does_not_block_validation(self):
        _si(Nurse(name="B", weekend_shifts=9)).validate()


class WeekendShiftsPresolveTests(unittest.TestCase):

    def _codes(self, si):
        rep = presolve.check(si)
        return [i.code for i in rep.errors], [i.code for i in rep.warnings]

    def test_non_senior_count_warns_and_is_ignored(self):
        errs, warns = self._codes(_si(Nurse(name="B", weekend_shifts=2)))
        self.assertIn("weekend_shifts_ignored", warns)
        self.assertNotIn("weekend_shifts_too_many", errs)

    def test_too_many_is_a_presolve_error(self):
        errs, _ = self._codes(_si(Nurse(name="Head", type="senior", weekend_shifts=6)))
        self.assertIn("weekend_shifts_too_many", errs)

    def test_opted_in_senior_meeting_on_a_claimable_weekend_day_is_not_an_error(self):
        head = Nurse(name="Head", type="senior", weekend_shifts=1, shifts={7: "mtg"})
        errs, _ = self._codes(_si(head))
        self.assertNotIn("senior_meeting_weekend", errs)
        # ...but without the opt-in it still is (today's behaviour).
        plain = Nurse(name="Head", type="senior", shifts={7: "mtg"})
        errs, _ = self._codes(_si(plain))
        self.assertIn("senior_meeting_weekend", errs)


@unittest.skipUnless(_XLSX, "openpyxl / tools.gen_templates unavailable")
class WeekendShiftsExcelTests(unittest.TestCase):

    @staticmethod
    def _load(fragment):
        spec = next(t for t in TEMPLATES if fragment in t.filename.lower())
        buf = io.BytesIO()
        build_workbook(spec).save(buf)
        return ScheduleInput.from_xlsx(buf.getvalue())

    def test_example_workbook_opts_the_senior_in(self):
        si = self._load("example")
        self.assertEqual({n.name: n.weekend_shifts for n in si.nurses if n.weekend_shifts},
                         {"NurseA": 2})
        si.validate()

    def test_blank_template_has_the_column_and_no_opt_in(self):
        si = self._load("ward (blank)")
        self.assertTrue(all(n.weekend_shifts == 0 for n in si.nurses))
        spec = next(t for t in TEMPLATES if "ward (blank)" in t.filename.lower())
        ws = build_workbook(spec)["Roster"]
        heads = [str(c.value) for row in ws.iter_rows() for c in row
                 if c.value and str(c.value).lower().startswith("weekend shifts")]
        self.assertEqual(len(heads), 1)

    def test_example_workbook_solves_with_exactly_two_senior_weekends(self):
        from webapp.core.solver_runner import run_schedule
        si = self._load("example")
        result = run_schedule(si, 90)
        self.assertTrue(result.solved, result.solver_status)
        row = next(r for r in result.grid["rows"] if r["name"] == "NurseA")
        cells = dict(enumerate(row["cells"], start=1))
        day = "ช"
        worked = []
        for sat, sun in zip(si.settings["saturdays"], si.settings["sundays"]):
            on = [d for d in (sat, sun) if cells[d] == day]
            self.assertLessEqual(len(on), 1, f"weekend {sat}/{sun}")
            worked += on
        self.assertEqual(len(worked), 2, cells)


class WeekendShiftsFormTests(unittest.TestCase):

    def test_grid_form_reads_wkshifts(self):
        from webapp.web.app import _nurses_from_form
        form = {"row_count": "3", "name_0": "Head", "type_0": "senior",
                "wkshifts_0": "2", "name_1": "B", "wkshifts_1": "",
                "name_2": "C", "wkshifts_2": "junk"}
        got = _nurses_from_form(form, 7)
        self.assertEqual([n.weekend_shifts for n in got], [2, 0, 0])


if __name__ == "__main__":
    unittest.main()
