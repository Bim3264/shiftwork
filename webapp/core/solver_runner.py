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
from webapp.core.schedule_input import ScheduleInput

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


def run_schedule(
    schedule_input: ScheduleInput,
    max_time_seconds: float,
) -> SolveResult:
    """Run the solver on `schedule_input` and return a normalized result."""
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
        f.write(schedule_input.to_solver_csv())

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
        if solved:
            csv_text, grid = _read_output(workdir, schedule_input)
    finally:
        os.chdir(prev_cwd)
        _rmtree_quiet(workdir)

    return SolveResult(
        solver_status=_STATUS_NAMES.get(status_int, str(status_int)),
        solved=solved,
        grid=grid,
        csv_text=csv_text,
        log=log_buf.getvalue(),
    )


def _read_output(
    workdir: str, schedule_input: ScheduleInput
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
    rows: list[dict[str, Any]] = []
    for pos, (_idx, series) in enumerate(df.iterrows()):
        name = (
            schedule_input.nurses[pos].name
            if pos < len(schedule_input.nurses)
            else f"Nurse {pos}"
        )
        rows.append({
            "nurse_index": pos,
            "name": name,
            "cells": [str(v) for v in series.tolist()],
        })

    grid = {"days": day_labels, "rows": rows}
    return csv_text, grid


def _rmtree_quiet(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
