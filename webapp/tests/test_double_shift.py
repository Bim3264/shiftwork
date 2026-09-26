"""Double-shift request cells (ช/บ = Day+Evening, ด/บ = Night+Evening).

Regression for the importer bug found 2026-09-26: DataImporter merged a cell's
entries into {day: shift}, so 'ช/บ' was honoured as บ only and 'ด/บ' was dropped
entirely. A double-shift cell is now ONE request (one quota slot) for TWO shifts,
kept or dropped as a unit, and the pre-solve mirror matches.

Run from the project root:
  python3 -m unittest webapp.tests.test_double_shift
"""

import os
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from constant import Shift  # noqa: E402
from dataimporter import DataImporter  # noqa: E402
from webapp.core import presolve  # noqa: E402
from webapp.core.schedule_input import Nurse, ScheduleInput  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402


def _si(cells_by_nurse, *, n=6, tier="ward", types=None, **settings):
    """n nurses, 7 weekdays (no weekends), head rule off, coverage 1/1/1."""
    s = dict(num_days=7, head_nurse_special_shift=False,
             coverage_weekday_day=1, coverage_weekday_evening=1,
             coverage_weekday_night=1, coverage_weekend_day=1,
             coverage_weekend_evening=1, coverage_weekend_night=1)
    s.update(settings)
    types = types or {}
    nurses = [Nurse(name=f"N{i}", type=types.get(i, ""), shifts=dict(cells_by_nurse.get(i, {})))
              for i in range(n)]
    si = ScheduleInput.from_grid(ward_meta={"tier": tier}, settings=s, nurses=nurses)
    si.settings["weekends"] = []
    return si


def _import(si, max_req=5):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(si.to_solver_csv())
        return DataImporter(path, 5, max_req).transform()
    finally:
        os.unlink(path)


def _cell(result, name, day):
    row = next(r for r in result.grid["rows"] if r["name"] == name)
    return row["cells"][result.grid["days"].index(day)]


class ImporterTests(unittest.TestCase):

    def test_double_cells_keep_both_shifts(self):
        data = _import(_si({2: {1: "ช/บ", 2: "ด/บ", 3: "ช"}}))
        self.assertEqual(data.reqShifts[2], {
            0: [Shift.DAY, Shift.EVENING],
            1: [Shift.NIGHT, Shift.EVENING],
            2: [Shift.DAY],
        })

    def test_quota_counts_cells_and_keeps_cells_whole(self):
        # free tier: 2 requests/nurse. Three cells -> 2 kept, each kept whole.
        si = _si({2: {1: "ช/บ", 2: "ด/บ", 3: "ช/บ"}}, tier="free")
        for _ in range(10):  # sampling is random; check the invariant repeatedly
            req = _import(si).reqShifts[2]
            self.assertEqual(len(req), 2)
            for day, shifts in req.items():
                self.assertEqual(len(shifts), 2, f"day {day} half-kept: {shifts}")
        # Exactly at quota: nothing dropped.
        req = _import(_si({2: {1: "ช/บ", 2: "ด/บ"}}, tier="free")).reqShifts[2]
        self.assertEqual(set(req), {0, 1})


class SolverTests(unittest.TestCase):

    def test_solver_assigns_both_shifts(self):
        si = _si({2: {3: "ช/บ"}, 3: {4: "ด/บ"}})
        res = run_schedule(si, 20)
        self.assertTrue(res.solved, res.solver_status)
        self.assertEqual(_cell(res, "N2", 3), "ช/บ")
        self.assertEqual(_cell(res, "N3", 4), "ด/บ")

    def test_soft_mode_reports_a_double_cell_as_one_request(self):
        si = _si({2: {3: "ช/บ"}, 3: {4: "ด/บ"}, 4: {5: "ช"}}, min_request_percent=50)
        res = run_schedule(si, 20)
        self.assertTrue(res.solved, res.solver_status)
        self.assertEqual(res.request_report["total"], 3)

    def test_new_nurse_night_evening_request_is_skipped_not_infeasible(self):
        si = _si({2: {3: "ด/บ"}}, types={2: "new"})
        report = presolve.check(si)
        self.assertEqual(report.errors, [])
        self.assertIn("new_request", {i.code for i in report.warnings})
        res = run_schedule(si, 20)
        self.assertTrue(res.solved, res.solver_status)
        self.assertNotEqual(_cell(res, "N2", 3), "ด/บ")


class PresolveMirrorTests(unittest.TestCase):

    def test_double_cell_forces_both_shifts_in_presolve(self):
        # ช/บ on day 2 then ด on day 3 with Evening->Night banned: the บ half of
        # the double conflicts. Presolve must flag it, and the solver agrees.
        si = _si({2: {2: "ช/บ", 3: "ด"}})
        report = presolve.check(si)
        self.assertIn("request_transition", {i.code for i in report.errors})
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_double_cell_uses_one_quota_slot(self):
        si = _si({2: {1: "ช/บ", 2: "ด/บ"}}, tier="free")
        self.assertNotIn("quota_shift", {i.code for i in presolve.check(si).warnings})


if __name__ == "__main__":
    unittest.main()
