"""Pre-solve checks — catch certain infeasibility and quota surprises BEFORE the
~minutes-long CP-SAT solve (FEATURE-GAPS item 3, wireframe 1i).

Design contract: an *error* is only raised when the real solver is guaranteed to
return INFEASIBLE (or crash). Every error comes from a RELAXATION of the model in
``shiftwork.Solver``: the same hard rules, restricted to one day (or two
consecutive days), with every droppable request ignored. If the relaxation is
infeasible, so is the full model. Anything merely risky is a *warning* and never
blocks solving.

This module mirrors (does not import) the solver's hard rules. If a hard rule in
shiftwork.py / dataimporter.py changes, update the matching block here — the
cross-check test in test_presolve.py (solver-feasible fixtures must produce no
errors) guards the direction that matters (no false blockers).

Mirrored as of 2026-09-26:
  constraint.md §2–§6 daily/transition rules, §9 coverage (settings coverage_*),
  §14 new nurse, §15 meetings (tier-gated), §16/§17 head+deputy,
  handleHolidaysAndReq hard-vs-soft classification, DataImporter.transform()
  token parsing, random down-sampling (sampled requests are treated as unknown)
  and the shift-request merge (see _TOKEN_SHIFTS).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from dataimporter import TIER_LIMITS
from webapp.core.tiers import resolve_tier

D, E, N = "D", "E", "N"
_SHIFTS = (D, E, N)
_SYMBOL = {D: "ช", E: "บ", N: "ด"}

# Shifts a request cell FORCES, exactly as DataImporter.transform() + the
# {day: shift} merge leave them when not down-sampled:
#   'ช/บ' produces DAY and EVENING entries, and the merge keeps EVENING only;
#   'ด/บ' produces no entry at all.
# Keep this in lock-step with dataimporter.py (see FEATURE-GAPS / double-token bug).
_TOKEN_SHIFTS: dict[str, tuple[str, ...]] = {
    "ช": (D,), "บ": (E,), "ด": (N,), "ช/บ": (E,), "ด/บ": (),
}
# How many request entries a cell counts against max_req_shifts (pre-merge).
_TOKEN_ENTRIES: dict[str, int] = {"ช": 1, "บ": 1, "ด": 1, "ช/บ": 2, "ด/บ": 0}

_NEW_FLAGS = {"new", "junior", "น้องใหม่"}
_HEAD, _DEPUTY = 0, 1


@dataclass
class Issue:
    code: str
    message: str
    days: list[int] = field(default_factory=list)   # 1-based
    nurse: str | None = None


@dataclass
class PresolveReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    checks_total: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def checks_passed(self) -> int:
        return len(self.passed)


@dataclass
class _NurseDay:
    """What is fixed for one nurse on one day by HARD constraints only."""
    off: bool = False            # hard OFF (off-request or vacation)
    forced: set = field(default_factory=set)   # shifts that must be 1
    meeting: bool = False        # forced DAY, excluded from DAY coverage


def check(si) -> PresolveReport:
    """Run all pre-solve checks on a ScheduleInput that already passed validate()."""
    rep = PresolveReport(checks_total=4)
    s = si.settings
    nurses = si.active_nurses()
    num_days = s["num_days"]
    weekends = set(s["weekends"])                       # 1-based
    tier, _src = resolve_tier(si.ward_meta)
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    soft = s["min_request_percent"] < 100
    seniors = {_HEAD, _DEPUTY} if s["head_nurse_special_shift"] else set()

    # ── 1. Roster vs plan ────────────────────────────────────────────────────
    cap = limits["max_nurses"]
    if cap is not None and len(nurses) > cap:
        rep.errors.append(Issue(
            "tier_nurses",
            f"{len(nurses)} active nurses, but the {tier} plan allows {cap}. "
            f"Disable {len(nurses) - cap} nurse(s) or upgrade the plan."))
    else:
        rep.passed.append(
            f"{len(nurses)} active nurses — within the {tier} plan"
            + (f" ({cap} max)" if cap is not None else ""))

    # ── 2. Request quotas (warnings only) ────────────────────────────────────
    n_warn_before = len(rep.warnings)
    fixed: list[dict[int, _NurseDay]] = []
    for n, nurse in enumerate(nurses):
        per_day: dict[int, _NurseDay] = {}
        offs = [d for d, t in nurse.shifts.items() if t == "off"]
        shift_cells = {d: t for d, t in nurse.shifts.items() if t in _TOKEN_SHIFTS}
        entries = sum(_TOKEN_ENTRIES[t] for t in shift_cells.values())

        offs_sampled = len(offs) > limits["max_holidays"]
        if offs_sampled:
            rep.warnings.append(Issue(
                "quota_off",
                f"{nurse.name} requested {len(offs)} days off; the {tier} plan "
                f"honours {limits['max_holidays']}, picked at random.",
                days=sorted(offs), nurse=nurse.name))
        shifts_sampled = entries > limits["max_req_shifts"]
        if shifts_sampled:
            rep.warnings.append(Issue(
                "quota_shift",
                f"{nurse.name} has {entries} shift requests; the {tier} plan "
                f"honours {limits['max_req_shifts']}, picked at random.",
                days=sorted(shift_cells), nurse=nurse.name))
        mtg_days = sorted(d for d, t in nurse.shifts.items() if t == "mtg")
        if mtg_days and not limits["enable_meetings"]:
            rep.warnings.append(Issue(
                "tier_meetings",
                f"{nurse.name} has meeting (mtg) days, which the {tier} plan "
                f"ignores.", days=mtg_days, nurse=nurse.name))

        for d, tok in nurse.shifts.items():
            nd = per_day.setdefault(d, _NurseDay())
            wk = d in weekends
            if tok == "off":
                # Hard iff not droppable (soft+relax_days_off) and not sampled.
                if not (soft and s["relax_days_off"]) and not offs_sampled:
                    nd.off = True
            elif tok == "vac":
                if not (soft and not s["enforce_vacation"]):
                    nd.off = True
            elif tok == "mtg":
                if limits["enable_meetings"]:
                    nd.meeting = True
            elif tok in _TOKEN_SHIFTS:
                forced = set(_TOKEN_SHIFTS[tok])
                if n in seniors and (wk or forced & {E, N} or tok in ("ช/บ", "ด/บ")):
                    rep.warnings.append(Issue(
                        "senior_request",
                        f"{nurse.name} (head/deputy) asked for {tok} on day {d}; "
                        f"head/deputy work Day only on weekdays and are off on "
                        f"weekends, so it will be ignored.",
                        days=[d], nurse=nurse.name))
                    continue
                if not soft and not shifts_sampled:
                    nd.forced |= forced
        fixed.append(per_day)
    if len(rep.warnings) == n_warn_before:
        rep.passed.append("Requests are within plan quotas")

    # ── 3. Per-nurse contradictions in hard requests ─────────────────────────
    n_err_before = len(rep.errors)
    allow_e_n = s["allow_evening_to_night"]
    allow_n_d = s["allow_night_to_day"]
    for n, nurse in enumerate(nurses):
        for d, nd in sorted(fixed[n].items()):
            if n in seniors and d in weekends and nd.meeting:
                rep.errors.append(Issue(
                    "senior_meeting_weekend",
                    f"{nurse.name} (head/deputy) has a meeting on day {d}, a "
                    f"weekend/holiday — head/deputy must be off then.",
                    days=[d], nurse=nurse.name))
            nxt = fixed[n].get(d + 1)
            if nxt is None or d + 1 > num_days:
                continue
            today = nd.forced | ({D} if nd.meeting else set())
            tomorrow = nxt.forced | ({D} if nxt.meeting else set())
            if not allow_n_d and N in today and D in tomorrow:
                rep.errors.append(Issue(
                    "request_transition",
                    f"{nurse.name}: ด on day {d} then ช on day {d + 1} — "
                    f"Night→Day is not allowed.", days=[d, d + 1], nurse=nurse.name))
            if not allow_e_n and E in today and N in tomorrow:
                rep.errors.append(Issue(
                    "request_transition",
                    f"{nurse.name}: บ on day {d} then ด on day {d + 1} — "
                    f"Evening→Night is not allowed.", days=[d, d + 1], nurse=nurse.name))
    if len(rep.errors) == n_err_before:
        rep.passed.append("No conflicting requests")
    explained = {tuple(i.days) for i in rep.errors[n_err_before:]}

    # ── 4. Coverage: exact per-day, then consecutive-day windows ─────────────
    n_err_before = len(rep.errors)
    new_idx = {n for n, x in enumerate(nurses) if x.type.strip().lower() in _NEW_FLAGS}
    ctx = dict(s=s, nurses=nurses, fixed=fixed, weekends=weekends,
               seniors=seniors, new_idx=new_idx)
    bad_days = set()
    for d in range(1, num_days + 1):
        if not _feasible(ctx, [d]):
            bad_days.add(d)
            rep.errors.append(Issue("coverage_day", _explain_day(ctx, d), days=[d]))
    if not (allow_e_n and allow_n_d):
        for d in range(1, num_days):
            if d in bad_days or d + 1 in bad_days or (d, d + 1) in explained:
                continue
            if not _feasible(ctx, [d, d + 1]):
                rep.errors.append(Issue(
                    "coverage_pair",
                    f"Days {d}–{d + 1}: each day can be covered alone, but not "
                    f"both — nurses on ด day {d} can't work ช day {d + 1}"
                    + ("" if allow_e_n else f", and บ day {d} can't be followed by ด")
                    + ". Lower coverage, move requests, or allow the transition.",
                    days=[d, d + 1]))
    if len(rep.errors) == n_err_before:
        rep.passed.append(f"Coverage can be met on each of the {num_days} days")
    return rep


def _band(s, wk: bool) -> dict[str, int]:
    b = "weekend" if wk else "weekday"
    return {D: s[f"coverage_{b}_day"], E: s[f"coverage_{b}_evening"],
            N: s[f"coverage_{b}_night"]}


def _feasible(ctx, days: list[int]) -> bool:
    """Hard-rule relaxation of shiftwork.Solver restricted to `days` (1-based,
    consecutive). True iff some assignment satisfies every hard rule there."""
    s, nurses, fixed = ctx["s"], ctx["nurses"], ctx["fixed"]
    m = cp_model.CpModel()
    x = {(n, d, sh): m.NewBoolVar(f"x{n}_{d}_{sh}")
         for n in range(len(nurses)) for d in days for sh in _SHIFTS}
    for n in range(len(nurses)):
        for d in days:
            dv, ev, nv = x[n, d, D], x[n, d, E], x[n, d, N]
            m.Add(dv + ev + nv <= 2)                      # §3
            m.Add(dv + nv <= 1)                           # §4
            if n in ctx["new_idx"]:
                m.Add(ev + nv <= 1)                       # §14
            nd = fixed[n].get(d)
            if nd is not None:
                if nd.off:                                # §7 / vacation (hard)
                    m.Add(dv + ev + nv == 0)
                for sh in nd.forced:                      # §8 (hard)
                    m.Add(x[n, d, sh] == 1)
                if nd.meeting:                            # §15
                    m.Add(dv == 1)
            if n in ctx["seniors"]:                       # §16/§17
                if d in ctx["weekends"]:
                    m.Add(dv + ev + nv == 0)
                else:
                    m.Add(ev == 0)
                    m.Add(nv == 0)
    for d in days:                                        # §9 (+§15 exclusion)
        req = _band(s, d in ctx["weekends"])
        for sh in _SHIFTS:
            pool = [x[n, d, sh] for n in range(len(nurses))
                    if not (sh == D and fixed[n].get(d) and fixed[n][d].meeting)]
            m.Add(sum(pool) >= req[sh])
    for d in days[:-1]:                                   # §5 / §6
        for n in range(len(nurses)):
            if not s["allow_evening_to_night"]:
                m.Add(x[n, d, E] + x[n, d + 1, N] <= 1)
            if not s["allow_night_to_day"]:
                m.Add(x[n, d, N] + x[n, d + 1, D] <= 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    solver.parameters.num_search_workers = 1
    status = solver.Solve(m)
    # Only a PROVEN infeasibility blocks; a timeout (UNKNOWN) never does.
    return status != cp_model.INFEASIBLE


def _explain_day(ctx, d: int) -> str:
    s, nurses, fixed = ctx["s"], ctx["nurses"], ctx["fixed"]
    wk = d in ctx["weekends"]
    req = _band(s, wk)
    off = vac_or_off = meet = senior_off = senior_day_only = 0
    for n in range(len(nurses)):
        nd = fixed[n].get(d)
        if nd and nd.off:
            vac_or_off += 1
        elif n in ctx["seniors"] and wk:
            senior_off += 1
        elif nd and nd.meeting:
            meet += 1
        elif n in ctx["seniors"]:
            senior_day_only += 1
    avail = len(nurses) - vac_or_off - senior_off - meet
    parts = []
    if vac_or_off:
        parts.append(f"{vac_or_off} requested off/vacation")
    if senior_off:
        parts.append(f"{senior_off} head/deputy off (weekend)")
    if meet:
        parts.append(f"{meet} in a meeting")
    if senior_day_only:
        parts.append(f"{senior_day_only} head/deputy Day-only")
    why = f" ({', '.join(parts)})" if parts else ""
    kind = "weekend/holiday" if wk else "weekday"
    return (f"Day {d} ({kind}): needs {req[D]} ช + {req[E]} บ + {req[N]} ด, but only "
            f"{avail} of {len(nurses)} nurses can cover{why}. A nurse works at most "
            f"2 shifts a day and never ช+ด together.")
