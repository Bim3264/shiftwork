"""Generated Excel roster template -> from_xlsx round-trip.

The shipped `.xlsx` workbooks are gitignored customer-facing artifacts, so no
test may read them. Instead these tests build a workbook **in memory** from the
same definition `tools/gen_templates.py` ships from, and push it through the
real parser. That makes template/parser drift a test failure rather than a
customer's failed upload (CODEBASE.md known failure mode 13).

Spec: docs/specs/2026-10-17-excel-template-parity.md 6.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from constant import LocaleShift, NEGATE_TOKENS, OFFWARD_TOKENS  # noqa: E402
from webapp.core.schedule_input import ScheduleInput  # noqa: E402

try:
    import openpyxl  # noqa: F401
    from tools.gen_templates import TEMPLATES, build_workbook
    _AVAILABLE = True
except ImportError:  # pragma: no cover - openpyxl or the generator is missing
    _AVAILABLE = False


def _spec(name_fragment):
    for spec in TEMPLATES:
        if name_fragment.lower() in spec.filename.lower():
            return spec
    raise AssertionError(f"no TemplateSpec matching {name_fragment!r}")


def _to_bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@unittest.skipUnless(_AVAILABLE, "openpyxl / tools.gen_templates unavailable")
class GeneratedTemplateRoundTripTests(unittest.TestCase):
    """The example workbook must express everything the CSV path can."""

    @classmethod
    def setUpClass(cls):
        cls.si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(_spec("example"))))

    def test_validates(self):
        self.si.validate()   # raises on a partially-classed roster

    def test_two_staff_groups(self):
        self.assertEqual(self.si.solve_groups(), ["rn", "pn_na"])
        classes = {n.staff_class for n in self.si.active_nurses()}
        self.assertNotIn("", classes, "every active nurse must carry a class")

    def test_holidays_list_survives(self):
        self.assertTrue(self.si.settings["holidays"],
                        "the example workbook must declare special holidays")
        for day in self.si.settings["holidays"]:
            self.assertGreaterEqual(day, 1)
            self.assertLessEqual(day, self.si.settings["num_days"])

    def test_holiday_band_is_its_own_band(self):
        s = self.si.settings
        holiday = (s["coverage_holiday_day"], s["coverage_holiday_evening"],
                   s["coverage_holiday_night"])
        weekend = (s["coverage_weekend_day"], s["coverage_weekend_evening"],
                   s["coverage_weekend_night"])
        self.assertNotEqual(holiday, weekend,
                            "Holiday need must be set, not inherited, in the example")

    def test_per_group_coverage_differs_from_legacy(self):
        s = self.si.settings
        # The PN/NA card rows are the ones the old split("/")[0] discarded.
        self.assertNotEqual(
            (s["coverage_pnna_weekday_day"], s["coverage_pnna_weekday_evening"],
             s["coverage_pnna_weekday_night"]),
            (s["coverage_weekday_day"], s["coverage_weekday_evening"],
             s["coverage_weekday_night"]),
        )
        # RN has no card row of its own: it inherits the general Weekday need.
        self.assertEqual(s["coverage_rn_weekday_day"], s["coverage_weekday_day"])

    def test_offward_cell_survives(self):
        tokens = {t for n in self.si.nurses for t in n.shifts.values()}
        self.assertTrue(tokens & OFFWARD_TOKENS,
                        f"no off-ward token round-tripped; got {sorted(tokens)}")

    def test_saturday_sunday_lists_and_needs_survive(self):
        s = self.si.settings
        self.assertEqual(s["saturdays"], [6, 13, 20, 27])
        self.assertEqual(s["sundays"], [7, 14, 21, 28])
        # Every listed day is also a rest day (weekends stays the union).
        self.assertTrue(set(s["saturdays"]) | set(s["sundays"]) <= set(s["weekends"]))
        sat = tuple(s[f"coverage_saturday_{k}"] for k in ("day", "evening", "night"))
        sun = tuple(s[f"coverage_sunday_{k}"] for k in ("day", "evening", "night"))
        wkd = tuple(s[f"coverage_weekend_{k}"] for k in ("day", "evening", "night"))
        self.assertNotEqual(sat, wkd, "Saturday need must be set, not inherited")
        self.assertNotEqual(sat, sun, "Saturday and Sunday are separate bands")
        self.assertEqual(s["coverage_pnna_saturday_day"], 1)

    def test_blank_templates_leave_saturday_sunday_empty(self):
        si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(_spec("Ward (blank)"))))
        self.assertEqual((si.settings["saturdays"], si.settings["sundays"]), ([], []))
        # ...so they inherit the Weekend need: nothing changes for a blank ward.
        for k in ("day", "evening", "night"):
            self.assertEqual(si.settings[f"coverage_saturday_{k}"],
                             si.settings[f"coverage_weekend_{k}"])

    def test_solver_csv_emits_saturday_sunday(self):
        for group in self.si.solve_groups():
            csv_text = self.si.to_solver_csv(group=group)
            self.assertIn("saturdays,6 13 20 27", csv_text)
            self.assertIn("sundays,7 14 21 28", csv_text)

    def test_solver_csv_emits_groups_and_holidays(self):
        for group in self.si.solve_groups():
            csv_text = self.si.to_solver_csv(group=group)
            self.assertIn("holidays,", csv_text)


@unittest.skipUnless(_AVAILABLE, "openpyxl / tools.gen_templates unavailable")
class AllGeneratedTemplatesTests(unittest.TestCase):

    def test_every_template_parses(self):
        self.assertGreaterEqual(len(TEMPLATES), 6)
        for spec in TEMPLATES:
            with self.subTest(spec.filename):
                si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(spec)))
                self.assertEqual(si.settings["num_days"], 31)
                # A blank template declares no special holidays; empty must mean
                # empty, never the weekends DEFAULT (failure mode 11).
                if not spec.example:
                    self.assertEqual(si.settings["holidays"], [])

    def test_blank_templates_have_no_nurses(self):
        for spec in TEMPLATES:
            if spec.example:
                continue
            with self.subTest(spec.filename):
                si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(spec)))
                self.assertEqual(si.nurses, [])

    def test_cell_dropdown_covers_every_valid_token(self):
        """Anti-drift: a new token in constant.py must reach the dropdown."""
        wb = build_workbook(_spec("Ward (blank)"))
        ws = wb["Roster"]
        listed = set()
        for dv in ws.data_validations.dataValidation:
            formula = str(dv.formula1).strip().strip('"')
            parts = {p.strip() for p in formula.split(",") if p.strip()}
            if parts & OFFWARD_TOKENS:
                listed = parts
                break
        # Derived, not spelled out: adding a member to LocaleShift or a token to
        # OFFWARD_TOKENS without updating the generator must fail here.
        expected = ({m.value for m in LocaleShift} | set(OFFWARD_TOKENS)
                    | set(NEGATE_TOKENS))
        self.assertEqual(listed, expected)

    def test_class_dropdown_present(self):
        ws = build_workbook(_spec("Ward (blank)"))["Roster"]
        formulas = {str(dv.formula1).strip().strip('"')
                    for dv in ws.data_validations.dataValidation}
        self.assertIn("RN,PN,NA", formulas)


@unittest.skipUnless(_AVAILABLE, "openpyxl / tools.gen_templates unavailable")
class ExampleTemplateSolvesTests(unittest.TestCase):
    """The example workbook must actually produce a schedule.

    `validate()` only checks the shape of the input, so it passed for months
    while the shipped example was INFEASIBLE: NurseF had a Night refer duty on
    day 21 and a Day shift on 22, which allow_night_to_day forbids. A head nurse
    opening the example to see what the tool does would have got nothing back.
    Solver-backed, so it is slower than the rest of this module.
    """

    def test_example_workbook_solves(self):
        from webapp.core.solver_runner import run_schedule

        si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(_spec("example"))))
        result = run_schedule(si, 90)
        self.assertTrue(
            result.solved,
            f"the example workbook does not solve: {result.solver_status}",
        )
        # Every group must solve, not just the roster as a whole.
        groups = (result.grid or {}).get("groups") or []
        self.assertEqual([g["key"] for g in groups], ["rn", "pn_na"])
        for group in groups:
            self.assertIn(group["solver_status"], ("OPTIMAL", "FEASIBLE"))

    def test_workbook_import_leaves_every_group_strict(self):
        # min_request_percent is web/CSV only (no Excel row), so an imported
        # workbook must not loosen either staff group.
        si = ScheduleInput.from_xlsx(_to_bytes(build_workbook(_spec("example"))))
        self.assertEqual(si.settings["min_request_percent_rn"], 100)
        self.assertEqual(si.settings["min_request_percent_pnna"], 100)


if __name__ == "__main__":
    unittest.main()
