"""Tests for the vacation ('vac') shift type.

A vacation behaves like an off-day for the solver (the nurse is assigned OFF),
but differs from an ordinary 'off' request in two ways that these tests pin down:

  * ENFORCE_VACATION (default True): every requested vacation is a HARD
    constraint the solver can never drop. An 'off' request, by contrast, is
    only ever requested and can be dropped in soft mode.
  * A granted vacation renders as the distinct symbol 'vac' in the output grid,
    not as 'off'.

The toggle is exercised with a paired over-constrained scenario (all nurses on
leave the same weekday): enforced -> INFEASIBLE; not-enforced + soft -> FEASIBLE
with vacations dropped. If the enforce flag were ignored, or vacations weren't
droppable when un-enforced, one of the pair would flip and fail.

Run with:  python3 -m unittest test_vacation
"""

import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from ortools.sat.python import cp_model  # noqa: E402
import shiftwork as sw  # noqa: E402
from constant import LocaleShift  # noqa: E402

_NUM_DAYS = 7
_WEEKENDS = [5, 6]  # 0-based, matches "weekends,6 7" (days 6 and 7)


def _ward_csv(rows, *, enforce_vacation=True, min_request_percent=100):
    """Build a sectioned ward CSV. `rows` is a list of (name, type, {daynum: token})."""
    header = ["Name", "Type"] + [str(d) for d in range(1, _NUM_DAYS + 1)]
    lines = [",".join(header)]
    for name, ntype, cells in rows:
        row = [name, ntype] + [cells.get(d, "") for d in range(1, _NUM_DAYS + 1)]
        lines.append(",".join(row))
    schedule = "\n".join(lines)
    return f"""[ward]
ward_name,VacWard
month,7
year,2026
tier,ward
license_key,

[settings]
num_days,7
weekends,6 7
allow_evening_to_night,false
allow_night_to_day,false
head_nurse_special_shift,true
enforce_vacation,{"true" if enforce_vacation else "false"}
min_request_percent,{min_request_percent}

[schedule]
{schedule}
"""


def _twelve(vac_day=None, vac_for=None, **kw):
    """12-nurse ward (2 seniors, 1 new). vac_for = set of nurse indices that get
    a 'vac' on day `vac_day` (1-based); if vac_for is None and vac_day set, all get it."""
    rows = []
    for i in range(12):
        ntype = "senior" if i < 2 else ("new" if i == 6 else "")
        cells = {}
        if vac_day is not None and (vac_for is None or i in vac_for):
            cells[vac_day] = "vac"
        rows.append((f"N{i}", ntype, cells))
    return _ward_csv(rows, **kw)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._prev_cwd = os.getcwd()
        self._workdir = tempfile.mkdtemp(prefix="shiftwork_vac_")
        os.makedirs(os.path.join(self._workdir, "input"))
        os.makedirs(os.path.join(self._workdir, "output"))
        self._input_path = os.path.join(self._workdir, "input", "vac.csv")
        os.chdir(self._workdir)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _write(self, csv_text):
        with open(self._input_path, "w", encoding="utf-8") as f:
            f.write(csv_text)

    def _solve(self, csv_text, max_time_seconds=8):
        self._write(csv_text)
        s = sw.Solver(self._input_path, _NUM_DAYS, _WEEKENDS)
        status = s.run(max_time_seconds=max_time_seconds)
        return s, status

    def _grid(self):
        import pandas as pd
        path = "output/VacWard_2026-07 Final.csv"
        return pd.read_csv(path, index_col=0, encoding="utf-8-sig")


class VacationEnforcedTest(_Fixture):
    def test_enforced_vacation_is_granted_and_rendered_vac(self):
        """A single enforced vacation on a slack (weekend) day is honoured, and
        the granted OFF renders as the distinct 'vac' symbol, not 'off'."""
        # N2 takes leave on day 6 (a weekend, lower coverage -> comfortably feasible).
        s, status = self._solve(_twelve(vac_day=6, vac_for={2}))
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE),
                      "one vacation on a weekend day must stay feasible")
        grid = self._grid()
        self.assertEqual(grid.iloc[2, 5], LocaleShift.VACATION.value,
                         "N2's day-6 cell should render as 'vac'")

    def test_enforced_vacation_that_breaks_coverage_is_infeasible(self):
        """With every nurse's leave enforced on the same weekday, nobody is left
        to meet coverage -> the model is provably INFEASIBLE (not merely a
        timeout: enforcement means the requests can't be dropped)."""
        _, status = self._solve(_twelve(vac_day=1))  # all 12, weekday, enforced
        self.assertEqual(status, cp_model.INFEASIBLE,
                         "all-hands enforced leave on a weekday must be infeasible")


class VacationNotEnforcedTest(_Fixture):
    def test_unenforced_vacation_drops_in_soft_mode(self):
        """The same all-hands scenario becomes solvable when vacations are NOT
        enforced and soft mode is on: the solver drops enough leave to cover the
        day. This is the 'off'-like behaviour the toggle switches to."""
        s, status = self._solve(
            _twelve(vac_day=1, enforce_vacation=False, min_request_percent=0)
        )
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE),
                      "un-enforced leave should be droppable to reach feasibility")
        # Coverage on day 1 must be met -> at least some nurses are working, i.e.
        # not every cell in column '1' is a day-off/vacation.
        grid = self._grid()
        col1 = [str(v) for v in grid.iloc[:, 0].tolist()]
        working = [v for v in col1 if v not in (LocaleShift.OFF.value,
                                                LocaleShift.VACATION.value)]
        self.assertGreaterEqual(len(working), 5,
                                "day-1 coverage should force several nurses to work")
        self.assertIsNotNone(s.request_report,
                             "soft mode should produce a request report")
        self.assertGreater(len(s.request_report["dropped"]), 0,
                           "some vacation requests must have been dropped")


if __name__ == "__main__":
    unittest.main()
