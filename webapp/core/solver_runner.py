"""solver_runner — the single boundary between the web app and the solver.

Given a ScheduleInput, it runs the existing `shiftwork.Solver` in an isolated
temporary directory and returns a normalized result (status + grid + CSV + log).
Nothing else in the app imports `shiftwork` directly; keeping that dependency in
one place means the solver can evolve without touching the web/worker layers.

Isolation: the Solver writes its output CSV relative to the current working
directory, so each run gets its own temp dir with input/ and output/ subfolders
and we chdir into it for the duration. Because chdir is process-global, this
function is NOT safe to call concurrently within one process — which is fine:
the worker runs one solve at a time (each solve already uses every CPU core).
"""

from __future__ import annotations

import contextlib
import glob
import io
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
from ortools.sat.python import cp_model

import shiftwork
from webapp.core.schedule_input import GROUP_LABELS, ScheduleInput
from webapp.core.tiers import enforce_max_nurses

# CP-SAT status int -> human-readable name.
_STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}
_SOLVED_STATUSES = {cp_model.OPTIMAL, cp_model.FEASIBLE}


@dataclass
class SolveResult:
    solver_status: str            # OPTIMAL | FEASIBLE | INFEASIBLE | ...
    solved: bool                  # True iff a usable schedule was produced
    grid: Optional[dict]          # rendered schedule, or None if unsolved
    csv_text: Optional[str]       # raw output CSV for download, or None
    log: str                      # captured solver stdout
    request_report: Optional[dict] = None  # soft-request acceptance summary
    diagnosis: Optional[dict] = None       # INFEASIBLE only: named unsat core
    relaxations: Optional[list] = None     # INFEASIBLE only: relaxation trials


def probe_feasible(schedule_input: ScheduleInput, max_time_seconds: float) -> str:
    """Feasibility-only solve of `schedule_input`; returns the CP-SAT status name.

    No objective, no output file. Same temp-dir/chdir pattern as run_schedule,
    so it shares its one-solve-at-a-time constraint."""
    num_days = schedule_input.settings["num_days"]
    weekends_0 = [d - 1 for d in schedule_input.settings["weekends"]]
    allow_e_n = schedule_input.settings["allow_evening_to_night"]
    allow_n_d = schedule_input.settings["allow_night_to_day"]

    prev_cwd = os.getcwd()
    workdir = tempfile.mkdtemp(prefix="shiftwork_probe_")
    try:
        os.makedirs(os.path.join(workdir, "input"))
        os.makedirs(os.path.join(workdir, "output"))
        input_path = os.path.join(workdir, "input", "schedule.csv")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(schedule_input.to_solver_csv())
        os.chdir(workdir)
        with contextlib.redirect_stdout(io.StringIO()):
            solver = shiftwork.Solver(
                input_path, num_days, weekends_0,
                allow_shift_e_n=allow_e_n, allow_shift_n_d=allow_n_d,
            )
            solver.build(with_objective=False)
            status_int = solver.solve(
                max_time_seconds=max_time_seconds, write_output=False
            )
    finally:
        os.chdir(prev_cwd)
        _rmtree_quiet(workdir)
    return _STATUS_NAMES.get(status_int, str(status_int))


def run_schedule(
    schedule_input: ScheduleInput,
    max_time_seconds: float,
    *,
    diagnose_seconds: float = 0.0,
    relax_seconds: float = 0.0,
) -> SolveResult:
    """Run the solver on `schedule_input` and return a normalized result.

    A ward whose nurses carry no staff class takes the legacy path: exactly one
    solve over the whole roster. A classed ward (RN / PN-NA) is solved as one
    fully independent run per group and the results are merged. Every time
    budget is divided across the groups so the total stays under
    WORKER_JOB_TIMEOUT_SECONDS (webapp/config.py asserts
    SOLVER_MAX_TIME_SECONDS < WORKER_JOB_TIMEOUT_SECONDS).
    """
    groups = schedule_input.solve_groups()
    if not groups:
        return _run_one(
            schedule_input, None, max_time_seconds, diagnose_seconds, relax_seconds
        )

    # The combined tier ceiling must be checked BEFORE the split:
    # DataImporter only checks one file at a time, so each per-group CSV would
    # otherwise pass on its own and the ward would get one allowance per group.
    enforce_max_nurses(schedule_input)

    # validate() normally blocks a mixed classed/unclassed roster, but this
    # function is also called directly (worker, tests). A nurse who belongs to
    # no group would silently be missing from the schedule.
    grouped = sum(len(schedule_input.active_nurses(g)) for g in groups)
    total = len(schedule_input.active_nurses())
    if grouped != total:
        raise ValueError(
            f"{total - grouped} active nurse(s) have no staff class. "
            "Set RN or PN/NA for every nurse, or clear them all."
        )

    share = len(groups)
    results = [
        (g, _run_one(
            schedule_input, g,
            max_time_seconds / share,
            diagnose_seconds / share,
            relax_seconds / share,
        ))
        for g in groups
    ]
    return _merge(schedule_input, results)


class _GroupView:
    """Just enough of a ScheduleInput for diagnosis naming: solver indices are
    group-local, so names must be looked up in that group's roster."""

    def __init__(self, nurses):
        self._nurses = nurses

    def active_nurses(self, group=None):
        return self._nurses


def _run_one(
    schedule_input: ScheduleInput,
    group: Optional[str],
    max_time_seconds: float,
    diagnose_seconds: float,
    relax_seconds: float,
) -> SolveResult:
    """One solver run over `group`'s roster (or the whole roster if None)."""
    num_days = schedule_input.settings["num_days"]
    # Solver constructor expects 0-based weekend indices; our canonical form is
    # 1-based (as written in the CSV), so convert at this boundary.
    weekends_0 = [d - 1 for d in schedule_input.settings["weekends"]]
    allow_e_n = schedule_input.settings["allow_evening_to_night"]
    allow_n_d = schedule_input.settings["allow_night_to_day"]

    prev_cwd = os.getcwd()
    workdir = tempfile.mkdtemp(prefix="shiftwork_solve_")
    os.makedirs(os.path.join(workdir, "input"))
    os.makedirs(os.path.join(workdir, "output"))
    input_path = os.path.join(workdir, "input", "schedule.csv")
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(schedule_input.to_solver_csv(group=group))

    log_buf = io.StringIO()
    try:
        os.chdir(workdir)
        with contextlib.redirect_stdout(log_buf):
            solver = shiftwork.Solver(
                input_path, num_days, weekends_0,
                allow_shift_e_n=allow_e_n, allow_shift_n_d=allow_n_d,
            )
            status_int = solver.run(max_time_seconds=max_time_seconds)

        solved = status_int in _SOLVED_STATUSES
        grid, csv_text = (None, None)
        report = None
        diagnosis = None
        relaxations = None
        if not solved and status_int == cp_model.INFEASIBLE and diagnose_seconds > 0:
            # Explain the contradiction. Printed inside the redirected-stdout
            # scope so it lands in `log`. A failure here must never change the
            # job's outcome.
            with contextlib.redirect_stdout(log_buf):
                try:
                    from webapp.core.diagnosis import name_diagnosis, render_text

                    raw = solver.diagnose(diagnose_seconds)
                    view = (
                        schedule_input if group is None
                        else _GroupView(schedule_input.active_nurses(group))
                    )
                    named = name_diagnosis(raw, view)
                    diagnosis = named
                    if relax_seconds > 0 and group is None:
                        try:
                            from webapp.core import relaxation

                            trials = relaxation.suggest(
                                schedule_input, raw, budget_seconds=relax_seconds
                            )
                            relaxations = [t.to_dict() for t in trials]
                        except Exception as exc:  # keep the diagnosis text
                            print(f"[Diagnosis] relaxation unavailable: {exc}")
                    text = render_text(named, relaxations)
                    if text:
                        print(text)
                except Exception as exc:
                    diagnosis = None
                    relaxations = None
                    print(f"[Diagnosis] unavailable: {exc}")
        if solved:
            csv_text, grid = _read_output(workdir, schedule_input, group)
            report = _name_request_report(
                getattr(solver, "request_report", None), schedule_input, group
            )
            if grid is not None and report is not None:
                grid["request_report"] = report
    finally:
        os.chdir(prev_cwd)
        _rmtree_quiet(workdir)

    return SolveResult(
        solver_status=_STATUS_NAMES.get(status_int, str(status_int)),
        solved=solved,
        grid=grid,
        csv_text=csv_text,
        log=log_buf.getvalue(),
        request_report=report,
        diagnosis=diagnosis,
        relaxations=relaxations,
    )


def _name_request_report(report, schedule_input, group=None):
    """Attach nurse names to a solver request_report (which uses indices).

    The solver's nurse index matches the active-nurse ordering fed to it, so map
    dropped-request indices back through that list."""
    if not report:
        return None
    solved_nurses = schedule_input.active_nurses(group)

    def _name(i):
        return solved_nurses[i].name if i < len(solved_nurses) else f"Nurse {i}"

    return {
        "total": report["total"],
        "accepted": report["accepted"],
        "percent": report["percent"],
        "dropped": [
            {"name": _name(r["nurse_index"]), "day": r["day"],
             "shift": r["shift"], "kind": r["kind"]}
            for r in report["dropped"]
        ],
    }


def _read_output(
    workdir: str, schedule_input: ScheduleInput, group: Optional[str] = None
) -> tuple[str, dict]:
    """Read the single output CSV the solver wrote and render it into a grid.

    The output CSV is transposed: row label = nurse index (0-based), columns =
    day numbers, cells = shift symbols. Nurse names are recovered by position
    from the input (the solver only knows nurses by index).
    """
    matches = glob.glob(os.path.join(workdir, "output", "*Final.csv"))
    if not matches:
        raise RuntimeError("solver reported success but wrote no output file")
    out_path = matches[0]

    with open(out_path, encoding="utf-8-sig") as f:
        csv_text = f.read()

    df = pd.read_csv(out_path, index_col=0, encoding="utf-8-sig")
    day_labels = [int(c) for c in df.columns]
    # Only active nurses (of this group, if any) were sent to the solver, in
    # this order, so map the output rows back to that same list. With a group
    # the nurse_index is group-local; _merge remaps it to the global index.
    solved_nurses = schedule_input.active_nurses(group)
    rows: list[dict[str, Any]] = []
    for pos, (_idx, series) in enumerate(df.iterrows()):
        name = (
            solved_nurses[pos].name
            if pos < len(solved_nurses)
            else f"Nurse {pos}"
        )
        rows.append({
            "nurse_index": pos,
            "name": name,
            "cells": [str(v) for v in series.tolist()],
        })

    grid = {"days": day_labels, "rows": rows}
    return csv_text, grid


def _merge(
    schedule_input: ScheduleInput, results: list[tuple[str, SolveResult]]
) -> SolveResult:
    """Combine per-group results into one SolveResult (grid shape: spec 3.4).

    Solved only if EVERY group solved - half a ward is not a schedule."""
    log = "\n".join(
        f"=== {GROUP_LABELS.get(g, g)} ===\n{r.log}" for g, r in results
    )
    unsolved = next(((g, r) for g, r in results if not r.solved), None)
    if unsolved is not None:
        ug, ur = unsolved
        diagnosis = None
        if ur.diagnosis:
            diagnosis = dict(ur.diagnosis)
            diagnosis["group"] = ug
        relaxations = None
        if ur.relaxations:
            relaxations = [dict(t, group=ug) for t in ur.relaxations]
        return SolveResult(
            solver_status=ur.solver_status,
            solved=False,
            grid=None,
            csv_text=None,
            log=log,
            request_report=None,
            diagnosis=diagnosis,
            relaxations=relaxations,
        )

    status = (
        "OPTIMAL" if all(r.solver_status == "OPTIMAL" for _g, r in results)
        else "FEASIBLE"
    )

    days = results[0][1].grid["days"]
    rows: list[dict[str, Any]] = []
    group_meta: list[dict[str, Any]] = []
    for g, r in results:
        global_idx = schedule_input.group_indices(g)
        for row in r.grid["rows"]:
            local = row["nurse_index"]
            rows.append({
                "nurse_index": global_idx[local],
                "name": row["name"],
                "cells": row["cells"],
                "group": g,
            })
        group_meta.append({
            "key": g,
            "label": GROUP_LABELS.get(g, g),
            "nurse_indices": list(global_idx),
            "solver_status": r.solver_status,
            "request_report": r.request_report,
        })
    rows.sort(key=lambda row: row["nurse_index"])

    reports = [r.request_report for _g, r in results if r.request_report is not None]
    report = None
    if reports:
        total = sum(x["total"] for x in reports)
        accepted = sum(x["accepted"] for x in reports)
        report = {
            "total": total,
            "accepted": accepted,
            "percent": round(100.0 * accepted / total, 1) if total else 100.0,
            "dropped": [d for x in reports for d in x["dropped"]],
        }

    grid: dict[str, Any] = {"days": days, "rows": rows, "groups": group_meta}
    if report is not None:
        grid["request_report"] = report

    # Same layout the solver writes and _read_output reads: index = nurse index
    # (now global), columns = day numbers.
    df = pd.DataFrame(
        [row["cells"] for row in rows],
        index=[row["nurse_index"] for row in rows],
        columns=[str(d) for d in days],
    )
    csv_text = df.to_csv(lineterminator="\n")

    return SolveResult(
        solver_status=status,
        solved=True,
        grid=grid,
        csv_text=csv_text,
        log=log,
        request_report=report,
        diagnosis=None,
        relaxations=None,
    )


def _rmtree_quiet(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
