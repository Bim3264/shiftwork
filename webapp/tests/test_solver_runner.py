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
