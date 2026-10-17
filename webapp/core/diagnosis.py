"""diagnosis — turn the solver's raw infeasibility diagnosis into text.

`Solver.diagnose()` reports nurses by 0-based index into the active-nurse list
that was fed to the solver. This module maps those indices back to names (the
same mapping as solver_runner._name_request_report) and renders a short plain-
text block that is printed into the solve log, which the status page already
shows under the INFEASIBLE "Solver log" details.
"""

from __future__ import annotations

import copy
from typing import Optional


def name_diagnosis(diagnosis: Optional[dict], schedule_input) -> Optional[dict]:
    """Return a copy of `diagnosis` with a 'name' added to each request record.

    Indices refer to schedule_input.active_nurses() (inactive nurses are never
    sent to the solver); out-of-range indices fall back to "Nurse {i}"."""
    if not diagnosis:
        return None
    named = copy.deepcopy(diagnosis)
    active = schedule_input.active_nurses()
    for rec in named.get("requests", []):
        i = rec.get("nurse_index", 0)
        rec["name"] = active[i].name if 0 <= i < len(active) else f"Nurse {i}"
    return named


def render_text(named: Optional[dict], relaxations: Optional[list]) -> str:
    """Plain-text rendering (<= ~25 lines) starting with "[Diagnosis] "."""
    if not named:
        return ""
    reason = named.get("reason")
    lines: list[str] = []
    if reason == "requests":
        reqs = named.get("requests", [])
        lines.append(
            f"[Diagnosis] These {len(reqs)} request(s) cannot all be satisfied "
            "- dropping any one of them may be enough:"
        )
        for r in reqs:
            name = r.get("name") or f"Nurse {r.get('nurse_index')}"
            lines.append(f"  · {name} day {r['day']}: {r['shift']} ({r['kind']})")
        if named.get("truncated"):
            lines.append("  (list truncated)")
    elif reason == "coverage":
        lines.append("[Diagnosis] Coverage cannot be met:")
        for c in named.get("coverage", []):
            lines.append(f"  · day {c['day']}: needs {c['required']} {c['shift']}")
        if named.get("truncated"):
            lines.append("  (list truncated)")
    elif reason == "structural":
        lines.append(
            "[Diagnosis] The ward's fixed rules (senior staff, shift "
            "transitions, daily limits) are contradictory on their own, "
            "independent of requests and coverage."
        )
    else:
        lines.append(
            "[Diagnosis] The diagnosis did not finish within its time budget, "
            "so no cause is reported."
        )

    if relaxations:
        lines.append("Relaxations tested:")
        for t in relaxations:
            f = t.get("feasible")
            tag = "works" if f is True else ("no" if f is False else "untested")
            lines.append(f"  · [{tag}] {t.get('label', '')} - {t.get('detail', '')}")
    return "\n".join(lines)
