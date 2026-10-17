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
    nurses = [Nurse(name="Head", type="senior"), Nurse(name="Deputy", type="senior"),
              Nurse(name="C")]
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
        nurses = [Nurse(name="Head", type="senior", shifts={6: "ช"}), Nurse(name="Deputy"),
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



# -- Off-ward duties (constraint.md section 21) and the holiday band (section 22) --

def _offward_base(**over):
    s = {"num_days": 6, "weekends": [], "head_nurse_special_shift": False,
         "coverage_weekday_day": 1, "coverage_weekday_evening": 1,
         "coverage_weekday_night": 1}
    s.update(over)
    return s


def _holiday_si(senior_idx=0, **over):
    """6 nurses, 7 days. All three bands default to 2/1/1 so a test changes
    exactly one band's number and nothing else."""
    s = {"num_days": 7, "weekends": [], "holidays": [],
         "coverage_weekday_day": 2, "coverage_weekday_evening": 1,
         "coverage_weekday_night": 1,
         "coverage_weekend_day": 2, "coverage_weekend_evening": 1,
         "coverage_weekend_night": 1,
         "coverage_holiday_day": 2, "coverage_holiday_evening": 1,
         "coverage_holiday_night": 1,
         "head_nurse_special_shift": False}
    s.update(over)
    return _si(settings=s, nurses=[
        Nurse(name=f"N{i}", type=("senior" if i == senior_idx else ""))
        for i in range(6)])


# Cell tokens, spelled as escapes so this file stays ASCII: ช = day,
# บ = evening, ด = night.
class PresolveOffwardTests(unittest.TestCase):

    def test_presolve_no_false_blockers_with_offward(self):
        # Failure mode 9: a feasible ward with off-ward cells must not be blocked.
        nurses = [Nurse(name=f"N{i}") for i in range(6)]
        nurses[2].shifts[2] = "flt:บ"
        nurses[3].shifts[3] = "ref:ช"
        si = _si(settings=_offward_base(), nurses=nurses)
        result = run_schedule(si, 20)
        self.assertTrue(result.solved, result.solver_status)
        report = presolve.check(si)
        self.assertEqual(report.errors, [])

    def test_presolve_offward_exclusion_is_a_real_coverage_error(self):
        # The converse: off-ward removes the nurse from the coverage pool, so
        # when that makes a day short presolve must say so (and the solver agrees).
        nurses = [Nurse(name=f"N{i}") for i in range(3)]
        nurses[0].shifts[2] = "flt:ด"
        nurses[1].shifts[2] = "off"
        nurses[2].shifts[2] = "off"
        si = _si(settings=_offward_base(
            num_days=3, coverage_weekday_evening=0), nurses=nurses)
        report = presolve.check(si)
        cov = [e for e in report.errors if e.code == "coverage_day"]
        self.assertEqual(len(cov), 1)
        self.assertEqual(cov[0].days, [2])
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_presolve_offward_transition_error(self):
        zero = {"coverage_weekday_day": 0, "coverage_weekday_evening": 0,
                "coverage_weekday_night": 0, "coverage_weekend_day": 0,
                "coverage_weekend_evening": 0, "coverage_weekend_night": 0}

        def ward(allow_night_to_day):
            settings = _offward_base(num_days=5,
                                     allow_night_to_day=allow_night_to_day)
            settings.update(zero)
            return _si(settings=settings, nurses=[
                Nurse(name="N0", shifts={4: "flt:ด", 5: "ช"})])

        blocked = presolve.check(ward(False))
        self.assertEqual([e.code for e in blocked.errors], ["request_transition"])
        self.assertEqual(blocked.errors[0].days, [4, 5])
        self.assertEqual(presolve.check(ward(True)).errors, [])
        # Sound: the solver really cannot do night -> day when it is disallowed.
        self.assertEqual(run_schedule(ward(False), 20).solver_status, "INFEASIBLE")

    def test_presolve_senior_offward_is_a_warning(self):
        # The solver DROPS an off-ward cell that conflicts with the senior rule
        # (skip-guard), so the ward still solves. Presolve errors must be sound:
        # promoting this to an error would block a solvable ward.
        nurses = [Nurse(name=f"N{i}", type="senior" if i < 2 else "")
                  for i in range(6)]
        nurses[0].shifts[6] = "ref:ช"      # head on a weekend
        nurses[1].shifts[2] = "flt:ด"      # deputy on a night
        nurses[2].shifts[2] = "flt:บ"
        si = _si(settings=_offward_base(
            weekends=[6], head_nurse_special_shift=True,
            coverage_weekend_day=1, coverage_weekend_evening=1,
            coverage_weekend_night=1), nurses=nurses)
        report = presolve.check(si)
        self.assertEqual(report.errors, [])
        offward = [w for w in report.warnings if w.code == "senior_offward"]
        self.assertEqual(len(offward), 2)
        self.assertEqual(len(report.warnings), 2)
        self.assertTrue(report.ok)
        result = run_schedule(si, 20)
        self.assertTrue(result.solved, result.solver_status)

    def test_presolve_grouped_messages_and_bands(self):
        nurses = ([Nurse(name=f"RN{i}", staff_class="rn", type=("senior" if i == 0 else ""))
                   for i in range(4)]
                  + [Nurse(name=f"PN{i}", staff_class="pn") for i in range(3)])
        # PN/NA has 3 nurses and needs 4 on Day; RN has 4 and needs 1.
        nurses[0].shifts[1] = "ref:ด"      # RN head on night -> warning
        nurses[4].shifts[1] = "ref:ด"      # PN index 0: no senior rule
        settings = _offward_base(
            num_days=2, head_nurse_special_shift=True,
            coverage_rn_weekday_day=1, coverage_rn_weekday_evening=1,
            coverage_rn_weekday_night=1,
            coverage_pnna_weekday_day=4, coverage_pnna_weekday_evening=0,
            coverage_pnna_weekday_night=0)
        si = _si(ward_meta={"tier": "department"}, settings=settings, nurses=nurses)
        report = presolve.check(si)
        self.assertTrue(any(e.message.startswith("PN/NA: ") for e in report.errors),
                        report.errors)
        self.assertFalse(any(e.message.startswith("RN: ") for e in report.errors),
                         report.errors)
        self.assertTrue(any(w.code == "senior_offward"
                            and w.message.startswith("RN: ")
                            for w in report.warnings), report.warnings)
        # 1 tier check + 3 per group x 2 groups.
        self.assertEqual(report.checks_total, 7)

        # The tier cap is on the COMBINED roster and is reported once, not per group.
        big = ([Nurse(name=f"RN{i}", staff_class="rn") for i in range(5)]
               + [Nurse(name=f"PN{i}", staff_class="pn") for i in range(5)])
        big_report = presolve.check(_si(settings=_offward_base(num_days=1), nurses=big))
        self.assertEqual([e.code for e in big_report.errors].count("tier_nurses"), 1)


class PresolveHolidayBandTests(unittest.TestCase):

    def test_high_holiday_band_is_a_coverage_error_and_really_infeasible(self):
        si = _holiday_si(holidays=[3], coverage_holiday_day=10)
        report = presolve.check(si)
        cov = [e for e in report.errors if e.code == "coverage_day"]
        self.assertEqual(len(cov), 1, report.errors)
        self.assertEqual(cov[0].days, [3])
        # Soundness: presolve may only block what the solver cannot solve.
        result = run_schedule(si, 20)
        self.assertFalse(result.solved)
        self.assertEqual(result.solver_status, "INFEASIBLE")

    def test_holiday_band_inheriting_weekend_numbers_is_not_blocked(self):
        # Control: holiday numbers equal the weekend numbers (the default).
        si = _holiday_si(holidays=[3])
        report = presolve.check(si)
        self.assertNotIn("coverage_day", _codes(report.errors))
        self.assertEqual(report.errors, [])
        result = run_schedule(si, 20)
        self.assertTrue(result.solved, result.solver_status)

    def test_holiday_wins_over_weekend_band(self):
        # Day 3 is in BOTH lists. High weekend number, low holiday number: the
        # holiday band wins, so there is no error and the ward solves.
        si = _holiday_si(holidays=[3], weekends=[3],
                         coverage_weekend_day=10, coverage_holiday_day=2)
        self.assertEqual(presolve.check(si).errors, [])
        self.assertTrue(run_schedule(si, 20).solved)
        # And the reverse: high holiday number on a day also in weekends blocks.
        si2 = _holiday_si(holidays=[3], weekends=[3], coverage_holiday_day=10)
        self.assertIn("coverage_day", _codes(presolve.check(si2).errors))
        self.assertEqual(run_schedule(si2, 20).solver_status, "INFEASIBLE")

    def test_saturday_band_is_a_coverage_error_and_really_infeasible(self):
        si = _holiday_si(saturdays=[3], coverage_saturday_day=10)
        self.assertIn("coverage_day", _codes(presolve.check(si).errors))
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")
        # Control: same number, day not listed in saturdays -> no error.
        ok = _holiday_si(coverage_saturday_day=10)
        self.assertEqual(presolve.check(ok).errors, [])

    def test_sunday_wins_over_saturday_and_weekend(self):
        # Day 3 is in saturdays, sundays and weekends. Sunday's low number wins
        # over the high Saturday/weekend numbers: no error, and it solves.
        si = _holiday_si(saturdays=[3], sundays=[3], weekends=[3],
                         coverage_weekend_day=10, coverage_saturday_day=10,
                         coverage_sunday_day=2)
        self.assertEqual(presolve.check(si).errors, [])
        self.assertTrue(run_schedule(si, 20).solved)
        # Reverse: high Sunday number on that day blocks.
        si2 = _holiday_si(saturdays=[3], sundays=[3], coverage_sunday_day=10)
        self.assertIn("coverage_day", _codes(presolve.check(si2).errors))

    def test_holiday_wins_over_sunday(self):
        si = _holiday_si(holidays=[3], sundays=[3], coverage_sunday_day=10,
                         coverage_holiday_day=2)
        self.assertEqual(presolve.check(si).errors, [])

    def test_senior_in_the_middle_is_the_one_warned(self):
        # Seniority is the Type column: nurse 3 is senior, nurses 0/1 are not.
        si = _holiday_si(senior_idx=3, holidays=[3], head_nurse_special_shift=True)
        si.nurses[0].shifts[3] = "ช"
        self.assertNotIn("senior_request", _codes(presolve.check(si).warnings))
        si.nurses[3].shifts[3] = "ช"
        self.assertIn("senior_request", _codes(presolve.check(si).warnings))

    def test_no_senior_typed_means_no_senior_warning(self):
        si = _holiday_si(senior_idx=-1, holidays=[3], head_nurse_special_shift=True)
        for n in si.nurses:
            n.shifts[3] = "ช"
        self.assertNotIn("senior_request", _codes(presolve.check(si).warnings))

    def test_saturday_is_non_working_for_the_senior_rule(self):
        # A Saturday NOT in `weekends` still counts as a no-senior day.
        si = _holiday_si(saturdays=[3], head_nurse_special_shift=True)
        si.nurses[0].shifts[3] = "\u0e0a"        # head nurse asks for Day on it
        report = presolve.check(si)
        self.assertIn("senior_request", _codes(report.warnings))
        self.assertEqual(report.errors, [])

    def test_high_weekend_band_only_blocks_non_holiday_weekend_days(self):
        # Day 3 is a holiday (2 on Day: fine); day 6 is a plain weekend day and
        # carries the impossible weekend number, so ONLY day 6 is flagged.
        si = _holiday_si(holidays=[3], weekends=[6], coverage_weekend_day=10)
        cov = [e for e in presolve.check(si).errors if e.code == "coverage_day"]
        self.assertEqual(len(cov), 1)
        self.assertEqual(cov[0].days, [6])
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_holiday_is_non_working_for_the_senior_rule(self):
        # A holiday not in `weekends` is still a no-senior day: a head-nurse
        # request on it is warned about (and not an error: the solver skips it).
        si = _holiday_si(holidays=[3], head_nurse_special_shift=True)
        si.nurses[0].shifts[3] = "ช"
        report = presolve.check(si)
        self.assertIn("senior_request", _codes(report.warnings))
        self.assertEqual(report.errors, [])
        self.assertTrue(run_schedule(si, 20).solved)

    def test_missing_or_null_holidays_key_is_tolerated(self):
        # Months saved before the holiday band have no `holidays` key.
        si = _holiday_si(weekends=[3])
        si.settings.pop("holidays", None)
        self.assertEqual(presolve.check(si).errors, [])
        si.settings["holidays"] = None
        self.assertEqual(presolve.check(si).errors, [])


class PresolveNegationTests(unittest.TestCase):
    """'no:X' cells (constraint.md section 23) feed the coverage check."""

    def _si(self, negators, **over):
        nurses = [Nurse(name=f"N{i}") for i in range(3)]
        for i in range(negators):
            nurses[i].shifts[2] = "no:บ"          # no:evening on day 2
        s = _offward_base(num_days=3, coverage_weekday_night=0, **over)
        return _si(settings=s, nurses=nurses)

    def test_everyone_negating_a_needed_shift_is_a_coverage_error(self):
        si = self._si(3)
        cov = [e for e in presolve.check(si).errors if e.code == "coverage_day"]
        self.assertEqual([e.days for e in cov], [[2]])
        self.assertIn("no:", cov[0].message)
        self.assertEqual(run_schedule(si, 20).solver_status, "INFEASIBLE")

    def test_partial_negation_is_not_blocked(self):
        # Failure mode 9: the converse. Two of three negating still leaves one.
        si = self._si(2)
        self.assertEqual(presolve.check(si).errors, [])
        self.assertTrue(run_schedule(si, 20).solved)

    def test_negation_is_not_hard_in_soft_mode(self):
        si = self._si(3, min_request_percent=50)
        self.assertEqual(presolve.check(si).errors, [])


if __name__ == "__main__":
    unittest.main()
