"""constraint.md section 9: exact_coverage through the app layers (settings, CSV,
Excel card, form, presolve, diagnosis text) and on a staff-class ward."""

import io
import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core import presolve  # noqa: E402
from webapp.core.diagnosis import render_text  # noqa: E402
from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402
from webapp.web.app import _settings_from_form  # noqa: E402

try:
    import openpyxl  # noqa: F401
    from tools.gen_templates import TEMPLATES, build_workbook
    _XLSX = True
except Exception:  # pragma: no cover
    _XLSX = False


def _si(nurses, cov, days=1, **flags):
    settings = {"num_days": days, "weekends": [], "head_nurse_special_shift": False,
                "coverage_weekday_day": cov[0], "coverage_weekday_evening": cov[1],
                "coverage_weekday_night": cov[2]}
    settings.update(flags)
    return ScheduleInput.from_grid(ward_meta={"ward_name": "W", "tier": "ward"},
                                   settings=settings, nurses=nurses)


def _count(rows, day_idx, symbol):
    """How many of `rows` work `symbol` on a 0-based day (cells like 'ช/บ' count for both)."""
    return sum(symbol in r["cells"][day_idx].split("/") for r in rows)


class SettingsLayerTests(unittest.TestCase):
    def test_default_is_on_and_round_trips_to_solver_csv(self):
        si = _si([Nurse(name="A")], (1, 0, 0))
        self.assertTrue(si.settings["exact_coverage"])
        self.assertIn("exact_coverage,true", si.to_solver_csv())
        si.settings["exact_coverage"] = False
        self.assertIn("exact_coverage,false", si.to_solver_csv())

    def test_saved_input_without_the_key_takes_the_default(self):
        d = _si([Nurse(name="A")], (1, 0, 0)).to_dict()
        d["settings"].pop("exact_coverage", None)
        self.assertTrue(ScheduleInput.from_dict(d).settings["exact_coverage"])

    def test_form_checkbox(self):
        self.assertIs(_settings_from_form({"exact_coverage": "on"})["exact_coverage"], True)
        self.assertIs(_settings_from_form({})["exact_coverage"], False)

    @unittest.skipUnless(_XLSX, "openpyxl/templates unavailable")
    def test_excel_card_row_parses_both_ways(self):
        for want in (True, False):
            wb = build_workbook(TEMPLATES[0])
            hits = 0
            ws = wb.worksheets[0]
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value.lower().startswith("exact coverage"):
                        for cc in ws[c.row]:
                            if cc.column > c.column and cc.value == "Yes":
                                cc.value = "Yes" if want else "No"
                                hits += 1
                                break
            self.assertEqual(hits, 1)
            buf = io.BytesIO()
            wb.save(buf)
            self.assertEqual(
                ScheduleInput.from_xlsx(buf.getvalue()).settings["exact_coverage"], want)


class PresolveTests(unittest.TestCase):
    def _three_forced(self, **flags):
        return _si([Nurse(name=n, shifts={1: "ช"}) for n in "ABC"], (2, 0, 0), **flags)

    def test_too_many_forced_is_a_blocker_only_in_exact_mode(self):
        exact = [i for i in presolve.check(self._three_forced(exact_coverage=True)).errors
                 if i.code == "coverage_day"]
        self.assertEqual(len(exact), 1)
        self.assertIn("locked onto a shift", exact[0].message)
        self.assertIn("3 fixed but only 2 allowed", exact[0].message)
        floor = presolve.check(self._three_forced(exact_coverage=False)).errors
        self.assertEqual([i for i in floor if i.code == "coverage_day"], [])

    def test_presolve_agrees_with_solver(self):
        for exact in (True, False):
            si = self._three_forced(exact_coverage=exact)
            blocked = bool(presolve.check(si).errors)
            solved = run_schedule(si, 10).solved
            self.assertEqual(blocked, not solved, f"exact={exact}")

    def test_meeting_attendee_and_offward_do_not_count_as_over(self):
        si = _si([Nurse(name="A", shifts={1: "mtg"}), Nurse(name="B", shifts={1: "flt:ช"}),
                  Nurse(name="C", shifts={1: "ช"})], (1, 0, 0), exact_coverage=True)
        self.assertEqual([i for i in presolve.check(si).errors
                          if i.code == "coverage_day"], [])


class DiagnosisTextTests(unittest.TestCase):
    def test_over_and_short_read_differently(self):
        named = {"reason": "coverage", "coverage": [
            {"day": 3, "shift": "D", "required": 5, "band": "weekday", "side": "short"},
            {"day": 4, "shift": "D", "required": 5, "band": "weekday", "side": "over"}]}
        txt = render_text(named, None)
        self.assertIn("day 3: needs 5 D", txt)
        self.assertIn("day 4: too many nurses forced onto D - exactly 5 allowed", txt)


class StaffGroupTests(unittest.TestCase):
    def _ward(self, exact):
        nurses = ([Nurse(name=f"R{i}", staff_class="rn") for i in range(6)]
                  + [Nurse(name=f"P{i}", staff_class="pn") for i in range(4)])
        flags = {"exact_coverage": exact}
        for band in ("weekday", "weekend", "saturday", "sunday", "holiday"):
            for shift, rn, pn in (("day", 2, 1), ("evening", 1, 1), ("night", 1, 0)):
                flags[f"coverage_rn_{band}_{shift}"] = rn
                flags[f"coverage_pnna_{band}_{shift}"] = pn
        return _si(nurses, (2, 1, 1), days=5, **flags)

    def test_each_group_gets_exactly_its_own_count_every_day(self):
        res = run_schedule(self._ward(True), 30)
        self.assertTrue(res.solved)
        for grp, need in (("rn", (2, 1, 1)), ("pn_na", (1, 1, 0))):
            rows = [r for r in res.grid["rows"] if r["group"] == grp]
            for d in range(5):
                got = tuple(_count(rows, d, s) for s in ("ช", "บ", "ด"))
                self.assertEqual(got, need, f"{grp} day {d + 1}")


if __name__ == "__main__":
    unittest.main()
