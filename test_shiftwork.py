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

import contextlib
import glob
import io
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
from constant import LocaleShift, Shift  # noqa: E402

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



def _diag_ward_csv(num_days, nurse_rows):
    """Sectioned CSV for the infeasibility-diagnosis fixtures: a 'department'
    tier ward with coverage 2/2/2 in both day bands and no head-nurse special
    shift (so no nurse is a senior with extra rules). `nurse_rows` is a list of
    (name, {day_number: request_text}) pairs."""
    header = ",".join(["Name", "Type"] + [str(d) for d in range(1, num_days + 1)])
    rows = []
    for name, reqs in nurse_rows:
        cells = [reqs.get(d, "") for d in range(1, num_days + 1)]
        rows.append(",".join([name, ""] + cells))
    lines = [
        "[ward]",
        "ward_name,DiagWard",
        "month,7",
        "year,2026",
        "tier,department",
        "license_key,",
        "",
        "[settings]",
        f"num_days,{num_days}",
        "weekends,6 7",
        "allow_evening_to_night,false",
        "allow_night_to_day,false",
        "head_nurse_special_shift,false",
        "coverage_weekday_day,2",
        "coverage_weekday_evening,2",
        "coverage_weekday_night,2",
        "coverage_weekend_day,2",
        "coverage_weekend_evening,2",
        "coverage_weekend_night,2",
        "",
        "[schedule]",
        header,
    ] + rows
    return "\n".join(lines) + "\n"


class DiagnoseTest(_FixtureTest):
    """Solver.diagnose(): an INFEASIBLE solve must be explained by naming the
    request cells, or the day+shift coverage, that cannot be satisfied.

    Each test would fail if the rule it covers were removed: with the request
    gating gone the requests pass has an empty core (reason would fall through
    to 'coverage'/'structural'); with the coverage gating gone the second pass
    has an empty core (reason would be 'structural')."""

    def _diag_solver(self, num_days, nurse_rows):
        with open(self._input_path, "w", encoding="utf-8") as f:
            f.write(_diag_ward_csv(num_days, nurse_rows))
        with contextlib.redirect_stdout(io.StringIO()):
            return sw.Solver(self._input_path, num_days, [5, 6])

    def _run_quiet(self, solver, max_time_seconds=10):
        with contextlib.redirect_stdout(io.StringIO()):
            return solver.run(max_time_seconds=max_time_seconds)

    def _diagnose_quiet(self, solver, max_time_seconds):
        with contextlib.redirect_stdout(io.StringIO()):
            return solver.diagnose(max_time_seconds)

    def _conflicting_requests_solver(self):
        # 8 nurses, coverage 2/2/2. Day 5: six nurses want off and two want the
        # Day shift, so Evening (2) + Night (2) cannot both be covered.
        day_symbol = LocaleShift.DAY.value
        rows = []
        for i in range(8):
            reqs = {5: "off"} if i < 6 else {5: day_symbol}
            rows.append((f"N{i}", reqs))
        return self._diag_solver(10, rows)

    def _coverage_solver(self):
        # 3 nurses cannot cover 2 Day + 2 Evening + 2 Night on a single day.
        return self._diag_solver(5, [(f"N{i}", {}) for i in range(3)])

    def test_diagnose_names_conflicting_requests(self):
        s = self._conflicting_requests_solver()
        status = self._run_quiet(s)
        self.assertEqual(status, cp_model.INFEASIBLE,
                         "fixture must be infeasible for the diagnosis to mean anything")

        diag = self._diagnose_quiet(s, 15)

        self.assertEqual(diag["reason"], "requests")
        self.assertGreaterEqual(len(diag["requests"]), 3)
        self.assertTrue(all(r["day"] == 5 for r in diag["requests"]),
                        f"every blamed request must be on day 5: {diag['requests']}")
        self.assertTrue(any(r["kind"] == "holiday" for r in diag["requests"]),
                        "at least one blamed request must be a day-off (holiday)")
        self.assertEqual(diag["coverage"], [],
                         "a requests diagnosis must not also blame coverage")
        self.assertGreater(diag["gated"], 0)

    def test_diagnose_names_coverage_day(self):
        s = self._coverage_solver()
        status = self._run_quiet(s)
        self.assertEqual(status, cp_model.INFEASIBLE)

        diag = self._diagnose_quiet(s, 15)

        self.assertEqual(diag["reason"], "coverage")
        self.assertTrue(diag["coverage"],
                        "coverage core must name at least one day+shift")
        for rec in diag["coverage"]:
            self.assertIn(rec["shift"], {"day", "evening", "night"})
            self.assertEqual(rec["required"], 2)
        self.assertEqual(diag["requests"], [],
                         "no requests exist, so none may be blamed")

    def test_diagnose_rejects_solved_solver(self):
        s = self._new_solver()
        status = self._run_quiet(s, max_time_seconds=8)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))

        with self.assertRaises(RuntimeError):
            self._diagnose_quiet(s, 5)

    def test_diagnose_zero_budget(self):
        s = self._coverage_solver()
        self.assertEqual(self._run_quiet(s), cp_model.INFEASIBLE)

        diag = self._diagnose_quiet(s, 0)

        self.assertEqual(diag["reason"], "undetermined")
        self.assertEqual(diag["seconds"], 0.0)
        self.assertEqual(diag["requests"], [])
        self.assertEqual(diag["coverage"], [])

    def test_build_without_objective_still_feasible(self):
        s = self._new_solver()
        s.build(with_objective=False)
        with contextlib.redirect_stdout(io.StringIO()):
            status = s.solve(max_time_seconds=20, write_output=False)

        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(glob.glob("output/*Final.csv"), [],
                         "write_output=False must not create an output CSV")


# ---------------------------------------------------------------------------
# Off-ward duty (constraint.md section 21) and the special-holiday band (22).
#
# These build their own tiny wards through _ward_csv() so each test controls
# exactly the coverage numbers and the weekends/holidays lists. Every assertion
# is chosen so it FAILS if the rule under test is removed - "a schedule was
# produced" is never the assertion on its own.
# ---------------------------------------------------------------------------

_SOLVED = (cp_model.OPTIMAL, cp_model.FEASIBLE)


def _ward_csv(num_days, rows, cov=(1, 1, 1), cov_hol=None, senior="false",
              weekends="", holidays="", extra=""):
    """Build a ward CSV. `extra` is raw text appended to [settings] (saturdays /
    sundays lists and coverage_saturday_* / coverage_sunday_* keys). `rows` = [(name, type, [cells...])].

    `cov` fills BOTH the weekday and the weekend band. `cov_hol` (optional) adds
    explicit coverage_holiday_* keys; None omits them so the holiday band must
    inherit this file's weekend numbers. `holidays` is always written (possibly
    empty) so a blank value is exercised as "empty means empty".
    """
    head = (
        "[ward]\nward_name,Smoke\ntier,department\n\n[settings]\n"
        f"num_days,{num_days}\nweekends,{weekends}\nholidays,{holidays}\n"
        "allow_evening_to_night,false\nallow_night_to_day,false\n"
        f"head_nurse_special_shift,{senior}\n"
        "min_request_percent,100\nrelax_days_off,false\nenforce_vacation,true\n"
        f"coverage_weekday_day,{cov[0]}\ncoverage_weekday_evening,{cov[1]}\n"
        f"coverage_weekday_night,{cov[2]}\n"
        f"coverage_weekend_day,{cov[0]}\ncoverage_weekend_evening,{cov[1]}\n"
        f"coverage_weekend_night,{cov[2]}\n"
    )
    if cov_hol is not None:
        head += (f"coverage_holiday_day,{cov_hol[0]}\n"
                 f"coverage_holiday_evening,{cov_hol[1]}\n"
                 f"coverage_holiday_night,{cov_hol[2]}\n")
    head += extra
    head += ("\n[schedule]\nName,Type,"
             + ",".join(str(d) for d in range(1, num_days + 1)) + "\n")
    for name, ntype, cells in rows:
        padded = list(cells) + [""] * (num_days - len(cells))
        head += f"{name},{ntype}," + ",".join(padded) + "\n"
    return head


class _SmallWardTest(unittest.TestCase):
    """Temp workspace + a `_run()` that builds and (optionally) solves a ward."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self._workdir = tempfile.mkdtemp(prefix="shiftwork_smoke_")
        os.makedirs(os.path.join(self._workdir, "input"))
        os.makedirs(os.path.join(self._workdir, "output"))
        os.chdir(self._workdir)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _run(self, num_days, rows, solve=True, **kw):
        """Return (solver, status_or_None, captured_stdout)."""
        path = os.path.join(self._workdir, "input", "w.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_ward_csv(num_days, rows, **kw))
        log = io.StringIO()
        status = None
        with contextlib.redirect_stdout(log):
            s = sw.Solver(path, num_days, [], allow_shift_e_n=False,
                          allow_shift_n_d=False)
            if solve:
                status = s.run(max_time_seconds=10)
            else:
                s.loadReqShifts(path, s.MAX_REQ_HOLIDAYS, s.MAX_REQ_SHIFT)
        return s, status, log.getvalue()

    @staticmethod
    def _plain(n, days=7, seniors=()):
        return [("N%d" % i, "senior" if i in seniors else "", [""] * days)
                for i in range(n)]


class SeniorByTypeTest(_SmallWardTest):
    """constraint.md section 16/17: seniority comes from the Type column, not
    from row position. Any number of seniors (zero included)."""

    def _check_seniors(self, s, senior_set):
        for n in range(s.num_nurses):
            for d in range(s.num_days):
                if n in senior_set:
                    off = d in s.weekends
                    self.assertEqual(s.solver.Value(s.assignments[n][d][Shift.EVENING]), 0)
                    self.assertEqual(s.solver.Value(s.assignments[n][d][Shift.NIGHT]), 0)
                    if off:
                        self.assertEqual(s.solver.Value(s.assignments[n][d][Shift.OFF]), 1)

    def test_senior_in_the_middle_is_constrained_and_rows_0_1_are_not(self):
        # Regression: rows 0/1 used to be hard-wired as head/deputy.
        s, st, _ = self._run(7, self._plain(8, seniors=(4,)), cov=(1, 1, 1),
                             senior="true", weekends="6 7")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.senior_nurse_indices, {4})
        self._check_seniors(s, {4})
        # Rows 0/1 are free: with 1 senior pinned to Day/OFF, evening+night
        # coverage (1+1 every day) must come from the other nurses; rows 0 and 1
        # are not forced off weekends.
        self.assertTrue(any(
            s.solver.Value(s.assignments[n][d][Shift.OFF]) == 0
            for n in (0, 1) for d in (5, 6)),
            "nurses 0/1 must no longer be pinned OFF on weekends")

    def test_no_senior_makes_the_rule_a_noop_and_ward_solves(self):
        s, st, _ = self._run(7, self._plain(6), cov=(1, 1, 1), senior="true",
                             weekends="6 7")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.senior_nurse_indices, set())

    def test_thai_senior_synonym_is_accepted(self):
        rows = self._plain(6)
        rows[3] = ("N3", "อาวุโส", [""] * 7)
        s, st, _ = self._run(7, rows, cov=(1, 1, 1), senior="true", weekends="6 7")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.senior_nurse_indices, {3})

    def test_three_seniors_are_all_constrained(self):
        s, st, _ = self._run(7, self._plain(9, seniors=(1, 5, 8)), cov=(1, 1, 1),
                             senior="true", weekends="6 7")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.senior_nurse_indices, {1, 5, 8})
        self._check_seniors(s, {1, 5, 8})


class OffWardDutyTest(_SmallWardTest):
    """constraint.md section 21: float / refer cells are forced, exclusive, and
    excluded from the ward's coverage pool."""

    def test_offward_shift_is_forced_and_exclusive(self):
        # B9. ref:<night> on day 3 for nurse 5 in a roomy ward.
        rows = self._plain(10)
        rows[5] = ("N5", "", ["", "", "ref:ด", "", "", "", ""])
        s, st, _ = self._run(7, rows, cov=(2, 1, 1))
        self.assertIn(st, _SOLVED)
        val = {sh: s.solver.Value(s.assignments[5][2][sh]) for sh in Shift}
        self.assertEqual(val[Shift.NIGHT], 1)
        self.assertEqual(val[Shift.DAY], 0)
        self.assertEqual(val[Shift.EVENING], 0)
        self.assertEqual(val[Shift.OFF], 0, "an off-ward shift is a worked shift")
        self.assertIn(2, s._offward_applied.get(5, {}))

    def test_offward_excluded_from_coverage(self):
        # B10. 2 nurses, 1 day, coverage 1/1/1. Baseline solves; with A off-ward
        # on Day, A no longer counts toward Day coverage, so B is the only Day
        # candidate while B is also needed elsewhere -> INFEASIBLE. If the
        # nurse stayed in the coverage pool this would still solve.
        s, st, _ = self._run(1, [("A", "", [""]), ("B", "", [""])])
        self.assertIn(st, _SOLVED, "baseline ward must be feasible")
        s, st, _ = self._run(1, [("A", "", ["flt:ช"]), ("B", "", [""])])
        self.assertEqual(st, cp_model.INFEASIBLE,
                         "an off-ward nurse must leave the coverage pool")
        self.assertEqual(s._offward_excluded, {(0, Shift.DAY): {0}})

    def test_offward_senior_conflict_is_skipped(self):
        # B12. The head nurse (nurse 0, 'senior') is pinned to Day/OFF by the
        # senior rule, so ref:<night> would be infeasible. The cell must be
        # dropped loudly, not honoured (known failure mode 1).
        rows = self._plain(10)
        rows[0] = ("N0", "senior", ["", "", "ref:ด", "", "", "", ""])
        s, st, log = self._run(7, rows, cov=(2, 1, 1), senior="true")
        self.assertIn(st, _SOLVED, "skip-guard must prevent infeasibility")
        self.assertIn("skipped (conflicts with senior nurse rule)", log)
        self.assertNotIn(0, s._offward_applied)
        self.assertEqual(s._offward_excluded, {})
        self.assertEqual(s.solver.Value(s.assignments[0][2][Shift.NIGHT]), 0,
                         "the senior's cell must not be the off-ward night")


class HolidayBandTest(_SmallWardTest):
    """constraint.md section 22: a third coverage band that defaults to the
    weekend band, while Solver.weekends stays the union for every other rule."""

    def test_holiday_band_defaults_to_weekend(self):
        # C1. No coverage_holiday_* keys; weekend day = 2 -> holiday day = 2.
        # A hard-coded default of 4 would give 4 here.
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            holidays="3")
        self.assertEqual(s.holidays, {2})
        self.assertEqual(s.coverage['holiday'][Shift.DAY], 2)
        self.assertEqual(s.coverage['holiday'][Shift.EVENING], 1)
        self.assertEqual(s.coverage['holiday'][Shift.NIGHT], 1)
        self.assertEqual(s.days[2]['min_shifts'][Shift.DAY], 2)

    def test_holiday_band_is_applied(self):
        # C2. Holiday on DAY 1: it has no preceding day, so the N->D ban cannot
        # cap who may work Day (a day-3 holiday made an earlier version of this
        # fixture INFEASIBLE in both arms, proving nothing). Weekday band is
        # 1/1/1; the holiday needs Day=6 (all six nurses: feasible) vs Day=7
        # (more than exist: infeasible).
        plain = self._plain(6)
        s, st, _ = self._run(7, plain, cov=(1, 1, 1), cov_hol=(6, 0, 0),
                             holidays="1")
        self.assertIn(st, _SOLVED)
        self.assertEqual(
            sum(s.solver.Value(s.assignments[n][0][Shift.DAY]) for n in range(6)), 6)
        s, st, _ = self._run(7, plain, cov=(1, 1, 1), cov_hol=(7, 0, 0),
                             holidays="1")
        self.assertEqual(st, cp_model.INFEASIBLE,
                         "the holiday band must drive day-1 coverage")
        # Control: identical keys but NO holidays value -> solves, proving the
        # flip comes from the band lookup and not from the keys existing.
        s, st, _ = self._run(7, plain, cov=(1, 1, 1), cov_hol=(7, 0, 0),
                             holidays="")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.holidays, set())

    def test_holiday_wins_over_weekend_band(self):
        # C3. Day 6 is in BOTH lists -> holiday numbers; day 7 is weekend only.
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            cov_hol=(5, 4, 3), weekends="6 7", holidays="6")
        self.assertEqual(s.days[5]['min_shifts'][Shift.DAY], 5)
        self.assertEqual(s.days[5]['min_shifts'][Shift.EVENING], 4)
        self.assertEqual(s.days[5]['min_shifts'][Shift.NIGHT], 3)
        self.assertEqual(s.days[6]['min_shifts'][Shift.DAY], 2)
        self.assertEqual(s.weekends, {5, 6}, "weekends must be the union")

    def test_holiday_is_non_working_for_senior(self):
        # C4. Day 3 is a holiday but NOT in weekends. With the senior rule on,
        # the head nurse (nurse 0) must be OFF that day: Solver.weekends has to
        # be the union, or head nurses start working public holidays.
        s, st, _ = self._run(7, self._plain(6, seniors=(0,)), cov=(1, 1, 1),
                             senior="true", weekends="", holidays="3")
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.holidays, {2})
        self.assertIn(2, s.weekends)
        self.assertEqual(s.solver.Value(s.assignments[0][2][Shift.OFF]), 1)
        self.assertEqual(s.solver.Value(s.assignments[0][2][Shift.DAY]), 0)

    def test_file_without_holidays_line_is_unchanged(self):
        # Legacy control: no holidays line -> no holiday days, and the weekend
        # band alone drives the weekend days exactly as before.
        path = os.path.join(self._workdir, "input", "w.csv")
        text = _ward_csv(7, self._plain(6), cov=(2, 1, 1), weekends="6 7")
        text = text.replace("holidays,\n", "", 1)
        self.assertNotIn("holidays", text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with contextlib.redirect_stdout(io.StringIO()):
            s = sw.Solver(path, 7, [])
            s.loadReqShifts(path, s.MAX_REQ_HOLIDAYS, s.MAX_REQ_SHIFT)
        self.assertEqual(s.holidays, set())
        self.assertEqual(s.weekends, {5, 6})
        self.assertTrue(all(s.days[d]['min_shifts'][Shift.DAY] == 2
                            for d in range(7)))

    def test_infeasible_holiday_floor_is_named_in_diagnosis(self):
        # The diagnosis coverage item carries the band so the floor is nameable.
        s, st, _ = self._run(7, self._plain(6), cov=(2, 1, 1),
                             cov_hol=(7, 0, 0), holidays="3")
        self.assertEqual(st, cp_model.INFEASIBLE)
        with contextlib.redirect_stdout(io.StringIO()):
            d = s.diagnose(8.0)
        self.assertEqual(d["reason"], "coverage")
        self.assertTrue(
            any(c.get("band") == "holiday" and c["day"] == 3
                for c in d["coverage"]), d["coverage"])


class SaturdaySundayBandTest(_SmallWardTest):
    """constraint.md section 22: Saturday / Sunday bands. Solver.weekends stays
    the union; the two lists only pick the coverage band."""

    _SAT = "saturdays,3\nsundays,4\n"
    _SAT_NONE_NAMED = "coverage_saturday_evening,0\ncoverage_saturday_night,0\n"

    def test_bands_default_to_weekend_when_absent(self):
        # No saturdays/sundays lines: weekend days 3 and 4 keep the weekend band.
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            weekends="3 4")
        self.assertEqual((s.saturdays, s.sundays), (set(), set()))
        self.assertEqual(s.days[2]['min_shifts'][Shift.DAY], 2)
        self.assertEqual(s.days[3]['min_shifts'][Shift.DAY], 2)

    def test_saturday_and_sunday_numbers_inherit_weekend_keys(self):
        # Lists present but no coverage_saturday_* keys -> weekend numbers (2),
        # not a hard-coded 4.
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            weekends="3 4", extra=self._SAT)
        self.assertEqual(s.saturdays, {2})
        self.assertEqual(s.sundays, {3})
        self.assertEqual(s.coverage['saturday'][Shift.DAY], 2)
        self.assertEqual(s.coverage['sunday'][Shift.NIGHT], 1)

    def test_saturday_and_sunday_bands_are_distinct_and_applied(self):
        extra = (self._SAT + "coverage_saturday_day,5\ncoverage_saturday_evening,0\n"
                 "coverage_saturday_night,0\ncoverage_sunday_day,3\n"
                 "coverage_sunday_evening,0\ncoverage_sunday_night,0\n")
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            weekends="3 4", extra=extra)
        self.assertEqual(s.days[2]['min_shifts'][Shift.DAY], 5)   # Saturday
        self.assertEqual(s.days[3]['min_shifts'][Shift.DAY], 3)   # Sunday
        self.assertEqual(s.days[0]['min_shifts'][Shift.DAY], 2)   # weekday

    def test_precedence_holiday_over_sunday_over_saturday(self):
        # Day 3 is holiday AND saturday; day 4 is sunday AND weekend.
        extra = self._SAT + "coverage_saturday_day,5\ncoverage_sunday_day,3\n"
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            cov_hol=(6, 0, 0), weekends="3 4", holidays="3",
                            extra=extra)
        self.assertEqual(s.days[2]['min_shifts'][Shift.DAY], 6)   # holiday wins
        self.assertEqual(s.days[3]['min_shifts'][Shift.DAY], 3)   # sunday wins
        # Sunday beats Saturday if a day is (wrongly) in both lists.
        extra = "saturdays,4\nsundays,4\ncoverage_saturday_day,5\ncoverage_sunday_day,3\n"
        s, _, _ = self._run(7, self._plain(6), solve=False, cov=(2, 1, 1),
                            weekends="4", extra=extra)
        self.assertEqual(s.days[3]['min_shifts'][Shift.DAY], 3)

    def test_weekends_is_the_union_and_saturday_is_non_working_for_senior(self):
        # Day 3 is listed ONLY in saturdays (not in weekends): it must still be
        # in Solver.weekends, so the head nurse is OFF (senior rule, section 16).
        s, st, _ = self._run(7, self._plain(6, seniors=(0,)), cov=(1, 1, 1), senior="true",
                             weekends="", extra="saturdays,3\n")
        self.assertIn(st, _SOLVED)
        self.assertIn(2, s.weekends)
        self.assertEqual(s.solver.Value(s.assignments[0][2][Shift.OFF]), 1)

    def test_saturday_band_drives_solver(self):
        # Day 1 has no preceding day. Saturday needs 7 Day (> 6 nurses) ->
        # INFEASIBLE; the control (no saturdays list) solves.
        extra = "saturdays,1\ncoverage_saturday_day,7\n" + self._SAT_NONE_NAMED
        s, st, _ = self._run(7, self._plain(6), cov=(1, 1, 1), weekends="1",
                             extra=extra)
        self.assertEqual(st, cp_model.INFEASIBLE)
        s, st, _ = self._run(7, self._plain(6), cov=(1, 1, 1), weekends="1",
                             extra=extra.replace("saturdays,1", "saturdays,"))
        self.assertIn(st, _SOLVED)

    def test_infeasible_saturday_floor_is_named_in_diagnosis(self):
        extra = "saturdays,3\ncoverage_saturday_day,7\n" + self._SAT_NONE_NAMED
        s, st, _ = self._run(7, self._plain(6), cov=(1, 1, 1), weekends="3",
                             extra=extra)
        self.assertEqual(st, cp_model.INFEASIBLE)
        with contextlib.redirect_stdout(io.StringIO()):
            d = s.diagnose(8.0)
        self.assertEqual(d["reason"], "coverage")
        self.assertTrue(
            any(c.get("band") == "saturday" and c["day"] == 3
                for c in d["coverage"]), d["coverage"])


class NegativeRequestTest(_SmallWardTest):
    """constraint.md section 23: 'no:X' = assignments[n][d][X] == 0 (pure negation),
    routed through _add_request so soft mode, the report and diagnose() apply."""

    E = Shift.EVENING

    def test_negation_forbids_the_shift_only(self):
        # Evening needed once, 2 nurses, 1 day. A says no:บ -> B must take it.
        # (Without the rule either nurse could be picked.) Also: A is NOT forced
        # to work - nothing demands it, and OFF stays legal.
        rows = [("A", "", ["no:บ"]), ("B", "", [""])]
        s, st, _ = self._run(1, rows, cov=(0, 1, 0))
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.solver.Value(s.assignments[0][0][self.E]), 0)
        self.assertEqual(s.solver.Value(s.assignments[1][0][self.E]), 1)

    def test_all_nurses_negating_a_needed_shift_is_infeasible(self):
        # Proves it is a real hard constraint (min_request_percent 100), and that
        # diagnose() names the cells through the shared _add_request gate.
        rows = [("A", "", ["no:บ"]), ("B", "", ["no:บ"])]
        s, st, _ = self._run(1, rows, cov=(0, 1, 0))
        self.assertEqual(st, cp_model.INFEASIBLE)
        with contextlib.redirect_stdout(io.StringIO()):
            d = s.diagnose(8.0)
        self.assertEqual(d["reason"], "requests")
        self.assertTrue(all(r["kind"] == "avoid" and r["shift"] == "no:บ"
                            for r in d["requests"]), d["requests"])
        self.assertEqual(len(d["requests"]), 2)

    def test_negation_is_droppable_and_reported_in_soft_mode(self):
        # Same infeasible ward, but min_request_percent 50: one of the two
        # negations is dropped and the acceptance report says so.
        path = os.path.join(self._workdir, "input", "w.csv")
        text = _ward_csv(1, [("A", "", ["no:บ"]), ("B", "", ["no:บ"])], cov=(0, 1, 0))
        text = text.replace("min_request_percent,100", "min_request_percent,50")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with contextlib.redirect_stdout(io.StringIO()):
            s = sw.Solver(path, 1, [], allow_shift_e_n=False, allow_shift_n_d=False)
            st = s.run(max_time_seconds=10)
        self.assertIn(st, _SOLVED)
        rep = s.request_report
        self.assertEqual((rep["total"], rep["accepted"]), (2, 1))
        self.assertEqual(rep["dropped"][0]["shift"], "no:บ")
        self.assertEqual(rep["dropped"][0]["kind"], "avoid")

    def test_double_shift_assignments_are_ruled_out(self):
        # ช/บ and ด/บ both set Evening, so no:บ excludes them with no extra code.
        # 3 nurses, cov 1/1/1 on a single day, A negates Evening: A's Evening var
        # is 0 whatever else she does.
        rows = [("A", "", ["no:บ"]), ("B", "", [""]), ("C", "", [""])]
        s, st, _ = self._run(1, rows, cov=(1, 1, 1))
        self.assertIn(st, _SOLVED)
        self.assertEqual(s.solver.Value(s.assignments[0][0][self.E]), 0)

    def test_negation_shares_the_request_quota(self):
        # DataImporter samples positive and negative cells together, so a nurse
        # with more request cells than max_req_shifts keeps only that many in
        # total. 'department' tier -> read the limit from the loader.
        path = os.path.join(self._workdir, "input", "w.csv")
        cells = ["no:บ", "no:ช", "ช", "บ", "ด", "no:ด", "ช"]
        with open(path, "w", encoding="utf-8") as f:
            f.write(_ward_csv(7, [("A", "", cells)], cov=(0, 0, 0)))
        with contextlib.redirect_stdout(io.StringIO()):
            s = sw.Solver(path, 7, [], allow_shift_e_n=False, allow_shift_n_d=False)
        quota = s.MAX_REQ_SHIFT
        self.assertLess(quota, 7, "fixture needs a quota below the 7 cells")
        nurse = s.nurses[0]
        self.assertEqual(
            len(nurse["requested_shifts"]) + len(nurse["requested_negations"]), quota)


class RequestFloorTest(_SmallWardTest):
    """The soft-request acceptance floor must not round up to "keep everything"
    for a small set of requests (constraint.md section 8)."""

    def _ward(self, pct=None):
        """One day; coverage needs BOTH nurses on Day, so A's `off` is impossible."""
        path = os.path.join(self._workdir, "input", "w.csv")
        text = _ward_csv(1, [("A", "", ["off"]), ("B", "", [""])], cov=(2, 0, 0))
        text = text.replace("relax_days_off,false", "relax_days_off,true")
        if pct is not None:
            text = text.replace("min_request_percent,100",
                                "min_request_percent,%d" % pct)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with contextlib.redirect_stdout(io.StringIO()):
            s = sw.Solver(path, 1, [], allow_shift_e_n=False, allow_shift_n_d=False)
            return s, s.run(max_time_seconds=10)

    def test_a_lone_request_can_still_be_dropped(self):
        # Regression: the floor was a bare ceil(), so a SMALL set of relaxable
        # requests rounded up to "keep everything" and the setting silently did
        # nothing — ceil(0.5 * 1) == 1 leaves nothing to drop. It bit PN/NA
        # hardest, because each staff group's requests are counted on their own
        # since the split, so the smaller group lost all its slack.
        s, st = self._ward(pct=50)
        self.assertIn(st, _SOLVED)
        rep = s.request_report
        self.assertEqual((rep["total"], rep["accepted"]), (1, 0))
        self.assertEqual(rep["dropped"][0]["kind"], "holiday")

    def test_hundred_percent_stays_hard(self):
        # The cap applies only BELOW 100%. At 100% every request is required, so
        # the same ward must still be infeasible.
        _s, st = self._ward()
        self.assertEqual(st, cp_model.INFEASIBLE)


class FatigueRulesTest(_SmallWardTest):
    """constraint.md section 24: opt-in max-consecutive-nights and
    no-consecutive-ช/บ. Coverage is 0 so only the rule under test can make a
    ward infeasible."""

    ON_N3 = "limit_consecutive_nights,true\nmax_consecutive_nights,3\n"
    ON_DE = "no_consecutive_day_eve,true\n"

    def _status(self, cells, extra, days=10):
        _s, status, _out = self._run(days, [("A", "", cells)], cov=(0, 0, 0),
                                     extra=extra)
        return status

    def test_four_nights_infeasible_at_limit_3(self):
        self.assertEqual(self._status(["ด"] * 4, self.ON_N3), cp_model.INFEASIBLE)

    def test_three_nights_ok_at_limit_3(self):
        self.assertIn(self._status(["ด"] * 3, self.ON_N3), _SOLVED)

    def test_nights_split_by_a_gap_ok(self):
        self.assertIn(self._status(["ด", "ด", "ด", "", "ด", "ด", "ด"], self.ON_N3),
                      _SOLVED)

    def test_nights_toggle_off_is_no_constraint(self):
        self.assertIn(self._status(["ด"] * 4, "max_consecutive_nights,3\n"), _SOLVED)

    def test_nights_limit_longer_than_month_is_noop(self):
        extra = "limit_consecutive_nights,true\nmax_consecutive_nights,20\n"
        self.assertIn(self._status(["ด"] * 4, extra, days=5), _SOLVED)

    def test_nights_limit_is_configurable(self):
        extra = "limit_consecutive_nights,true\nmax_consecutive_nights,2\n"
        self.assertEqual(self._status(["ด"] * 3, extra), cp_model.INFEASIBLE)
        self.assertIn(self._status(["ด"] * 2, extra), _SOLVED)

    def test_two_adjacent_day_eve_doubles_infeasible(self):
        self.assertEqual(self._status(["ช/บ", "ช/บ"], self.ON_DE), cp_model.INFEASIBLE)

    def test_day_eve_then_day_only_ok(self):
        self.assertIn(self._status(["ช/บ", "ช"], self.ON_DE), _SOLVED)

    def test_day_eve_toggle_off_is_no_constraint(self):
        self.assertIn(self._status(["ช/บ", "ช/บ"], ""), _SOLVED)

    def test_day_eve_not_adjacent_ok(self):
        self.assertIn(self._status(["ช/บ", "", "ช/บ"], self.ON_DE), _SOLVED)

    def test_solved_schedule_respects_both_rules(self):
        """No requests: a busy ward (4 nurses, 1/1/2 nights) solved with both
        rules on must contain no run of 4 nights and no adjacent ช/บ."""
        days = 14
        rows = [("N%d" % i, "", [""] * days) for i in range(4)]
        s, status, _out = self._run(days, rows, cov=(1, 1, 2),
                                    extra=self.ON_N3 + self.ON_DE)
        self.assertIn(status, _SOLVED)
        for n in range(4):
            night = [s.solver.Value(s.assignments[n][d][Shift.NIGHT]) for d in range(days)]
            day = [s.solver.Value(s.assignments[n][d][Shift.DAY]) for d in range(days)]
            eve = [s.solver.Value(s.assignments[n][d][Shift.EVENING]) for d in range(days)]
            for d in range(days - 3):
                self.assertLessEqual(sum(night[d:d + 4]), 3, f"nurse {n} 4 nights from {d}")
            for d in range(days - 1):
                self.assertFalse(day[d] and eve[d] and day[d + 1] and eve[d + 1],
                                 f"nurse {n} ช/บ on days {d},{d+1}")

    def test_diagnose_names_the_requests_for_nights(self):
        _s, diag = self._diag([["ด"] * 4], self.ON_N3)
        self.assertEqual(diag["reason"], "requests")
        self.assertEqual(sorted(r["day"] for r in diag["requests"]), [1, 2, 3, 4])

    def test_diagnose_names_the_requests_for_day_eve(self):
        _s, diag = self._diag([["ช/บ", "ช/บ"]], self.ON_DE)
        self.assertEqual(diag["reason"], "requests")
        self.assertEqual(sorted(r["day"] for r in diag["requests"]), [1, 2])

    def _diag(self, cell_rows, extra):
        s, status, _out = self._run(
            10, [("A%d" % i, "", c) for i, c in enumerate(cell_rows)],
            cov=(0, 0, 0), extra=extra)
        self.assertEqual(status, cp_model.INFEASIBLE)
        with contextlib.redirect_stdout(io.StringIO()):
            return s, s.diagnose(10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
