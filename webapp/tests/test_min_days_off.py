"""constraint.md section 25: soft per-group monthly minimum of days off, through
the app layers (settings, inheritance, CSV, form, Excel card, per-group solve)."""

import io
import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402
from webapp.web.app import _settings_from_form  # noqa: E402

try:
    import openpyxl  # noqa: F401
    from tools.gen_templates import TEMPLATES, build_workbook
    _XLSX = True
except Exception:  # pragma: no cover
    _XLSX = False


def _lines(csv_text):
    block = csv_text.split("[settings]\n", 1)[1].split("\n[schedule]", 1)[0]
    return [ln for ln in block.splitlines() if ln]


def _classed(settings=None, rn=6, pn=4, days=5):
    nurses = ([Nurse(name=f"R{i}", staff_class="rn") for i in range(rn)]
              + [Nurse(name=f"P{i}", staff_class="pn") for i in range(pn)])
    base = {"num_days": days, "weekends": [], "head_nurse_special_shift": False}
    for band in ("weekday", "weekend", "saturday", "sunday", "holiday"):
        # RN 3/1/1 = 25 of 30 nurse-days; PN/NA 2/2/0 = 20 of 20 (nothing spare).
        for shift, r, p in (("day", 3, 2), ("evening", 1, 2), ("night", 1, 0)):
            base[f"coverage_rn_{band}_{shift}"] = r
            base[f"coverage_pnna_{band}_{shift}"] = p
    base.update(settings or {})
    return ScheduleInput.from_grid(ward_meta={"ward_name": "W", "tier": "ward"},
                                   settings=base, nurses=nurses)


class SettingsLayerTests(unittest.TestCase):
    def test_default_is_zero_everywhere(self):
        si = _classed()
        self.assertEqual([si.settings[k] for k in
                          ("min_days_off", "min_days_off_rn", "min_days_off_pnna")], [0, 0, 0])

    def test_group_keys_inherit_the_legacy_key_when_absent(self):
        old = ScheduleInput.from_dict({"ward_meta": {}, "nurses": [],
                                       "settings": {"num_days": 7, "min_days_off": 4}})
        self.assertEqual(old.min_days_off_for("rn"), 4)
        self.assertEqual(old.min_days_off_for("pn_na"), 4)
        self.assertEqual(old.min_days_off_for(None), 4)

    def test_explicit_group_key_wins_and_negatives_clamp(self):
        si = ScheduleInput.from_grid(
            ward_meta={"tier": "ward"}, nurses=[],
            settings={"num_days": 7, "min_days_off": 4, "min_days_off_pnna": 1,
                      "min_days_off_rn": -3})
        self.assertEqual(si.settings["min_days_off_rn"], 0)      # clamped
        self.assertEqual(si.settings["min_days_off_pnna"], 1)    # explicit, not 4

    def test_each_group_emits_its_own_value_under_the_legacy_name(self):
        si = _classed({"min_days_off": 9, "min_days_off_rn": 5, "min_days_off_pnna": 2})
        self.assertIn("min_days_off,5", _lines(si.to_solver_csv(group="rn")))
        self.assertIn("min_days_off,2", _lines(si.to_solver_csv(group="pn_na")))
        self.assertIn("min_days_off,9", _lines(si.to_solver_csv()))
        for g in ("rn", "pn_na"):
            self.assertFalse([l for l in _lines(si.to_solver_csv(group=g))
                              if l.startswith("min_days_off_")])

    def test_form_absent_group_keys_keep_current(self):
        # The PN/NA block is hidden until a nurse is classed: absent = not shown.
        cur = {"min_days_off_rn": 3, "min_days_off_pnna": 2, "min_days_off": 1}
        st = _settings_from_form({"min_days_off_rn": "6"}, cur)
        self.assertEqual(st["min_days_off_rn"], "6")             # submitted
        self.assertEqual(st["min_days_off_pnna"], 2)             # kept
        self.assertEqual(st["min_days_off"], 1)                  # kept

    @unittest.skipUnless(_XLSX, "openpyxl/templates unavailable")
    def test_excel_card_row_sets_both_groups(self):
        wb = build_workbook(TEMPLATES[0])
        ws = wb.worksheets[0]
        hits = 0
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.lower().startswith("min days off"):
                    for cc in ws[c.row]:
                        if cc.column > c.column and cc.value == "0 / 0":
                            cc.value = "5 / 2"
                            hits += 1
                            break
        self.assertEqual(hits, 1)
        buf = io.BytesIO()
        wb.save(buf)
        st = ScheduleInput.from_xlsx(buf.getvalue()).settings
        self.assertEqual((st["min_days_off_rn"], st["min_days_off_pnna"]), (5, 2))


def _group_stats(res, grp):
    """(smallest discretionary OFF count, double-shift days) of one group."""
    rows = [r for r in res.grid["rows"] if r["group"] == grp]
    return (min(sum(c == "off" for c in r["cells"]) for r in rows),
            sum("/" in c for r in rows for c in r["cells"]))


class PerGroupSolveTests(unittest.TestCase):
    def _solve(self, rn, pn):
        res = run_schedule(_classed({"min_days_off_rn": rn, "min_days_off_pnna": pn}), 20)
        self.assertTrue(res.solved, f"rn={rn} pn={pn} -> {res.solver_status}")
        return res

    def test_groups_are_independent_and_never_infeasible(self):
        # PN/NA has no spare day at all, so its baseline is 0 off and 0 doubles;
        # a minimum of 2 is bought entirely with doubles (observed 8). RN's
        # minimum of 2 is only partly reachable: it still solves, with more doubles.
        base = self._solve(0, 0)
        only_rn = self._solve(2, 0)
        only_pn = self._solve(0, 2)
        self.assertEqual(_group_stats(base, "pn_na"), (0, 0))
        self.assertGreaterEqual(_group_stats(only_pn, "pn_na")[0], 2)
        self.assertGreater(_group_stats(only_pn, "pn_na")[1], 0)
        self.assertGreater(_group_stats(only_rn, "rn")[1], _group_stats(only_pn, "rn")[1])
        # No leakage: a group's own minimum does not move the other group.
        self.assertEqual(_group_stats(only_rn, "pn_na"), (0, 0))
        self.assertEqual(_group_stats(only_pn, "rn"), _group_stats(base, "rn"))


if __name__ == "__main__":
    unittest.main()
