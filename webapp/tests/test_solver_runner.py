"""Tests for the solver adapter.

Exercises the real solver through the adapter on a small ward:
  - a feasible ward returns a solved result whose grid has one row per nurse
    (with names mapped back) and one cell per day,
  - an impossible ward (too few nurses to meet coverage) comes back INFEASIBLE
    with no grid — proving we surface unsolvable inputs rather than inventing a
    schedule.

Run from the project root:  python3 -m unittest webapp.tests.test_solver_runner
"""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402


def _feasible_ward():
    # 12 nurses / 7 days — comfortably solvable (same shape as the solver's own
    # test fixture).
    nurses = [Nurse(name=f"N{i}", type=("senior" if i < 2 else ("new" if i == 6 else "")))
              for i in range(12)]
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "RunnerWard", "tier": "ward"},
        settings={"num_days": 7, "weekends": [6, 7]},
        nurses=nurses,
    )


class SolverRunnerTest(unittest.TestCase):

    def test_feasible_ward_produces_grid(self):
        si = _feasible_ward()
        result = run_schedule(si, max_time_seconds=8)

        self.assertTrue(result.solved, f"expected solved, got {result.solver_status}")
        self.assertIn(result.solver_status, ("OPTIMAL", "FEASIBLE"))
        self.assertIsNotNone(result.grid)
        self.assertEqual(result.grid["days"], list(range(1, 8)))
        self.assertEqual(len(result.grid["rows"]), 12)
        # Names are mapped back from the input by position.
        self.assertEqual(result.grid["rows"][0]["name"], "N0")
        # Every nurse row has exactly one cell per day.
        for row in result.grid["rows"]:
            self.assertEqual(len(row["cells"]), 7)
        self.assertTrue(result.csv_text)
        self.assertIn("Solution found", result.log)

    def test_disabled_nurse_excluded_from_schedule(self):
        si = _feasible_ward()          # 12 nurses; disable one normal nurse
        si.nurses[5].active = False
        result = run_schedule(si, max_time_seconds=8)

        self.assertTrue(result.solved, f"expected solved, got {result.solver_status}")
        # Only the 11 active nurses appear in the schedule...
        self.assertEqual(len(result.grid["rows"]), 11)
        names = [r["name"] for r in result.grid["rows"]]
        self.assertNotIn("N5", names)          # the disabled one is absent
        # ...and remaining names map correctly (not shifted onto the wrong rows).
        self.assertEqual(names[0], "N0")
        self.assertEqual(len(set(names)), 11)

    def _partial_ward(self, min_percent):
        # 10 nurses / 3 weekdays; 4 request OFF on day 1. A weekday needs 5 Day
        # and 3 Night workers (disjoint), i.e. 8 working, so at most 2 of the 4
        # off-requests can be honoured.
        nurses = [
            Nurse(name=f"N{i}", type="", shifts=({1: "off"} if i < 4 else {}))
            for i in range(10)
        ]
        return ScheduleInput.from_grid(
            ward_meta={"ward_name": "Soft", "tier": "ward"},
            settings={"num_days": 3, "weekends": [], "head_nurse_special_shift": False,
                      "min_request_percent": min_percent, "relax_days_off": True},
            nurses=nurses,
        )

    def test_soft_requests_drop_minimally_and_report(self):
        result = run_schedule(self._partial_ward(0), max_time_seconds=8)
        self.assertTrue(result.solved, result.solver_status)
        rep = result.request_report
        self.assertIsNotNone(rep)
        self.assertEqual(rep["total"], 4)
        self.assertEqual(rep["accepted"], 2)          # the maximum possible
        self.assertEqual(len(rep["dropped"]), 2)
        self.assertTrue(all(d["kind"] == "holiday" and d["name"].startswith("N")
                            for d in rep["dropped"]))
        # The report is embedded in the grid for the UI to render.
        self.assertEqual(result.grid["request_report"]["accepted"], 2)

    def test_all_requests_hard_is_infeasible(self):
        # min%=100 keeps every request hard -> the same ward is infeasible.
        result = run_schedule(self._partial_ward(100), max_time_seconds=8)
        self.assertFalse(result.solved)
        self.assertEqual(result.solver_status, "INFEASIBLE")

    def test_lowered_coverage_makes_tiny_ward_feasible(self):
        # 4 nurses can't meet default coverage (weekday needs 5 Day + 3 Night
        # disjoint = 8), but they can with coverage lowered to 1/1/1.
        nurses = [Nurse(name=f"N{i}") for i in range(4)]
        base = dict(num_days=3, weekends=[], head_nurse_special_shift=False)

        infeasible = run_schedule(
            ScheduleInput.from_grid(ward_meta={"tier": "ward"}, settings=base,
                                    nurses=nurses),
            max_time_seconds=8)
        self.assertFalse(infeasible.solved)

        feasible = run_schedule(
            ScheduleInput.from_grid(
                ward_meta={"tier": "ward"},
                settings={**base, "coverage_weekday_day": 1,
                          "coverage_weekday_evening": 1, "coverage_weekday_night": 1},
                nurses=nurses),
            max_time_seconds=8)
        self.assertTrue(feasible.solved, feasible.solver_status)

    def test_impossible_ward_is_infeasible(self):
        # Two nurses cannot satisfy weekday coverage minimums (Day needs 5), so
        # the model is genuinely infeasible. Two (not one) nurses keeps the
        # senior-nurse rule valid so we reach the solver rather than a crash.
        # cwd must be restored even on this unsolved path.
        cwd_before = os.getcwd()
        si = ScheduleInput.from_grid(
            ward_meta={"ward_name": "TinyWard", "tier": "ward"},
            settings={"num_days": 3, "weekends": []},
            nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
        )
        result = run_schedule(si, max_time_seconds=8)

        self.assertFalse(result.solved)
        self.assertEqual(result.solver_status, "INFEASIBLE")
        self.assertIsNone(result.grid)
        self.assertIsNone(result.csv_text)
        self.assertEqual(os.getcwd(), cwd_before, "cwd must be restored after solve")


if __name__ == "__main__":
    unittest.main(verbosity=2)
