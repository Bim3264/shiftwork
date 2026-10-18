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
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core import solver_runner, tiers  # noqa: E402
from webapp.core.schedule_input import (  # noqa: E402
    ScheduleInput, Nurse, VALID_CELL_TOKENS,
)
from webapp.core.solver_runner import run_schedule, SolveResult  # noqa: E402


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


def _classed_ward(rn, pn, tier="ward", extra_settings=None, na=0):
    """A ward whose nurses carry a staff class (RN / PN / NA)."""
    nurses = [Nurse(name=f"R{i}", staff_class="rn") for i in range(rn)]
    nurses += [Nurse(name=f"P{i}", staff_class="pn") for i in range(pn)]
    nurses += [Nurse(name=f"A{i}", staff_class="na") for i in range(na)]
    settings = {"num_days": 7, "weekends": [6, 7]}
    settings.update(extra_settings or {})
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "W", "tier": tier},
        settings=settings, nurses=nurses)


class StaffClassSolveTest(unittest.TestCase):
    """Split solves per staff group (RN vs PN/NA) and their merged result."""

    def test_two_groups_produce_two_group_grids(self):
        cov = {}
        for grp in ("rn", "pnna"):
            for band in ("weekday", "weekend"):
                for sh, n in (("day", 2), ("evening", 1), ("night", 1)):
                    cov[f"coverage_{grp}_{band}_{sh}"] = n
        si = _classed_ward(6, 6, extra_settings=cov)
        r = run_schedule(si, 20)

        self.assertTrue(r.solved, r.solver_status)
        g = r.grid
        self.assertEqual(len(g["groups"]), 2)
        self.assertEqual([x["key"] for x in g["groups"]], ["rn", "pn_na"])
        a, b = (set(x["nurse_indices"]) for x in g["groups"])
        self.assertFalse(a & b, "group nurse_indices must be disjoint")
        self.assertEqual(sorted(a | b), list(range(12)))
        # The group-local -> global index remap: RN are roster 0..5, PN 6..11.
        self.assertEqual(a, set(range(0, 6)))
        self.assertEqual(b, set(range(6, 12)))

        self.assertEqual(len(g["rows"]), 12)
        self.assertTrue(all(row.get("group") for row in g["rows"]))
        self.assertEqual([row["nurse_index"] for row in g["rows"]], list(range(12)))
        # Names follow the GLOBAL index, i.e. the remap put each row in its place.
        self.assertEqual([row["name"] for row in g["rows"]],
                         [n.name for n in si.active_nurses()])
        self.assertEqual([row["group"] for row in g["rows"]],
                         ["rn"] * 6 + ["pn_na"] * 6)

        lines = r.csv_text.splitlines()
        self.assertEqual(lines[0], "," + ",".join(str(d) for d in range(1, 8)))
        self.assertEqual(len(lines) - 1, 12)
        self.assertEqual([ln.split(",")[0] for ln in lines[1:]],
                         [str(i) for i in range(12)])

    def test_combined_tier_cap_blocks_split(self):
        # Free tier caps at 8 nurses ACROSS groups. Each group of 5 would pass
        # DataImporter's per-file check on its own.
        si = _classed_ward(5, 5, tier="free")
        self.assertLessEqual(len(si.active_nurses("rn")), 8)
        self.assertLessEqual(len(si.active_nurses("pn_na")), 8)
        with self.assertRaises(ValueError):
            tiers.enforce_max_nurses(si)
        with self.assertRaises(ValueError):
            run_schedule(si, 5)

        # PN and NA both count towards the ceiling: 4 + 3 + 2 = 9 > 8.
        si2 = _classed_ward(4, 3, tier="free", na=2)
        with self.assertRaises(ValueError):
            tiers.enforce_max_nurses(si2)
        with self.assertRaises(ValueError):
            run_schedule(si2, 5)

        view = tiers.tier_view(si)
        self.assertEqual(view["nurse_count"], 10)
        self.assertTrue(view["over_max"])
        self.assertEqual(view["by_group"], {"rn": 5, "pn_na": 5})

    def test_budget_is_split_across_groups(self):
        calls = []

        def fake(si_, group, max_time, diag, relax):
            calls.append((group, max_time, diag, relax))
            n = len(si_.active_nurses(group))
            grid = {"days": [1, 2],
                    "rows": [{"nurse_index": i, "name": f"x{i}", "cells": ["ช", "off"]}
                             for i in range(n)]}
            return SolveResult("OPTIMAL", True, grid, "csv", "log")

        with mock.patch.object(solver_runner, "_run_one", fake):
            r = run_schedule(_classed_ward(6, 6), 100,
                             diagnose_seconds=20, relax_seconds=10)
        self.assertEqual(calls, [("rn", 50, 10, 5), ("pn_na", 50, 10, 5)])
        self.assertTrue(r.solved)
        self.assertEqual(len(r.grid["rows"]), 12)

        # An ungrouped ward is ONE solve with the full, undivided budget.
        calls.clear()
        si_u = ScheduleInput.from_grid(
            ward_meta={"tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name=f"N{i}") for i in range(12)])
        with mock.patch.object(solver_runner, "_run_one", fake):
            run_schedule(si_u, 100, diagnose_seconds=20, relax_seconds=10)
        self.assertEqual(calls, [(None, 100, 20, 10)])

    def test_mixed_classed_and_unclassed_roster_is_refused(self):
        bad = ScheduleInput.from_grid(
            ward_meta={"tier": "ward"},
            settings={"num_days": 7, "weekends": [6, 7]},
            nurses=[Nurse(name="a", staff_class="rn"), Nurse(name="b")])
        with self.assertRaises(ValueError):
            run_schedule(bad, 5)


class PerGroupRequestFloorTest(unittest.TestCase):
    """RN and PN/NA are solved independently, so each takes its own
    min_request_percent (constraint.md 8)."""

    @staticmethod
    def _ward(rn_off, pn_off, **settings):
        # 7 RN + 7 PN (15-nurse cap), 3 weekdays, coverage 2/1/1 per group => 4
        # working, so at most 3 of any group's day-1 off-requests can be honoured.
        nurses = [Nurse(name=f"R{i}", staff_class="rn",
                        shifts=({1: "off"} if i < rn_off else {})) for i in range(7)]
        nurses += [Nurse(name=f"P{i}", staff_class="pn",
                         shifts=({1: "off"} if i < pn_off else {})) for i in range(7)]
        cov = {f"coverage_{g}_weekday_{s}": n for g in ("rn", "pnna")
               for s, n in (("day", 2), ("evening", 1), ("night", 1))}
        base = {**cov, "num_days": 3, "weekends": [], "head_nurse_special_shift": False,
                "relax_days_off": True}
        base.update(settings)
        return ScheduleInput.from_grid(
            ward_meta={"ward_name": "W", "tier": "ward"}, settings=base, nurses=nurses)

    def test_each_group_uses_its_own_threshold_in_one_solve(self):
        # RN asks for 2 days off (all honourable) at 100; PN asks for 5 (too many
        # to honour) at 0 -> PN drops some, RN drops none, in the SAME run.
        si = self._ward(2, 5, min_request_percent_rn=100, min_request_percent_pnna=0)
        r = run_schedule(si, 30)
        self.assertTrue(r.solved, r.solver_status)
        dropped = r.request_report["dropped"]
        self.assertGreaterEqual(len(dropped), 1)
        self.assertTrue(all(d["name"].startswith("P") for d in dropped))
        # Control: the same ward with PN/NA also at 100 cannot be solved at all.
        hard = self._ward(2, 5, min_request_percent_rn=100, min_request_percent_pnna=100)
        self.assertFalse(run_schedule(hard, 30).solved)

    def test_strict_group_is_not_loosened_by_the_other(self):
        # RN asks 5 off but only 3 can be honoured, and RN is at 100 -> RN is
        # INFEASIBLE even though PN/NA is at 0. Control: RN at 0 solves.
        strict = self._ward(5, 0, min_request_percent_rn=100, min_request_percent_pnna=0)
        self.assertFalse(run_schedule(strict, 30).solved)
        loose = self._ward(5, 0, min_request_percent_rn=0, min_request_percent_pnna=100)
        r = run_schedule(loose, 30)
        self.assertTrue(r.solved, r.solver_status)
        self.assertTrue(all(d["name"].startswith("R") for d in r.request_report["dropped"]))

    def test_legacy_only_setting_behaves_as_before(self):
        # Only the legacy key (an input saved before per-group): both groups get 0.
        si = self._ward(5, 5, min_request_percent=0)
        r = run_schedule(si, 30)
        self.assertTrue(r.solved, r.solver_status)
        self.assertEqual


class OffwardRenderTest(unittest.TestCase):

    def test_offward_render_round_trips(self):
        # A float duty on a non-senior nurse survives the solve and comes back
        # as a token the input grid accepts (VALID_CELL_TOKENS), unchanged.
        si = _feasible_ward()
        si.nurses[8].shifts = {3: "flt:ช"}
        result = run_schedule(si, max_time_seconds=8)

        self.assertTrue(result.solved, result.solver_status)
        row = result.grid["rows"][8]
        self.assertEqual(row["name"], "N8")
        self.assertEqual(row["cells"][2], "flt:ช")
        for r in result.grid["rows"]:
            for cell in r["cells"]:
                self.assertIn(cell, VALID_CELL_TOKENS)
        # The CSV carries the same token for that nurse/day.
        data = [ln.split(",") for ln in result.csv_text.splitlines()[1:]]
        self.assertEqual(data[8][3], "flt:ช")


if __name__ == "__main__":
    unittest.main(verbosity=2)
