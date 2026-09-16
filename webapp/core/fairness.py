"""Per-nurse fairness statistics (wireframe 1p).

Pure, side-effect-free derivation from a solved ``result_grid`` — the same JSON
already stored on ``SolveJob.result_grid``. No solver run, no DB column: this can
be computed at display time and works retroactively on old jobs.

Cell vocabulary written by ``shiftwork._write_schedule`` (see constant.LocaleShift):
    ช        Day
    บ        Evening
    ด        Night
    ช/บ      Day + Evening   (double shift -> counts one Day AND one Evening)
    ด/บ      Night + Evening (double shift -> counts one Night AND one Evening)
    off      day off (discretionary)
    vac      approved vacation / leave (an off-day, but reported separately)
A double-shift cell contributes to BOTH of its shift counts (so ``shifts`` is a
slot count, while ``working_days`` counts the calendar day only once). A ``vac``
cell is a non-working day like ``off``, but is counted as ``vac_days`` and kept
OUT of ``off_days`` so approved leave isn't conflated with discretionary days off
(mirroring the solver, which balances only discretionary off).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# locale symbol -> canonical shift key
_SYMBOL = {"ช": "day", "บ": "evening", "ด": "night"}
_VAC = "vac"  # LocaleShift.VACATION.value — approved-leave cell


def _parse_cell(cell: str) -> list[str]:
    """Return the shift keys in one grid cell. 'off'/'' -> []; 'ช/บ' -> [day, evening]."""
    out: list[str] = []
    for part in str(cell).split("/"):
        key = _SYMBOL.get(part.strip())
        if key:
            out.append(key)
    return out


def _spread(values: list[int]) -> dict[str, float]:
    """min / max / range / mean over a per-nurse metric. Empty -> zeros."""
    if not values:
        return {"min": 0, "max": 0, "range": 0, "mean": 0.0}
    lo, hi = min(values), max(values)
    return {"min": lo, "max": hi, "range": hi - lo,
            "mean": round(sum(values) / len(values), 1)}


def fairness_stats(grid: dict[str, Any],
                   weekend_days: Optional[Iterable[int]] = None,
                   senior_indices: Optional[Iterable[int]] = None) -> Optional[dict]:
    """Compute fairness stats from a solved result grid.

    ``grid``          -- ``SolveJob.result_grid`` = {"days":[...], "rows":[{name, cells:[...]}]}.
    ``weekend_days``  -- 1-based day numbers treated as weekend/holiday (from the
                         input's ``settings["weekends"]``). Used for the
                         "unpopular slots" columns; None -> those columns are 0.
    ``senior_indices``-- nurse indices that follow the Day-only senior rule
                         (head/deputy). They are tagged in ``per_nurse`` and
                         EXCLUDED from ``spread`` so the fairness range reflects
                         the interchangeable staff, not rule-bound outliers.

    Returns None if the grid has no rows (nothing to summarise).
    """
    rows = (grid or {}).get("rows") or []
    if not rows:
        return None
    seniors = {int(i) for i in (senior_indices or [])}

    day_labels = grid.get("days") or []
    num_days = len(day_labels)
    # weekend membership per COLUMN index, matched on the day label (1-based).
    wk = {int(d) for d in (weekend_days or [])}
    is_weekend_col = [
        (str(lbl).strip().isdigit() and int(lbl) in wk) for lbl in day_labels
    ]

    per_nurse: list[dict] = []
    for pos, r in enumerate(rows):
        idx = int(r.get("nurse_index", pos))
        cells = r.get("cells") or []
        c = {"day": 0, "evening": 0, "night": 0}
        working_days = 0
        vac_days = 0
        weekend_shifts = 0
        night_weekend = 0
        for i, cell in enumerate(cells):
            keys = _parse_cell(cell)
            if keys:
                working_days += 1
            elif str(cell).strip().lower() == _VAC:
                vac_days += 1
            for k in keys:
                c[k] += 1
                if i < len(is_weekend_col) and is_weekend_col[i]:
                    weekend_shifts += 1
                    if k == "night":
                        night_weekend += 1
        shifts = c["day"] + c["evening"] + c["night"]
        per_nurse.append({
            "name": r.get("name", ""),
            "nurse_index": idx,
            "senior": idx in seniors,
            "day": c["day"],
            "evening": c["evening"],
            "night": c["night"],
            "shifts": shifts,
            "working_days": working_days,
            # off_days = discretionary days off only (approved vacation excluded,
            # so working_days + off_days + vac_days == num_days).
            "off_days": num_days - working_days - vac_days,
            "vac_days": vac_days,
            "weekend_shifts": weekend_shifts,
            "night_weekend": night_weekend,
        })

    def col(key: str, only=None) -> list[int]:
        src = per_nurse if only is None else [n for n in per_nurse if only(n)]
        return [n[key] for n in src]

    totals = {k: sum(col(k)) for k in
              ("day", "evening", "night", "shifts", "weekend_shifts", "vac_days")}
    # Spread reflects the interchangeable staff: drop rule-bound seniors, but
    # never end up with an empty basis (fall back to everyone if all are senior).
    regular = [n for n in per_nurse if not n["senior"]]
    basis = (lambda n: not n["senior"]) if regular else (lambda n: True)
    spread = {k: _spread(col(k, basis)) for k in
              ("night", "evening", "shifts", "off_days", "weekend_shifts")}

    return {
        "num_days": num_days,
        "num_nurses": len(per_nurse),
        "has_vacations": any(n["vac_days"] for n in per_nurse),
        "senior_excluded": len([n for n in per_nurse if n["senior"]]) if regular else 0,
        "per_nurse": per_nurse,
        "totals": totals,
        "spread": spread,
    }
