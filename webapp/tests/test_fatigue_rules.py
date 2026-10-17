"""constraint.md section 24: max-consecutive-nights and no-consecutive-ช/บ,
through every app layer (settings, CSV, Excel card, form, presolve)."""

import io
import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core import presolve  # noqa: E402
from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402
from webapp.web.app import _settings_from_form  # noqa: E402

try:
    import openpyxl
    from tools.gen_templates import TEMPLATES, build_workbook
    _XLSX = True
except Exception:  # pragma: no cover
    _XLSX = False


def _si(shifts, **flags):
    settings = {"num_days": 10, "weekends": [], "exact_coverage": False,
                "coverage_weekday_day": 0, "coverage_weekday_evening": 0,
                "coverage_weekday_night": 0, "head_nurse_special_shift": False}
    settings.update(flags)
    return ScheduleInput.from_grid(ward_meta={"ward_name": "W", "tier": "ward"}, settings=settings,
                                   nurses=[Nurse(name="A", shifts=shifts)])


def _codes(issues):
    return {i.code for i in issues}


class SettingsLayerTests(unittest.TestCase):
    def test_defaults_are_off_and_three(self):
        s = _si({}).settings
        self.assertFalse(s["limit_consecutive_nights"])
        self.assertFalse(s["no_consecutive_day_eve"])
        self.assertEqual(s["max_consecutive_nights"], 3)

    def test_round_trip_through_solver_csv(self):
        si = _si({}, limit_consecutive_nights=True, max_consecutive_nights=2,
                 no_consecutive_day_eve=True)
        s = ScheduleInput.from_csv(si.to_solver_csv()).settings
        self.assertTrue(s["limit_consecutive_nights"])
        self.assertTrue(s["no_consecutive_day_eve"])
        self.assertEqual(s["max_consecutive_nights"], 2)

    def test_dataimporter_reads_them(self):
        import tempfile
        from dataimporter import DataImporter
        si = _si({}, limit_consecutive_nights=True, max_consecutive_nights=2,
                 no_consecutive_day_eve=True)
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(si.to_solver_csv())
            s = DataImporter(path, 5, 5).transform().settings
        finally:
            os.unlink(path)
        self.assertTrue(s["limit_consecutive_nights"] and s["no_consecutive_day_eve"])
        self.assertEqual(s["max_consecutive_nights"], 2)

    def test_form_builder(self):
        class F(dict):
            pass
        on = _settings_from_form(F(limit_consecutive_nights="on", max_consecutive_nights="4",
                                   no_consecutive_day_eve="on"))
        self.assertEqual((on["limit_consecutive_nights"], on["max_consecutive_nights"],
                          on["no_consecutive_day_eve"]), (True, "4", True))
        off = _settings_from_form(F())
        self.assertFalse(off["limit_consecutive_nights"] or off["no_consecutive_day_eve"])


@unittest.skipUnless(_XLSX, "openpyxl / tools.gen_templates unavailable")
class ExcelCardTests(unittest.TestCase):
    def _wb(self):
        spec = next(t for t in TEMPLATES if "blank" in t.filename.lower() or "blank" in str(t).lower())
        return build_workbook(spec)

    @staticmethod
    def _bytes(wb):
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_blank_template_defaults(self):
        s = ScheduleInput.from_xlsx(self._bytes(self._wb())).settings
        self.assertFalse(s["limit_consecutive_nights"] or s["no_consecutive_day_eve"])
        self.assertEqual(s["max_consecutive_nights"], 3)

    def test_card_values_are_read(self):
        wb = self._wb()
        ws = wb.worksheets[0]
        hits = 0
        for row in ws.iter_rows():
            for c in row:
                if not isinstance(c.value, str):
                    continue
                v = c.value.lower()
                target = None
                if v.startswith("limit consecutive nights"):
                    target = "Yes"
                elif v.startswith("max consecutive nights"):
                    target = 2
                elif v.startswith("no consecutive day"):
                    target = "Yes"
                if target is not None:
                    # value cell is to the right on the same row (merged label span)
                    for cc in ws[c.row]:
                        if cc.column > c.column and cc.value in ("No", 3):
                            cc.value = target
                            hits += 1
                            break
        self.assertEqual(hits, 3)
        s = ScheduleInput.from_xlsx(self._bytes(wb)).settings
        self.assertTrue(s["limit_consecutive_nights"])
        self.assertTrue(s["no_consecutive_day_eve"])
        self.assertEqual(s["max_consecutive_nights"], 2)


class PresolveTests(unittest.TestCase):
    NIGHTS = {2: "ด", 3: "ด", 4: "ด", 5: "ด"}

    def test_four_nights_flagged_at_limit_3_and_solver_agrees(self):
        si = _si(self.NIGHTS, limit_consecutive_nights=True, max_consecutive_nights=3)
        issues = [i for i in presolve.check(si).errors if i.code == "request_consecutive_nights"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].days, [2, 3, 4, 5])
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_three_nights_not_flagged(self):
        si = _si({2: "ด", 3: "ด", 4: "ด"}, limit_consecutive_nights=True,
                 max_consecutive_nights=3)
        self.assertEqual(_codes(presolve.check(si).errors), set())

    def test_nights_toggle_off_not_flagged(self):
        self.assertEqual(_codes(presolve.check(_si(self.NIGHTS)).errors), set())

    def test_day_eve_adjacent_flagged_and_solver_agrees(self):
        si = _si({3: "ช/บ", 4: "ช/บ"}, no_consecutive_day_eve=True)
        self.assertIn("request_consecutive_day_eve", _codes(presolve.check(si).errors))
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_day_eve_then_day_not_flagged(self):
        si = _si({3: "ช/บ", 4: "ช"}, no_consecutive_day_eve=True)
        self.assertEqual(_codes(presolve.check(si).errors), set())

    def test_day_eve_toggle_off_not_flagged(self):
        self.assertEqual(_codes(presolve.check(_si({3: "ช/บ", 4: "ช/บ"})).errors), set())


if __name__ == "__main__":
    unittest.main()
