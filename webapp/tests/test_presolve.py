"""Tests for the pre-solve validation kernel (webapp/core/presolve.py).

Builds ScheduleInput objects directly (from_grid), never through the web
routes — those are covered in test_web.py's PresolveRouteTests. Every crafted
"error" fixture here is cross-checked against the real CP-SAT solver
(test_no_false_blockers_vs_real_solver / test_errors_agree_with_real_solver):
presolve.check() must never block something the real solver can actually
solve, and every error it raises must correspond to a real INFEASIBLE.

Run from the project root:
  python3 -m unittest webapp.tests.test_presolve
"""

import glob
import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core import presolve  # noqa: E402
from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.solver_runner import run_schedule  # noqa: E402


def _si(ward_meta=None, settings=None, nurses=None):
    wm = {"ward_name": "TestWard"}
    if ward_meta:
        wm.update(ward_meta)
    return ScheduleInput.from_grid(
        ward_meta=wm, settings=settings or {}, nurses=nurses or []
    )


def _codes(issues):
    return {i.code for i in issues}


# ── Fixture builders shared between the unit tests and the real-solver
#    cross-checks below ──────────────────────────────────────────────────────

def _tier_nurse_cap_si(inactive_last=False):
    nurses = [Nurse(name=f"N{i}") for i in range(9)]
    if inactive_last:
        nurses[8].active = False
    return _si(settings={"num_days": 1, "head_nurse_special_shift": False},
               nurses=nurses)


def _coverage_day_si(off_token="off", soft=False, relax_days_off=False,
                      enforce_vacation=True):
    nurses = [Nurse(name=f"N{i}") for i in range(4)]
    for i in range(3):
        nurses[i].shifts[2] = off_token
    settings = {
        "num_days": 7, "weekends": [],
        "coverage_weekday_day": 2, "coverage_weekday_evening": 1,
        "coverage_weekday_night": 1,
        "coverage_weekend_day": 0, "coverage_weekend_evening": 0,
        "coverage_weekend_night": 0,
        "head_nurse_special_shift": False,
        "min_request_percent": 80 if soft else 100,
        "relax_days_off": relax_days_off,
        "enforce_vacation": enforce_vacation,
    }
    return _si(settings=settings, nurses=nurses)


def _transition_si(allow_night_to_day):
    nurses = [Nurse(name="N0", shifts={3: "ด", 4: "ช"})]
    settings = {
        "num_days": 4, "weekends": [],
        "coverage_weekday_day": 0, "coverage_weekday_evening": 0,
        "coverage_weekday_night": 0,
        "coverage_weekend_day": 0, "coverage_weekend_evening": 0,
        "coverage_weekend_night": 0,
        "head_nurse_special_shift": False,
        "allow_night_to_day": allow_night_to_day,
    }
    return _si(settings=settings, nurses=nurses)


def _pair_si(allow_evening_to_night=False):
    nurses = [Nurse(name=f"N{i}") for i in range(3)]
    settings = {
        "num_days": 2,
        "coverage_weekday_day": 0, "coverage_weekday_evening": 2,
        "coverage_weekday_night": 2,
        "head_nurse_special_shift": False,
        "allow_evening_to_night": allow_evening_to_night,
    }
    si = _si(settings=settings, nurses=nurses)
    si.settings["weekends"] = []
    return si


def _senior_meeting_si():
    nurses = [Nurse(name="Head"), Nurse(name="Deputy"), Nurse(name="C")]
    nurses[0].shifts[6] = "mtg"
    settings = {
        "num_days": 7, "weekends": [6, 7],
        "coverage_weekday_day": 0, "coverage_weekday_evening": 0,
        "coverage_weekday_night": 0,
        "coverage_weekend_day": 0, "coverage_weekend_evening": 0,
        "coverage_weekend_night": 0,
        "head_nurse_special_shift": True,
    }
    return _si(ward_meta={"tier": "ward"}, settings=settings, nurses=nurses)


class PresolveKernelTests(unittest.TestCase):

    def test_feasible_fixture_has_no_errors(self):
        input_dir = os.path.join(_PROJECT_DIR, "input")
        paths = sorted(glob.glob(os.path.join(input_dir, "Schedule *.csv")))
        self.assertTrue(paths, "expected input/Schedule *.csv fixtures to exist")
        for path in paths:
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                si = ScheduleInput.from_csv(text)
                si.ward_meta["tier"] = "department"
                report = presolve.check(si)
                self.assertEqual(report.errors, [], f"{path}: {report.errors}")

    def test_no_false_blockers_vs_real_solver(self):
        cases = [
            _si(settings={"num_days": 5, "weekends": [],
                           "coverage_weekday_day": 1, "coverage_weekday_evening": 1,
                           "coverage_weekday_night": 1, "head_nurse_special_shift": False},
                nurses=[Nurse(name=f"N{i}") for i in range(4)]),
            _si(settings={"num_days": 7, "weekends": [6, 7],
                           "coverage_weekday_day": 2, "coverage_weekday_evening": 1,
                           "coverage_weekday_night": 1,
                           "coverage_weekend_day": 1, "coverage_weekend_evening": 1,
                           "coverage_weekend_night": 1,
                           "head_nurse_special_shift": True},
                nurses=[Nurse(name=f"N{i}", type=("senior" if i < 2 else ""))
                        for i in range(6)]),
            _si(settings={"num_days": 6, "weekends": [],
                           "coverage_weekday_day": 1, "coverage_weekday_evening": 1,
                           "coverage_weekday_night": 1,
                           "head_nurse_special_shift": False,
                           "allow_evening_to_night": True,
                           "allow_night_to_day": True},
                nurses=[Nurse(name=f"N{i}") for i in range(6)]),
        ]
        for i, si in enumerate(cases):
            with self.subTest(case=i):
                result = run_schedule(si, 20)
                self.assertTrue(result.solved,
                                 f"case {i} expected solved, got {result.solver_status}")
                report = presolve.check(si)
                self.assertTrue(report.ok, f"case {i} presolve errors: {report.errors}")

    def test_errors_agree_with_real_solver(self):
        with self.subTest(case="tier_nurses"):
            si = _tier_nurse_cap_si()
            self.assertIn("tier_nurses", _codes(presolve.check(si).errors))
            with self.assertRaises(ValueError):
                run_schedule(si, 20)

        with self.subTest(case="coverage_day"):
            si = _coverage_day_si()
            self.assertIn("coverage_day", _codes(presolve.check(si).errors))
            result = run_schedule(si, 20)
            self.assertEqual(result.solver_status, "INFEASIBLE")

        with self.subTest(case="vacation"):
            si = _coverage_day_si(off_token="vac")
            self.assertIn("coverage_day", _codes(presolve.check(si).errors))
            result = run_schedule(si, 20)
            self.assertEqual(result.solver_status, "INFEASIBLE")

        with self.subTest(case="request_transition"):
            si = _transition_si(allow_night_to_day=False)
            self.assertIn("request_transition", _codes(presolve.check(si).errors))
            result = run_schedule(si, 20)
            self.assertEqual(result.solver_status, "INFEASIBLE")

        with self.subTest(case="coverage_pair"):
            si = _pair_si()
            self.assertIn("coverage_pair", _codes(presolve.check(si).errors))
            result = run_schedule(si, 20)
            self.assertEqual(result.solver_status, "INFEASIBLE")

        with self.subTest(case="senior_meeting_weekend"):
            si = _senior_meeting_si()
            self.assertIn("senior_meeting_weekend", _codes(presolve.check(si).errors))
            result = run_schedule(si, 20)
            self.assertEqual(result.solver_status, "INFEASIBLE")

    def test_tier_nurse_cap(self):
        report = presolve.check(_tier_nurse_cap_si())
        self.assertIn("tier_nurses", _codes(report.errors))

        report2 = presolve.check(_tier_nurse_cap_si(inactive_last=True))
        self.assertNotIn("tier_nurses", _codes(report2.errors))

    def test_coverage_day_error_names_day(self):
        report = presolve.check(_coverage_day_si())
        cov_errors = [i for i in report.errors if i.code == "coverage_day"]
        self.assertEqual(len(cov_errors), 1)
        self.assertEqual(cov_errors[0].days, [2])
        self.assertIn("Day 2", cov_errors[0].message)

    def test_relaxable_offs_do_not_block(self):
        report = presolve.check(_coverage_day_si(soft=True, relax_days_off=True))
        self.assertEqual(_codes(report.errors) & {"coverage_day"}, set())

    def test_vacation_hardness_follows_setting(self):
        report_hard = presolve.check(_coverage_day_si(off_token="vac"))
        self.assertIn("coverage_day", _codes(report_hard.errors))

        report_soft = presolve.check(_coverage_day_si(
            off_token="vac", soft=True, enforce_vacation=False))
        self.assertEqual(_codes(report_soft.errors) & {"coverage_day"}, set())

    def test_request_transition_night_then_day(self):
        report = presolve.check(_transition_si(allow_night_to_day=False))
        trans = [i for i in report.errors if i.code == "request_transition"]
        self.assertEqual(len(trans), 1)
        self.assertEqual(trans[0].days, [3, 4])

        report_ok = presolve.check(_transition_si(allow_night_to_day=True))
        self.assertNotIn("request_transition", _codes(report_ok.errors))

    def test_coverage_pair_error(self):
        report = presolve.check(_pair_si())
        pair_errors = [i for i in report.errors if i.code == "coverage_pair"]
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(len(pair_errors), 1)
        self.assertEqual(pair_errors[0].days, [1, 2])

        report_ok = presolve.check(_pair_si(allow_evening_to_night=True))
        self.assertEqual(report_ok.errors, [])

    def test_senior_meeting_on_weekend(self):
        report = presolve.check(_senior_meeting_si())
        self.assertIn("senior_meeting_weekend", _codes(report.errors))

    def test_quota_warnings_not_errors(self):
        nurses = [Nurse(name="N0", shifts={1: "off", 2: "off", 3: "off"}),
                  Nurse(name="N1")]
        settings = {
            "num_days": 3, "weekends": [],
            "coverage_weekday_day": 2, "coverage_weekday_evening": 0,
            "coverage_weekday_night": 0,
            "head_nurse_special_shift": False,
        }
        si = _si(settings=settings, nurses=nurses)  # tier defaults to free
        report = presolve.check(si)
        self.assertIn("quota_off", _codes(report.warnings))
        self.assertNotIn("quota_off", _codes(report.errors))
        self.assertNotIn("coverage_day", _codes(report.errors))

    def test_mtg_on_free_tier_warns(self):
        nurses = [Nurse(name="N0", shifts={1: "mtg"}), Nurse(name="N1")]
        settings = {
            "num_days": 1,
            "coverage_weekday_day": 0, "coverage_weekday_evening": 0,
            "coverage_weekday_night": 0,
            "head_nurse_special_shift": False,
        }
        si = _si(settings=settings, nurses=nurses)  # tier defaults to free
        report = presolve.check(si)
        self.assertIn("tier_meetings", _codes(report.warnings))

    def test_senior_weekend_request_warns(self):
        nurses = [Nurse(name="Head", shifts={6: "ช"}), Nurse(name="Deputy"),
                  Nurse(name="C")]
        settings = {
            "num_days": 7, "weekends": [6, 7],
            "coverage_weekday_day": 0, "coverage_weekday_evening": 0,
            "coverage_weekday_night": 0,
            "coverage_weekend_day": 0, "coverage_weekend_evening": 0,
            "coverage_weekend_night": 0,
            "head_nurse_special_shift": True,
        }
        si = _si(settings=settings, nurses=nurses)
        report = presolve.check(si)
        self.assertIn("senior_request", _codes(report.warnings))
        self.assertEqual(report.errors, [])

    def test_checklist_counts(self):
        nurses = [Nurse(name=f"N{i}") for i in range(3)]
        settings = {
            "num_days": 3, "weekends": [],
            "coverage_weekday_day": 1, "coverage_weekday_evening": 0,
            "coverage_weekday_night": 0,
            "head_nurse_special_shift": False,
        }
        si = _si(ward_meta={"tier": "ward"}, settings=settings, nurses=nurses)
        report = presolve.check(si)
        self.assertEqual(report.checks_total, 4)
        self.assertEqual(report.checks_passed, 4)
        self.assertTrue(report.ok)
        self.assertEqual(report.warnings, [])


if __name__ == "__main__":
    unittest.main()
