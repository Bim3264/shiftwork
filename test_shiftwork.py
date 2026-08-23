"""Tests for the Solver lifecycle refactor (build/solve/run split + output writer).

These tests exercise the *contract* of the refactor, not solver internals:

  - Constructing a Solver must set up state and variables but must NOT solve
    (no status, no output file) — the point of splitting the pipeline out of
    __init__.
  - build() must assemble the model (constraints appear) but still not solve
    or write anything.
  - solve() is what produces a feasible schedule and writes the CSV — i.e. the
    output-writing step only fires on solve, never on construction or build.

Each test runs against a small, self-contained ward fixture in a temporary
working directory so the real input/ and output/ folders are never touched.

Run with:  python3 test_shiftwork.py
"""

import os
import sys
import shutil
import tempfile
import unittest

# The Solver writes to a relative "output/" dir, so tests chdir into a temp
# workspace. Import shiftwork *before* that chdir (its sibling-module imports
# resolve against the project dir) and make sure the project dir is importable.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from ortools.sat.python import cp_model  # noqa: E402
import shiftwork as sw  # noqa: E402
from constant import LocaleShift  # noqa: E402

# A small ward that is comfortably feasible: 12 nurses over 7 days, two seniors,
# one new nurse. Small enough that solve() reaches a feasible solution quickly.
_MINI_WARD_CSV = """[ward]
ward_name,MiniWard
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

[schedule]
Name,Type,1,2,3,4,5,6,7
N0,senior,,,,,,,
N1,senior,,,,,,,
N2,,,,,,,,
N3,,,,,,,,
N4,,,,,,,,
N5,,,,,,,,
N6,new,,,,,,,
N7,,,,,,,,
N8,,,,,,,,
N9,,,,,,,,
N10,,,,,,,,
N11,,,,,,,,
"""

_NUM_NURSES = 12
_NUM_DAYS = 7
_WEEKENDS = [5, 6]  # 0-based, matches "weekends,6 7"
_OUTPUT_FILE = "output/MiniWard_2026-07 Final.csv"

# Every rendered cell must be one of these symbols (a valid shift or a day off).
_VALID_SYMBOLS = {
    LocaleShift.DAY.value,
    LocaleShift.EVENING.value,
    LocaleShift.NIGHT.value,
    LocaleShift.DAY_EVENING.value,
    LocaleShift.NIGHT_EVENING.value,
    LocaleShift.OFF.value,
}


class _FixtureTest(unittest.TestCase):
    """Shared fixture: an isolated temp workspace holding the mini ward."""

    def setUp(self):
        # Fresh temp workspace per test: input/ (the fixture) + output/ (empty).
        self._prev_cwd = os.getcwd()
        self._workdir = tempfile.mkdtemp(prefix="shiftwork_test_")
        os.makedirs(os.path.join(self._workdir, "input"))
        os.makedirs(os.path.join(self._workdir, "output"))
        self._input_path = os.path.join(self._workdir, "input", "mini.csv")
        with open(self._input_path, "w", encoding="utf-8") as f:
            f.write(_MINI_WARD_CSV)
        os.chdir(self._workdir)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _new_solver(self):
        return sw.Solver(self._input_path, _NUM_DAYS, _WEEKENDS)

    def _output_files(self):
        return set(os.listdir("output"))


class SolverLifecycleTest(_FixtureTest):
    """The build/solve/run split: construction and build() must not solve or
    write; only solve() does."""

    def test_construction_is_side_effect_free(self):
        """Constructing a Solver loads data and builds variables, but must not
        solve: no status is set and no output file is produced."""
        s = self._new_solver()

        self.assertFalse(hasattr(s, "status"),
                         "__init__ should not solve — 'status' was set")
        self.assertEqual(self._output_files(), set(),
                         "__init__ should not write any output file")

        # It *should* have loaded the data and created the decision variables,
        # otherwise the split went too far and broke setup.
        self.assertEqual(s.num_nurses, _NUM_NURSES)
        self.assertEqual(s.num_days, _NUM_DAYS)
        self.assertEqual(len(s.assignments), _NUM_NURSES)

    def test_build_assembles_model_without_solving(self):
        """build() must add constraints to the model but still not solve or
        write anything — solving is solve()'s job alone."""
        s = self._new_solver()

        constraints_before = len(s.model.Proto().constraints)
        s.build()
        constraints_after = len(s.model.Proto().constraints)

        self.assertGreater(constraints_after, constraints_before,
                           "build() should add constraints to the model")
        self.assertFalse(hasattr(s, "status"),
                         "build() should not solve — 'status' was set")
        self.assertEqual(self._output_files(), set(),
                         "build() should not write any output file")

    def test_output_is_written_only_on_solve(self):
        """The output CSV must appear only after solve(), never after build()."""
        s = self._new_solver()
        s.build()
        self.assertEqual(self._output_files(), set(),
                         "no output should exist before solve()")

        status = s.solve(max_time_seconds=8)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE),
                      "small fixture should be solvable")
        self.assertTrue(os.path.exists(_OUTPUT_FILE),
                        "solve() should write the output CSV")

    def test_run_produces_wellformed_schedule(self):
        """run() (build + solve) yields a feasible schedule whose written grid
        has one row per nurse, one column per day, and only valid symbols."""
        s = self._new_solver()
        status = s.run(max_time_seconds=8)

        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertTrue(os.path.exists(_OUTPUT_FILE))

        import pandas as pd
        grid = pd.read_csv(_OUTPUT_FILE, index_col=0, encoding="utf-8-sig")

        self.assertEqual(grid.shape, (_NUM_NURSES, _NUM_DAYS),
                         "grid should be num_nurses rows x num_days columns")

        invalid = {
            str(v) for v in grid.to_numpy().ravel()
            if str(v) not in _VALID_SYMBOLS
        }
        self.assertEqual(invalid, set(),
                         f"grid contains invalid shift symbols: {invalid}")


class ConstraintRuleTest(_FixtureTest):
    """Each decomposed rule group must still be enforced after the split.

    Pattern: build the real model, force the situation a rule forbids, and
    assert the solver proves it infeasible. If a rule were dropped in the
    refactor, the forced assignment would become satisfiable and the test would
    fail — that is what makes these tests meaningful rather than decorative.

    Nurse indices in the fixture: 0,1 are seniors (special rules), 6 is a new
    nurse, everyone else (e.g. 2) is a normal nurse. Days 0 and 1 are weekdays.
    """

    def _solve_forced(self, forcings):
        """Build a fresh model, apply each (n, d, shift) forcing, solve briefly,
        return the CP-SAT status."""
        s = self._new_solver()
        s.build()
        for (n, d, shift) in forcings:
            s.model.Add(s.assignments[n][d][shift] == 1)
        return s.solve(max_time_seconds=8)

    def test_new_nurse_cannot_work_evening_and_night_same_day(self):
        from constant import Shift
        status = self._solve_forced([
            (6, 0, Shift.EVENING),
            (6, 0, Shift.NIGHT),
        ])
        self.assertEqual(status, cp_model.INFEASIBLE,
                         "new nurse forced into Evening+Night should be infeasible")

    def test_normal_nurse_may_work_evening_and_night_same_day(self):
        # Contrast case: the Evening+Night ban is specific to new nurses, so a
        # normal nurse doing the same must stay feasible. This guards against a
        # future over-broad version of the rule.
        from constant import Shift
        status = self._solve_forced([
            (2, 0, Shift.EVENING),
            (2, 0, Shift.NIGHT),
        ])
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE),
                      "normal nurse Evening+Night should remain allowed")

    def test_same_day_night_and_day_is_illegal(self):
        from constant import Shift
        status = self._solve_forced([
            (2, 0, Shift.NIGHT),
            (2, 0, Shift.DAY),
        ])
        self.assertEqual(status, cp_model.INFEASIBLE,
                         "same-day Night+Day should be infeasible for any nurse")

    def test_night_to_day_transition_is_illegal(self):
        # mini fixture sets allow_night_to_day=false, so NIGHT on day 0 followed
        # by DAY on day 1 must be rejected.
        from constant import Shift
        status = self._solve_forced([
            (2, 0, Shift.NIGHT),
            (2, 1, Shift.DAY),
        ])
        self.assertEqual(status, cp_model.INFEASIBLE,
                         "Night→Day across consecutive days should be infeasible")


if __name__ == "__main__":
    unittest.main(verbosity=2)
