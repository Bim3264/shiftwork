# Staff classes (RN / PN-NA), off-ward duties (float / refer), day bands
Status: approved · Size: L · Date: 2026-10-17 · Owner: lead-engineer

Three tracks. **A and B are in scope now. C is specced but BLOCKED** pending Bim's
answer on splitting holidays out of `weekends` (§12). Do not implement C.

Base branch: `feature/staff-classes-and-offward`, cut from `5cac8a6` (NOT `main` —
`main` is `db183a3` and lacks `Solver.diagnose()`; see §12 E1).

---

## 1. Objective & interpretation

- **A. Staff classes.** A ward's roster splits into **RN** and **PN/NA** (PN and NA
  are one indistinguishable pool; the PN-vs-NA label is display only). The two
  groups are solved as **two fully independent CP-SAT runs** with their own
  per-shift staffing minimums. No constraint spans the groups.
- **B. Off-ward duties.** `flt` (float to another unit) and `ref` (refer / patient
  transfer escort) are **ONE mechanism**: a hard, head-nurse-authored assignment to
  one named shift that is **excluded from this ward's coverage count** but **counts
  as a worked shift for that nurse**, and is **marked distinctly** on the output.
  They differ only in label. The solver never chooses who floats.
- **C. Day bands.** `weekday` / `weekend` / `holiday` staffing, per group.

**Interpretation that drives the whole design:** the split happens at the
**`to_solver_csv` boundary**, not inside the model. `shiftwork.py` and
`dataimporter.py` learn **nothing** about staff classes — they are handed one
group's roster with that group's six coverage numbers under the existing key
names. This is why A adds no solver complexity and no new infeasibility surface.

**Challenge / pushback accepted into the design:** 18 coverage inputs is a bad UI
and 12 is already borderline. §4 renders them as ONE table (bands × group×shift)
and **hides the PN/NA half entirely until the roster actually contains a PN/NA
nurse**, so a single-class ward sees exactly today's 6 fields.

---

## 2. Current state

Quoted so no executor has to open these files.

- `webapp/core/schedule_input.py:44-71` · `_DEFAULT_SETTINGS` — six coverage keys:
  `coverage_weekday_{day,evening,night}` = 5/3/3, `coverage_weekend_*` = 4/2/2.
  `_COVERAGE_KEYS:80-83` lists them; `_coerce_settings:325-330` clamps each
  present key to `max(0, int(...))`.
- `webapp/core/schedule_input.py:112-127` · `Nurse(name, type="", shifts={}, active=True)`.
  `type` is **seniority only** (`"senior"`/`"new"`/`""`); `from_xlsx:297-299` does
  `if ntype not in ("senior", "new"): ntype = ""`. Staff class is an orthogonal
  axis (a PN can be senior or new) → **needs its own field; do NOT overload `type`.**
- `webapp/core/schedule_input.py:34-42` · `VALID_CELL_TOKENS` =
  `{ช, บ, ด, ช/บ, ด/บ} | {off, mtg, vac} | {""}`; `validate():470-479` rejects
  anything else. `_normalize_token:482-490` lowercases a token **only if the whole
  token is in `_CONTROL_TOKENS`**.
- `webapp/core/schedule_input.py:384-414` · `to_solver_csv()` — emits `[ward]`,
  `[settings]` (num_days, weekends, extra_holidays, `_BOOL_SETTING_KEYS`,
  min_request_percent, `_COVERAGE_KEYS`), then `[schedule]` with header
  `["Name","Type"] + 1..num_days` for `active_nurses()` only.
- `webapp/core/schedule_input.py:353-356` · `_parse_schedule_rows` maps day columns
  by digit header (`day_cols = {int(h): idx for idx, h in enumerate(header) if h.isdigit()}`),
  so **inserting a non-digit column is already safe**.
- `dataimporter.py:287-324` · `transform()` cell cascade: `off` → `days_off`,
  `vac` → `vacations` (never sampled), `mtg` → `meeting_days` (tier-gated),
  else `_cell_shifts` → `shift_cells` (**sampled** to `maxReqShift`).
  `dataimporter.py:242-249` raises `ValueError` when `len(self.df) > max_nurses`
  — **a per-file check, so a per-group CSV would get its own allowance (§4 A5).**
- `shiftwork.py:369-375` · meetings: `self.model.Add(self.assignments[n][d][Shift.DAY] == 1)`,
  "never dropped".
- `shiftwork.py:390-419` · `_constraintMinimumNurse()` — for `Shift.DAY` only, drops
  `self._meeting_nurses_per_day.get(d, set())` from the pool:
  `nurses_assigned = [... for n in range(self.num_nurses) if n not in meeting_nurses_today]`.
  **This is the precedent B generalises to any shift.**
- `shiftwork.py:342-367` · the senior skip-guard pattern (`continue` + `print('[Info] …')`)
  inside `handleHolidaysAndReq`. Copy this shape.
- `shiftwork.py:447-520` · `distribute()` — `shift_counts[n][s] = sum_d assignments[n][d][s]`;
  objective `Minimize(10*Σ(max-min per shift type) + 10*holiday_imb + total_dbl
  [+ 100000*dropped])`.
- `shiftwork.py:785-812` · `_write_schedule()` symbol cascade (day+eve → `ช/บ`,
  night+eve → `ด/บ`, …, OFF → `vac` if `d in self.vacation_days.get(n, ())` else `off`).
- `shiftwork.py:733-741` · `_diag_gate(self, n, d, shifts, kind)` — reifies one cell
  behind a fresh assumption literal and appends
  `{'kind','nurse_index','day' (1-based),'shift'}`.
- `webapp/core/solver_runner.py:88-174` · `run_schedule(schedule_input,
  max_time_seconds, *, diagnose_seconds=0.0, relax_seconds=0.0) -> SolveResult`.
  One Solver, one temp dir, `chdir` (**not concurrency-safe**),
  `_read_output:201-237` → `grid = {"days":[...], "rows":[{nurse_index,name,cells}]}`
  with `nurse_index = pos` in `active_nurses()`.
- `webapp/config.py:34-53,101` · `SOLVER_MAX_TIME_SECONDS=120`,
  `WORKER_JOB_TIMEOUT_SECONDS=180`, `diagnosis_budget()`, and a module-level
  `assert SOLVER_MAX_TIME_SECONDS < WORKER_JOB_TIMEOUT_SECONDS`.
  **Two serial 120 s solves would be 240 s > 180 s — the budget MUST be split (§4 A4).**
- `webapp/core/fairness.py:26-37` · `_SYMBOL = {"ช":"day","บ":"evening","ด":"night"}`,
  `_parse_cell` splits on `"/"` and looks each part up. An unknown cell yields
  `[]`, i.e. **counted as a day off** — so `flt:ด` would silently read as an off day.
- `webapp/core/tiers.py:35-80` · `tier_view(schedule_input)` — `nurse_count =
  len(schedule_input.active_nurses())`, i.e. **already the combined roster**.
- `webapp/core/presolve.py:40-42` · `_TOKEN_SHIFTS` mirrors `_cell_shifts`;
  `:72-78` `_NurseDay(off, forced, meeting)`; `:229-232` `_band(s, wk)`;
  `:235-280` `_feasible(ctx, days)`; `:263-268` coverage pool excludes meeting
  nurses for `D` only. **Known failure mode 9 — this file changes in lock-step.**
- `webapp/web/app.py:101-124` · `_settings_from_form(form)` returns each coverage
  key as `form.get(key, "<hardcoded default>")`.
  `:146-170` `_nurses_from_form` — `token = raw.lower() if raw.lower() in {"off","mtg"} else raw`
  (**pre-existing bug: `vac` is not in that set, so `VAC` fails validation**).
  `:430-439` `_fairness_for_job(job)` — `seniors = {0, 1} if settings.get("head_nurse_special_shift")`.
- `webapp/core/calendar_util.py:52` · `combined = sorted(set(weekends) | set(holiday_days) | set(extra))`;
  autofill stores `combined` into `settings["weekends"]` → **holidays are today
  indistinguishable from weekends** (track C's blocker).

---

## 3. Behaviour spec

### 3.1 Staff class (track A)

- New field `Nurse.staff_class: str`, one of `"rn"`, `"pn"`, `"na"`, `""`.
  Case-insensitive on input; stored lower-case; unknown value → `""`.
- Group mapping: `"rn"` → group `"rn"`; `"pn"` or `"na"` → group `"pn_na"`; `""` → ungrouped.
- `ScheduleInput.solve_groups()` → `[]` when **no** active nurse has a non-empty
  `staff_class` (legacy single solve), else the present groups in the fixed order
  `["rn", "pn_na"]`.
- **Edge — mixed/blank:** if at least one active nurse has a staff class and another
  active nurse has `""`, `validate()` raises
  `"Nurse 'X' has no staff class. Set RN or PN/NA for every nurse, or clear them all."`
  Rationale: otherwise that nurse appears in neither solve and silently vanishes.
  Inactive nurses are exempt (never solved).
- **Edge — one group only:** roster is all RN → `solve_groups() == ["rn"]`, one solve,
  but it uses the **RN** coverage keys (not the legacy ones).
- **Edge — a group with fewer than 2 active nurses while `head_nurse_special_shift`
  is on:** only the **RN** group is senior-ruled (§3.3), so this applies to RN only;
  `validate()` keeps today's ≥2 rule but counts **the RN group** when grouped.
- Twelve new settings, all ints clamped `>= 0`:
  `coverage_{rn,pnna}_{weekday,weekend}_{day,evening,night}`.
  Defaults = today's values (weekday 5/3/3, weekend 4/2/2).
  **Inheritance:** in `_coerce_settings`, when a group key is absent from `raw` **and**
  its legacy counterpart (`coverage_{band}_{shift}`) is present in `raw`, the group
  key takes the legacy value. So every already-saved input keeps its exact numbers.
- The six legacy keys are **retained** and remain authoritative for the ungrouped path.
- **Tier (decided 2026-10-17, do not re-litigate): one combined `max_nurses` ceiling
  across both groups.** PN and NA count. `tier_view.nurse_count` stays the full
  active roster and gains a display-only `by_group` breakdown. A combined-ceiling
  check runs **before** the split so the per-group CSVs cannot each claim the cap.
- Must NOT change: a ward where no nurse has a staff class solves **byte-identically**
  to today, including `to_solver_csv()` output.

### 3.2 Off-ward duty (track B)

- New cell tokens, six in total:
  `flt:ช` `flt:บ` `flt:ด` `ref:ช` `ref:บ` `ref:ด`.
  `flt` = float to another unit, `ref` = refer duty. Separator is a colon.
- Bare `flt` or `ref` with no shift is **invalid**:
  `"Nurse 'X' day 5: 'flt' needs a shift — use flt:ช, flt:บ or flt:ด."`
  (Defaulting to Day would silently produce a wrong schedule.)
- A off-ward cell is **hard and never searched**: the nurse is assigned exactly that
  shift that day, and **no other shift and not OFF** — it is her only work slot that
  day. (Decided: "occupies one shift slot" means it counts as one shift rather than
  blanking the day like `vac`; it does not mean she may also take a ward shift that
  day. Exclusivity removes an output-rendering ambiguity with `ช/บ` / `ด/บ`.)
- **Not counted in this ward's coverage** for that (day, shift) — the nurse is removed
  from that one coverage pool, exactly as `mtg` is removed from the Day pool.
- **Counted as a worked shift for the nurse.** This needs **no objective change**:
  `distribute()` sums the same `assignments[n][d][s]` variable the off-ward duty
  forces to 1, so the shift already enters shift-type balance and already keeps the
  day out of the OFF count. Coverage and fairness read the same variable; we remove
  the nurse only from the *coverage pool*, never from the *count*. A floated night
  is a worked night — which is precisely what the customer asked for.
- **Never sampled** against `maxReqShift` / `maxDayOff` (it is a management fact, like
  `mtg` and `vac`, not a request).
- **Skip-guard (mandatory — known failure mode 1):** head/deputy are pinned to Day-or-OFF
  on weekdays and OFF at weekends (§16/§17), so an off-ward cell on nurse index 0 or 1
  that is on a weekend, or asks for Evening/Night, is **dropped with a printed
  `[Info]` line** — and `presolve` raises a **blocking error** for the same case so the
  user is told before solving rather than after.
- **Rendered** in the output grid as the same token (`flt:ด`), so the result
  round-trips back into the grid editor.
- `fairness.py` counts an off-ward cell as a shift of its type **and** reports a new
  `offward_days` per nurse; it must not fall through to "day off".
- **Edge — off-ward + transition rule:** `flt:ด` on day d then a ward `ช` on day d+1
  is genuinely infeasible (§6). `presolve`'s `request_transition` check must treat
  off-ward shifts as hard forced shifts.
- **Edge — off-ward on an inactive nurse:** ignored (never solved).
- **Edge — not tier-gated.** Available on every tier, like every rule toggle.
  (Adding tier gating is an escalation; not in scope.)

### 3.3 Senior rule and groups

- `head_nurse_special_shift` applies to the **RN group only**. The PN/NA group is
  solved with the flag **forced false**, because head nurse and deputy are RNs and
  nothing should pin the first two PN rows to Day-only.
- Ungrouped wards: unchanged (indices 0 and 1 of the whole roster).

### 3.4 Result shape (see escalation §12 E2 — implement this shape)

`SolveJob.result_grid` stays a single JSON object:

```
{
  "days": [1, …, N],
  "rows": [ {"nurse_index": <global active index>, "name": …,
             "cells": [...], "group": "rn"|"pn_na"|""} , … ],   # flat, ALL nurses
  "groups": [ {"key": "rn", "label": "RN",
               "nurse_indices": [...], "solver_status": "OPTIMAL",
               "request_report": {...}|null} , … ],              # absent when ungrouped
  "request_report": {...}|absent                                 # merged, as today
}
```

`rows` stays the flat full list, so `fairness_stats`, `status_fragment.html` and the
CSV download keep working on old **and** new jobs with no migration. `groups` and
`row["group"]` are purely additive; a pre-existing job simply has neither.

---

## 4. Design

### Files (exhaustive)

| File | Track | Change |
|---|---|---|
| `constant.py` | B | `OffWard` enum, `OFFWARD_SEP`, `OFFWARD_SHIFTS`, `SHIFT_LOCALE`, `parse_offward()` |
| `dataimporter.py` | B | `transform()` parses off-ward cells → `self.reqOffward` |
| `shiftwork.py` | B | `offward_days`, `_offward_applied`, constraint + coverage exclusion + render + warm start + diag gate |
| `constraint.md` | B | new §21; amend §9 and §15 |
| `webapp/core/schedule_input.py` | A+B | `staff_class`, 12 coverage keys, token vocabulary, `to_solver_csv(group=)`, `solve_groups()`, `active_nurses(group=)`, `group_indices()`, `validate()` |
| `webapp/core/tiers.py` | A | `enforce_max_nurses()`, `tier_view(...)["by_group"]` |
| `webapp/core/solver_runner.py` | A | two-solve orchestration, merge, index remap, budget split |
| `webapp/core/fairness.py` | A+B | off-ward cell parsing, `offward_days`, `group=` filter |
| `webapp/core/presolve.py` | A+B | per-group checks, off-ward hard shifts, `senior_offward` error |
| `webapp/web/app.py` | A+B | `class_{r}` field, coverage form keys, `_normalize_token`, per-group fairness |
| `webapp/web/templates/edit_input.html` | A+B | Class column, compact coverage grid, token legend |
| `webapp/web/templates/status_fragment.html` | A+B | per-group schedule + fairness, off-ward cell tint |
| `webapp/tests/*`, `test_shiftwork.py` | A+B | §6 |
| `CODEBASE.md` | A+B | map + settings table + failure modes (lead, at close) |

### New / changed signatures (exact)

```python
# constant.py
class OffWard(Enum):
    FLOAT = "flt"
    REFER = "ref"

OFFWARD_SEP = ":"
OFFWARD_SHIFTS: dict[str, Shift]     # "ช"->Shift.DAY, "บ"->Shift.EVENING, "ด"->Shift.NIGHT
SHIFT_LOCALE: dict[Shift, str]       # inverse of OFFWARD_SHIFTS

def parse_offward(token) -> tuple[str, Shift] | None: ...
    # 'flt:ด' -> ('flt', Shift.NIGHT); 'FLT:ด' -> ('flt', Shift.NIGHT)
    # 'flt', 'flt:x', 'ช', 'off' -> None

# webapp/core/schedule_input.py
GROUP_RN = "rn"
GROUP_PN_NA = "pn_na"
GROUP_LABELS = {"rn": "RN", "pn_na": "PN/NA"}
STAFF_CLASSES = ("rn", "pn", "na")

@dataclass
class Nurse:
    name: str
    type: str = ""
    shifts: dict[int, str] = field(default_factory=dict)
    active: bool = True
    staff_class: str = ""            # NEW, last — keyword-safe for existing callers

def group_of(staff_class: str) -> str: ...          # "" when unclassed
class ScheduleInput:
    def solve_groups(self) -> list[str]: ...
    def active_nurses(self, group: str | None = None) -> list[Nurse]: ...
    def group_indices(self, group: str) -> list[int]: ...   # positions in active_nurses()
    def to_solver_csv(self, *, group: str | None = None) -> str: ...

# webapp/core/tiers.py
def enforce_max_nurses(schedule_input) -> None: ...   # raises ValueError

# webapp/core/fairness.py
def fairness_stats(grid, weekend_days=None, senior_indices=None,
                   *, group: str | None = None) -> dict | None: ...

# webapp/web/app.py
def _settings_from_form(form, current: dict | None = None) -> dict: ...
def _fairness_for_job(job) -> list[dict]: ...   # [{"key","label","fair"}], one entry if ungrouped
```

`run_schedule`'s signature is **unchanged**.

### Data flow

1. Grid/CSV/xlsx → `ScheduleInput` with `Nurse.staff_class` and the 12 coverage keys.
2. `solver_runner.run_schedule` asks `solve_groups()`. Empty → one solve, today's path.
3. Non-empty → `enforce_max_nurses(si)`, then per group:
   `si.to_solver_csv(group=g)` → own temp dir → `shiftwork.Solver` → `output/*Final.csv`.
4. Each group's rows are re-indexed from group-local position to global active index
   via `si.group_indices(g)`, then merged into one flat `rows` list in global order.
5. `shiftwork.py` sees only one group's roster with the six legacy coverage key names
   — it never learns that staff classes exist.

### Rejected alternative

**One solve with a group dimension inside the CP model** (per-group coverage
constraints over one variable set). Rejected: it doubles the coverage constraint
count on a single model, makes `optionHeadNurse`'s index-0/1 assumption ambiguous,
forces `distribute()` to balance two non-interchangeable pools in one objective
(an RN and an NA are not substitutes, so one shared imbalance term is meaningless),
and puts every group bug inside the solver where it is hardest to diagnose. Two
independent solves are what Bim specified and they are also the cheaper model:
2 × (n/2) nurses is a strictly smaller search than 1 × n with extra constraints.

### Kernel (track B) — written by the lead. Use verbatim, adapting only names.

**K1 · `constant.py`** (append; no new imports):

```python
OFFWARD_SEP = ":"


class OffWard(Enum):
    """Off-ward duty kinds. Both are a hard, head-nurse-authored assignment to one
    shift that does NOT count toward this ward's coverage but DOES count as a
    worked shift for the nurse (constraint.md §21). They differ only in label."""
    FLOAT = "flt"   # lent to another unit
    REFER = "ref"   # accompanying a patient transfer


# Off-ward cell suffix (locale symbol) -> solver shift, and its inverse.
OFFWARD_SHIFTS = {
    LocaleShift.DAY.value:     Shift.DAY,
    LocaleShift.EVENING.value: Shift.EVENING,
    LocaleShift.NIGHT.value:   Shift.NIGHT,
}
SHIFT_LOCALE = {v: k for k, v in OFFWARD_SHIFTS.items()}

OFFWARD_KINDS = {k.value for k in OffWard}

# Every valid off-ward cell token, e.g. {'flt:ช', ..., 'ref:ด'}.
OFFWARD_TOKENS = {
    f'{kind}{OFFWARD_SEP}{sym}' for kind in OFFWARD_KINDS for sym in OFFWARD_SHIFTS
}


def parse_offward(token):
    """'flt:ด' -> ('flt', Shift.NIGHT). Returns None for anything that is not a
    well-formed off-ward token (including a bare 'flt'), so callers can fall
    through to the ordinary shift/control vocabulary."""
    text = str(token).strip()
    if OFFWARD_SEP not in text:
        return None
    kind, _sep, symbol = text.partition(OFFWARD_SEP)
    kind = kind.strip().lower()
    if kind not in OFFWARD_KINDS:
        return None
    shift = OFFWARD_SHIFTS.get(symbol.strip())
    if shift is None:
        return None
    return kind, shift
```

**K2 · `dataimporter.py`** — in `__init__` add `self.reqOffward = []` beside
`self.reqMeetings`. In `transform()`, inside the per-row loop add `offward = []` next
to `days_off, vacations, meeting_days`, insert this branch **immediately before**
`elif val in _cell_shifts:`, and append after `self.reqMeetings.append(meeting_days)`:

```python
                elif parse_offward(val) is not None:
                    # Off-ward duty (float / refer), constraint.md §21. A management
                    # fact, not a request: never sampled against maxReqShift.
                    kind, shift = parse_offward(val)
                    offward.append((day_idx, shift, kind))
```
```python
            self.reqOffward.append(offward)      # [(0-based day, Shift, kind)]
```
Import `parse_offward` from `constant`.

**K3 · `shiftwork.py` · `__init__`** (beside `self.meeting_days`):

```python
        # Off-ward duties (float / refer), constraint.md §21.
        # offward_days  = {nurse: {0-based day: (Shift, kind)}} as loaded.
        # _offward_applied = the same, minus cells dropped by the senior
        #   skip-guard; it is the ONLY truth used for coverage exclusion and for
        #   rendering, so a dropped cell can never shrink a coverage pool.
        self.offward_days: dict = {}
        self._offward_applied: dict = {}
        self._offward_excluded: dict = {}   # {(day, Shift): {nurse, ...}}
```

**K4 · `shiftwork.py` · `loadReqShifts`**, after `self.meeting_days = {...}`:

```python
        self.offward_days = {}
        for _n, _cells in enumerate(data.reqOffward):
            for _d, _shift, _kind in _cells:
                self.offward_days.setdefault(_n, {})[_d] = (_shift, _kind)
        if self.offward_days:
            print(f'[Data] Off-ward duties for nurses: {sorted(self.offward_days)}')
```

**K5 · `shiftwork.py` · `handleHolidaysAndReq`**, immediately after the meeting-days
loop and **before** the `MIN_REQUEST_PERCENT` floor block:

```python
        # Off-ward duty (float / refer) — constraint.md §21. Hard and never
        # searched: the head nurse writes it in and the solver always honours it.
        # The nurse works exactly that shift and nothing else that day; she is
        # excluded from this ward's coverage pool for it (_constraintMinimumNurse)
        # yet still counts it as a worked shift, because distribute() sums the very
        # same assignment variable. Coverage and fairness read one variable; only
        # the coverage POOL drops her.
        self._offward_applied = {}
        self._offward_excluded = {}
        for n, days in self.offward_days.items():
            for d, (shift, kind) in days.items():
                if n in senior_indices:
                    if d in self.weekends or shift in (Shift.EVENING, Shift.NIGHT):
                        # Skip-guard (known failure mode 1): §16/§17 pin head and
                        # deputy to Day-or-OFF on weekdays and OFF at weekends, so
                        # this cell would make the model infeasible. Drop it loudly.
                        print(f'[Info] Nurse {n} {kind}{OFFWARD_SEP}{shift.value} on '
                              f'day {d + 1} skipped (conflicts with senior nurse rule).')
                        continue
                self._offward_applied.setdefault(n, {})[d] = (shift, kind)
                self._offward_excluded.setdefault((d, shift), set()).add(n)
                if self._diag_mode:
                    self._diag_gate(n, d, [shift], kind)
                    continue
                # Exactly this shift, nothing else, and not OFF.
                for s in Shift:
                    self.model.Add(self.assignments[n][d][s] == (1 if s is shift else 0))
```

Add `from constant import Shift, LocaleShift, OFFWARD_SEP, SHIFT_LOCALE` to the
module imports.

**K6 · `shiftwork.py` · `_constraintMinimumNurse`** — replace the whole
`if shift == Shift.DAY: … else: …` pool construction with:

```python
                # Nurses who are present but not covering THIS shift for THIS
                # ward: meeting attendees (Day only, §15) and off-ward duties
                # (any shift, §21).
                excluded = set(self._offward_excluded.get((d, shift), ()))
                if shift == Shift.DAY:
                    excluded |= self._meeting_nurses_per_day.get(d, set())
                nurses_assigned = [
                    self.assignments[n][d][shift]
                    for n in range(self.num_nurses)
                    if n not in excluded
                ]
```

**K7 · `shiftwork.py` · `_write_schedule`** — inside the `for d` / `for n` loop,
replace the symbol cascade's entry point so off-ward wins:

```python
                offward = self._offward_applied.get(n, {}).get(d)
                if offward is not None:
                    # Same token the head nurse typed, so the result round-trips
                    # back into the grid editor.
                    shift, kind = offward
                    symbol = f'{kind}{OFFWARD_SEP}{SHIFT_LOCALE[shift]}'
                elif is_day and is_evening:
                    ...                      # existing cascade, unchanged below
```

**K8 · `shiftwork.py` · `_build_warm_start`** — make off-ward the first branch of the
per-day `if`, since it is the most constrained:

```python
                offward = self.offward_days.get(n, {}).get(d)
                if offward is not None:
                    hints = {offward[0]}
                elif n in senior_indices:
                    ...                      # existing chain, unchanged below
```

---

## 5. Work packages

Files are disjoint. **WP1 and WP2 land first and in parallel** (different files);
WP3 is the lead's. WP4–WP6 start once WP1 is green. WP7 last.

| WP | Owner | Model | Files | Depends on | Effort |
|----|-------|-------|-------|-----------|--------|
| 1 | web-specialist | sonnet | `webapp/core/schedule_input.py` | — | M |
| 2 | data-import-specialist | sonnet | `constant.py`, `dataimporter.py` | — | S |
| 3 | lead | — | `shiftwork.py`, `constraint.md` | WP2 | M |
| 4 | web-specialist | sonnet | `webapp/core/solver_runner.py`, `webapp/core/tiers.py` | WP1 | M |
| 5 | web-specialist | sonnet | `webapp/web/app.py`, `edit_input.html`, `status_fragment.html`, `webapp/core/fairness.py` | WP1 | M |
| 6 | web-specialist | sonnet | `webapp/core/presolve.py` | WP1, WP2 | M |
| 7 | test-specialist | sonnet | `webapp/tests/*`, `test_shiftwork.py` | 1–6 | M |

### WP1 — `schedule_input.py` (contracts for both tracks)

1. Add `staff_class: str = ""` as the **last** field of `Nurse` (keeps positional callers working — see `webapp/tests/test_solver_runner.py:28`).
2. Add `GROUP_RN`, `GROUP_PN_NA`, `GROUP_LABELS`, `STAFF_CLASSES`, `group_of()` at module level.
3. Token vocabulary: `from constant import LocaleShift, OFFWARD_TOKENS, parse_offward`;
   `VALID_CELL_TOKENS = _SHIFT_SYMBOLS | _CONTROL_TOKENS | OFFWARD_TOKENS | {""}`.
4. `_normalize_token`: if `parse_offward(token)` returns `(kind, shift)`, return the
   canonical `f"{kind}:{SHIFT_LOCALE[shift]}"` (so `FLT:ด` normalises). Keep the
   existing control-token lowercasing. Pattern: `webapp/core/schedule_input.py:482`.
5. `validate()`: the bare-`flt`/`ref` message from §3.2, and the mixed-staff-class
   message from §3.1. Keep the existing ≥2-active check but count
   `self.active_nurses(GROUP_RN)` when `solve_groups()` is non-empty.
6. 12 coverage keys in `_DEFAULT_SETTINGS`, added to `_COVERAGE_KEYS`. Inheritance in
   `_coerce_settings` exactly as §3.1 (legacy value used **only** when the group key
   is absent from `raw` and the legacy key is present in `raw`).
   Pattern to copy: `webapp/core/schedule_input.py:325-330`.
7. `solve_groups()`, `active_nurses(group=None)`, `group_indices(group)`.
8. `to_solver_csv(*, group=None)`:
   - `group is None` → **byte-identical to today**.
   - otherwise emit the group's `active_nurses(group)`; for each of the six legacy
     coverage key names write the value of `coverage_{rn|pnna}_{band}_{shift}`;
     and for `group == GROUP_PN_NA` write `head_nurse_special_shift,false`
     regardless of the setting (§3.3). Every other emitted line is unchanged.
9. `_parse_schedule_rows`: read a `Class` column by **header name** (case-insensitive,
   non-digit header, so `day_cols` is unaffected). `to_dict`/`from_dict` carry
   `staff_class` (`from_dict` defaults it to `""` for pre-existing rows).
10. `from_xlsx`: look for a header cell whose text starts with `class` in columns 1–6
    of `header_r` or `header_r - 1`; if found read `staff_class` from that column,
    else `""`. Pattern: `webapp/core/schedule_input.py:293-300`.

**Do not** touch `dataimporter.py` or any other file.

### WP2 — `constant.py`, `dataimporter.py`

Apply K1 and K2 verbatim. Nothing else. In particular do **not** sample off-ward
cells and do **not** tier-gate them.

### WP3 — `shiftwork.py`, `constraint.md` (lead)

K3–K8 plus `constraint.md` §21 and amendments to §9/§15.

### WP4 — `solver_runner.py`, `tiers.py`

1. Extract today's body of `run_schedule` into
   `_run_one(schedule_input, group, max_time_seconds, diagnose_seconds, relax_seconds) -> SolveResult`,
   changing only: `schedule_input.to_solver_csv(group=group)` and
   `_read_output(workdir, schedule_input, group)` → names from
   `schedule_input.active_nurses(group)`.
2. `run_schedule` keeps its signature and becomes the orchestrator:
   `groups = schedule_input.solve_groups()`; empty → `return _run_one(si, None, …)`
   (**the legacy path must stay exactly as it is today**). Otherwise
   `enforce_max_nurses(si)`, then **split every budget**:
   `share = max_time_seconds / len(groups)` and the same for `diagnose_seconds` and
   `relax_seconds` — two serial 120 s solves would blow `WORKER_JOB_TIMEOUT_SECONDS`
   (`webapp/config.py:101`).
3. `_merge(si, [(group, result), …]) -> SolveResult`:
   - `solved` = **all** groups solved (half a ward is not a schedule).
   - `solver_status`: the first unsolved group's status; else `"OPTIMAL"` if every
     group is OPTIMAL, else `"FEASIBLE"`.
   - `grid`: the §3.4 shape. Re-index each group's rows from group-local position to
     global via `si.group_indices(g)`; emit `rows` sorted by `nurse_index`.
   - `csv_text`: rebuild ONE csv from the merged grid — index column = global nurse
     index, columns = day numbers — matching what `_read_output` reads today
     (`pd.read_csv(..., index_col=0)`), so `/jobs/{id}/download` is unchanged.
   - `request_report`: summed `total`/`accepted`, concatenated `dropped` (names
     already resolved per group), `percent` recomputed; `None` if every group's is `None`.
   - `log`: each group's log under a `=== RN ===` / `=== PN/NA ===` header.
   - `diagnosis` / `relaxations`: from the first unsolved group, with
     `["group"] = g` added.
4. `tiers.enforce_max_nurses(si)` — resolve the tier, and raise
   `ValueError` naming the combined count and the cap when
   `max_nurses is not None and len(si.active_nurses()) > max_nurses`.
   Docstring must say **why**: `dataimporter.py:242-249` checks one file, so without
   this the split would hand each group its own allowance.
   `tier_view` gains `"by_group": {"rn": <n>, "pn_na": <n>}` for display; `nurse_count`
   and `over_max` stay the **combined** figures.

### WP5 — `app.py`, two templates, `fairness.py`

1. `_settings_from_form(form, current=None)`: for each of the 18 coverage keys use
   `form.get(k)` when present, else `current[k]` when `current` is given, else omit
   the key entirely (letting `_coerce_settings` default it). Pass the row's current
   settings from `_schedule_input_from_form`. This is what lets the UI render only
   one of the two coverage blocks without clobbering the other's saved values.
2. `_nurses_from_form`: read `class_{r}`; replace the ad-hoc
   `raw.lower() if raw.lower() in {"off","mtg"} else raw` with
   `schedule_input._normalize_token(raw)` — which also fixes the pre-existing `VAC`
   bug (`webapp/web/app.py:163`).
3. `_fairness_for_job` → `list[dict]`. Grouped: one entry per
   `result_grid["groups"]`, each calling `fairness_stats(grid, weekends, seniors,
   group=key)`, with `seniors` = the first two `nurse_indices` of the **rn** group
   when `head_nurse_special_shift` is on, and empty for `pn_na`. Ungrouped: one entry
   `{"key": "", "label": "", "fair": <today's call>}`.
4. `fairness.py`: `_parse_cell` must handle an off-ward cell —
   `parse_offward(cell)` first, and on a hit return that one shift key; add
   `offward_days` to each `per_nurse` entry and to `totals`; add
   `has_offward`. Add the `group=` kwarg (filter `grid["rows"]` by
   `row.get("group")`, no-op when `group is None`).
5. `edit_input.html`: a `class_{{ r }}` `<select>` (blank / RN / PN / NA) in the name
   cell beside `type_{{ r }}` (pattern: line 258); the compact coverage block —
   **one** table, rows = bands, and a column group per staff group, where the PN/NA
   column group renders **only if** any active nurse has a staff class (else today's
   6 legacy fields render unchanged); a token legend line for `flt:ช` / `ref:ช`.
6. `status_fragment.html`: when `result_grid.groups` exists, render one `<h3>` +
   table + fairness block per group (iterating `groups` and filtering `rows` by
   `nurse_index in group.nurse_indices`); otherwise today's single table.
   Tint an off-ward cell (a cell containing `:`) distinctly from `off` / `vac`.
   `fair` is now a list — loop it.

### WP6 — `presolve.py` (known failure mode 9: mirror, do not import)

1. `_TOKEN_SHIFTS` stays. Add off-ward via `constant.parse_offward`, mapping
   `Shift.DAY/EVENING/NIGHT` → presolve's `D/E/N`.
2. `_NurseDay` gains `offward: tuple | None = None` (presolve shift letter, kind).
   Set it **and** `nd.forced |= {letter}` for an off-ward cell (always hard, never
   sampled, never soft).
3. `_feasible`: for an off-ward `(n, d)` add `x[n,d,letter] == 1` and the other two
   `== 0`; and exclude that nurse from the coverage pool for **that** shift
   (generalise the Day-only meeting exclusion at `presolve.py:263-268`).
4. New **error** `senior_offward` when nurse 0/1 has an off-ward cell on a
   weekend/holiday or on Evening/Night — the solver skip-guards it (K5), so presolve
   must tell the user *before* the solve that the cell will be ignored.
   **Correction:** because the solver drops it rather than failing, this is a
   **warning**, not an error — an error must only fire when the solver is guaranteed
   INFEASIBLE (`presolve.py:4-9`). Emit `senior_offward` as a warning.
5. `_explain_day`: count off-ward nurses in the unavailable tally.
6. Groups: `check(si)` loops `si.solve_groups() or [None]`. Per group use that group's
   `active_nurses(group)`, that group's coverage band, and `seniors = {0,1}` only for
   `rn` (or for the ungrouped case). Prefix each `Issue.message` with `"RN: "` /
   `"PN/NA: "` when grouped. The `tier_nurses` check stays **combined** (one ceiling,
   §3.1) and runs once, outside the group loop.
7. `_band(s, wk)` → `_band(s, wk, group=None)` resolving the group keys.

### WP7 — tests

Write the §6 tests. Do not modify non-test files; report a needed change instead.

---

## 6. Acceptance tests (must exist and pass)

**Track A**
1. `test_ungrouped_solver_csv_unchanged` (`test_schedule_input`): a ward with no
   `staff_class` → `to_solver_csv()` equals the string produced before this change
   (assert against a literal containing `coverage_weekday_day,5` and header
   `Name,Type,1,...`). **Fails if** the group keys leak into the ungrouped emission.
2. `test_group_csv_contains_only_that_group` (`test_schedule_input`): 4 RN + 3 PN +
   1 NA → `to_solver_csv(group="rn")` has 4 schedule rows, `group="pn_na"` has 4.
   **Fails if** `active_nurses(group)` ignores the class.
3. `test_group_csv_uses_group_coverage` (`test_schedule_input`): set
   `coverage_pnna_weekday_day=2` → `to_solver_csv(group="pn_na")` contains
   `coverage_weekday_day,2` **and** `head_nurse_special_shift,false`.
   **Fails if** the remap or the senior-rule override is dropped.
4. `test_legacy_coverage_is_inherited` (`test_schedule_input`):
   `_coerce_settings({"coverage_weekday_night": 1})` → both
   `coverage_rn_weekday_night` and `coverage_pnna_weekday_night` are 1.
   **Fails if** inheritance is removed (then saved wards silently change coverage).
5. `test_mixed_staff_class_is_rejected` (`test_schedule_input`): one RN + one blank
   active nurse → `validate()` raises mentioning the blank nurse's name.
6. `test_two_groups_produce_two_group_grids` (`test_solver_runner`): 12 active
   nurses, 6 RN / 6 PN, 7 days, coverage lowered to 2/1/1 per group → `solved`,
   `grid["groups"]` has 2 entries with disjoint `nurse_indices` covering 0..11,
   `len(grid["rows"]) == 12`, every row has a `group`.
   **Fails if** the merge or the index remap is wrong.
7. `test_combined_tier_cap_blocks_split` (`test_tiers` + `test_solver_runner`):
   free tier (cap 8), 5 RN + 5 PN active → `enforce_max_nurses` raises, and
   `run_schedule` raises rather than solving.
   **Fails if** the cap is left to DataImporter's per-file check (each group would
   pass at 5 ≤ 8). This test is the guard on the confirmed tier decision.
8. `test_budget_is_split_across_groups` (`test_solver_runner`): monkeypatch
   `_run_one` to record its `max_time_seconds`; two groups with
   `max_time_seconds=100` → each receives 50. **Fails if** the split is removed
   (which would breach `WORKER_JOB_TIMEOUT_SECONDS`).

**Track B**
9. `test_offward_shift_is_forced_and_exclusive` (`test_shiftwork.py`): a nurse with
   `ref:ด` on day 3 → that nurse's day-3 cell is `ref:ด`, and her DAY/EVENING/OFF
   vars for day 3 are 0. **Fails if** the exclusivity constraints are dropped.
10. `test_offward_excluded_from_coverage` (`test_shiftwork.py`): a ward sized so that
    night coverage is met **exactly**; converting one nurse's ward `ด` to `flt:ด`
    makes the model INFEASIBLE. **Fails if** the nurse is left in the coverage pool
    (it would still solve). This is the test the whole feature rests on.
11. `test_offward_counts_as_a_worked_shift` (`webapp/tests/test_fairness.py`): a grid
    row `["flt:ด","off",…]` → `night == 1`, `shifts == 1`, `offward_days == 1`, and
    that day is **not** in `off_days`. **Fails if** `_parse_cell` doesn't learn the
    token (today it reads as a day off).
12. `test_offward_senior_conflict_is_skipped` (`test_shiftwork.py`): `ref:ด` on nurse
    index 0 with `head_nurse_special_shift` on → still solves, the cell is **not**
    `ref:ด`, and the log contains `skipped (conflicts with senior nurse rule)`.
    **Fails if** the skip-guard is removed (model goes INFEASIBLE — known failure mode 1).
13. `test_offward_render_round_trips` (`webapp/tests/test_solver_runner.py`): a solved
    grid's off-ward cell is in `VALID_CELL_TOKENS`.
14. `test_bare_flt_rejected` (`test_schedule_input`): `shifts={1: "flt"}` →
    `validate()` raises naming `flt:ช`.
15. `test_presolve_no_false_blockers_with_offward` (`test_presolve`): a feasible ward
    containing `flt:บ` and `ref:ช` cells produces **no** errors, cross-checked
    against the real solver (extend the existing
    `test_no_false_blockers_vs_real_solver` fixture set). Guards failure mode 9.
16. `test_presolve_offward_transition_error` (`test_presolve`): `flt:ด` day 4 +
    ward `ช` day 5 with `allow_night_to_day=false` → a `request_transition` error.

**Suites that must stay green** (from repo root):
`python -m unittest discover -s webapp/tests -p "test_*.py"` and
`python -m unittest test_shiftwork`.
Note: the Windows run reports ~49 `PermissionError`s in `tearDown` (temp sqlite
files held open). Pre-existing harness artifact, **not** a failure — ignore it.

---

## 7. Risks

Known failure modes touched (names from `CODEBASE.md` → "Known failure modes"):

- **1. Constraint vs request conflict → silent infeasibility.** The new off-ward rule
  needs its skip-guard (K5) and its `[Info]` line. Guarded by test 12.
- **2. Senior rules cover BOTH head (0) and deputy (1).** The off-ward skip-guard and
  the per-group senior set must both use `{0, 1}`, and only for the RN group.
- **3. `mtg` days**: forced Day, excluded from Day coverage. K6 **replaces** that
  special case with a general exclusion — the meeting behaviour must be unchanged.
- **4. `vac` is protected**: untouched. An off-ward cell is a *worked* shift, a `vac`
  cell is not; do not merge their handling.
- **5. Tier resolution**: unchanged. The new `enforce_max_nurses` adds a combined
  ceiling only; it must not change which tier resolves.
- **6. Input contract**: new `Class` column and six new cell tokens. CSV and Excel
  must still round-trip identically through `to_solver_csv()`.
- **7. Half-wired settings**: 12 new coverage keys must pass every layer
  (solver → dataimporter → schedule_input → form → template → display → tests).
  Track A deliberately stops at `to_solver_csv`, which remaps onto the existing
  solver/dataimporter keys — so for these 12, "every layer" ends there, by design.
- **8. No redundant model structure**: the off-ward constraint adds 4 `Add` calls per
  off-ward cell and **no new variables**. Do not introduce an indicator variable.
- **9. Pre-solve mirror drift**: WP6 is mandatory and lands with WP2/WP3, not after.

Additional risks specific to this change:

- **R1. Fairness distortion from forced load.** An off-ward shift is forced, so the
  shift-type imbalance term will try to give every other nurse the same count, which
  can inflate total ward work when one nurse has many off-ward cells. Identical in
  kind to the existing forced-`mtg` effect and **accepted**; recorded in
  `constraint.md` §21 rather than fixed.
- **R2. Solve-time budget.** Two serial solves halve each group's search time.
  Escalated (§12 E5).
- **R3. Pre-existing, out of scope:** `webapp/core/relaxation.py` does not exist, yet
  `webapp/core/solver_runner.py:139` imports it and `webapp/worker/tasks.py:52` passes
  `relax_seconds>0`. The `except Exception` at `:145` swallows the `ImportError`, so
  the advertised relaxation suggestions never appear. Reported, not fixed here.

**What the lead verifies at review:** the diff's file list matches §4; the ungrouped
path is untouched (test 1); test 10 genuinely flips to INFEASIBLE when the exclusion
is reverted; the skip-guard prints and continues; presolve changed in the same pass;
no new model variables; no weakened tests.

---

## 8. Do NOT

- Edit files outside your WP's list. Propose the diff in your report instead.
- Add dependencies. Change signatures not listed in §4.
- Rewrite or weaken a test to make it pass.
- Implement **track C** (holiday band / splitting `weekends`). It is blocked.
- Change solver output semantics for an ungrouped ward, tier/licensing resolution, or
  the CSV/Excel contract beyond the `Class` column and the six off-ward tokens.
- Tier-gate off-ward duties, or sample them against request quotas.
- Default a bare `flt`/`ref` to a shift.
- Touch `webapp/core/relaxation.py` (R3) or chase the Windows `tearDown`
  `PermissionError`s.

## 9. STOP and report instead of guessing if

- A named file/symbol is missing or its signature differs from §2/§4.
- The spec contradicts the code, `constraint.md`, or itself.
- An acceptance test cannot pass without breaking §8.
- You need to change a file another WP owns.

## 10. Report format

Files changed (paths only) · test commands + pass/fail counts · deviations from spec
+ why · open questions. No diffs, no file contents.

## 11. Done / deviations log

- [ ] Acceptance tests + suites green (run by the lead)
- [ ] `constraint.md` / `CODEBASE.md` updated
- [ ] Status → done

Deviations:

---

## 12. Escalations (lead → Bim)

**E1. Base commit.** The brief said "branch off `main` (currently at `5cac8a6`)", but
`main` is `db183a3`; `5cac8a6` exists only on `feature/infeasibility-diagnosis`. Since
the brief also requires new constraints to be diagnosable via `Solver.diagnose()`,
I branched from **`5cac8a6`**. *Recommendation:* merge
`feature/infeasibility-diagnosis` into `main` so the two agree.

**E2. Two result grids vs. saved flat jobs.** *Recommendation: the §3.4 shape* —
`rows` stays one flat list of every nurse, each row gains `"group"`, and a new
top-level `"groups"` array carries per-group metadata. No row duplication, no
migration, and `fairness_stats` / `status_fragment.html` / the CSV download keep
working on old jobs (which simply have neither key). Rejected: a top-level
`{"rn": grid, "pn_na": grid}` — it breaks every existing reader and forces either a
migration or a version flag on a JSON column.

**E3. Splitting holidays out of `weekends` (track C).** *Recommendation: do it, with
the holiday coverage band defaulting to the weekend band's values.* Then the split is
behaviour-preserving even after a user re-runs auto-fill: days move from `weekends`
into the new `holidays` list but get identical staffing numbers, and
`weekends ∪ holidays` continues to drive the senior weekend rule (§17) and the
fairness "weekend" column. Saved months are unaffected until auto-fill is pressed.
Without that default, re-running auto-fill on an existing month would change its
coverage — which is why C is blocked on your call.

**E4. Tier ceiling — CLOSED 2026-10-17.** One combined `max_nurses` across RN and
PN/NA; PN and NA count. Implemented via `tiers.enforce_max_nurses` (test 7).

**E5. Solve-time budget.** Two groups halve the per-group search (120 s → 60 s each).
*Recommendation: accept the split*, because it preserves the asserted invariant
`SOLVER_MAX_TIME_SECONDS < WORKER_JOB_TIMEOUT_SECONDS` (`webapp/config.py:101`) and
each group's model is roughly half the size, so a half budget is not half the quality.
If grouped wards time out in practice, the fix is to raise both
`SOLVER_MAX_TIME_SECONDS` and `WORKER_JOB_TIMEOUT_SECONDS` together, not to drop the split.

**E6. Senior rule in the PN/NA group.** Default chosen: `head_nurse_special_shift` is
forced **off** for PN/NA, because head nurse and deputy are RNs and otherwise the
first two PN rows would be pinned to Day-only. Say the word if a ward wants a
"head PN".

**E7. Off-ward exclusivity.** Chosen: an off-ward nurse works no other ward shift that
day. Alternative (allowing e.g. `flt:ด` + a ward `บ`) needs a compound output token
and is operationally odd; it can be added later without changing the token vocabulary.
