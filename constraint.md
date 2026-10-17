# ShiftWork — Constraint Reference

This file lists every constraint in the solver.
Edit a constraint here, then ask Claude Code to apply the change to `shiftwork.py`.

**Notation**
- `n` = nurse index (0-based)
- `d` = day index (0-based)
- `D` = total days in month
- `a[n][d][s]` = BoolVar — 1 if nurse `n` is assigned shift `s` on day `d`
- Shifts: `DAY`, `EVENING`, `NIGHT`, `OFF`

---

## 1. Daily assignment

**Rule:** Every nurse must be assigned at least one slot each day (a work shift or OFF).

```
a[n][d][DAY] + a[n][d][EVENING] + a[n][d][NIGHT] + a[n][d][OFF] >= 1
```

**Location:** `constraints()` loop, "At least one shift type must be assigned each day"

---

## 2. OFF exclusivity

**Rule:** If a nurse is OFF, they cannot also be assigned any work shift.

```
OFF[n][d] = 1  →  DAY[n][d] = 0
OFF[n][d] = 1  →  EVENING[n][d] = 0
OFF[n][d] = 1  →  NIGHT[n][d] = 0
```

Implemented with `AddImplication`.

**Location:** `constraints()` loop, "OFF is mutually exclusive with all work shifts"

---

## 3. Max shifts per day

**Rule:** A nurse can work at most `MAX_SHIFT_PER_DAY` work shifts in a single day (default: 2).

```
a[n][d][DAY] + a[n][d][EVENING] + a[n][d][NIGHT] <= MAX_SHIFT_PER_DAY
```

**Setting:** `self.MAX_SHIFT_PER_DAY = 2` in `initShiftSettings()`

**Location:** `constraints()` loop, "At most MAX_SHIFT_PER_DAY work shifts per day"

---

## 4. Illegal same-day combo — Night + Day

**Rule:** A nurse cannot work both Night and Day on the same day (always enforced, no flag).

```
a[n][d][NIGHT] + a[n][d][DAY] <= 1
```

**Location:** `constraints()` loop, "Illegal same-day combo: Night + Day"

---

## 5. Illegal consecutive-day combo — Evening → Night

**Rule:** A nurse cannot work Evening on day `d` and Night on day `d+1`.
Controlled by `ALLOW_SHIFT_E_N` — set to `True` to lift this restriction.

```
if not ALLOW_SHIFT_E_N:
    a[n][d][EVENING] + a[n][d+1][NIGHT] <= 1    for d in 0..D-2
```

**Setting:** `SettingLoader.allow_shift_e_n = False` (default)
Can be overridden per-run: `Solver(..., allow_shift_e_n=True)`

**Location:** `constraints()` loop, "E -> N"

---

## 6. Illegal consecutive-day combo — Night → Day

**Rule:** A nurse cannot work Night on day `d` and Day on day `d+1`.
Controlled by `ALLOW_SHIFT_N_D` — set to `True` to lift this restriction.

```
if not ALLOW_SHIFT_N_D:
    a[n][d][NIGHT] + a[n][d+1][DAY] <= 1    for d in 0..D-2
```

**Setting:** `SettingLoader.allow_shift_n_d = False` (default)
Can be overridden per-run: `Solver(..., allow_shift_n_d=True)`

**Location:** `constraints()` loop, "N -> D"

---

## 7. Requested holidays

**Rule:** If a nurse requests day `d` off, they must be assigned OFF on that day.

```
a[n][d][OFF] == 1    for each d in nurse[n].requested_holidays
```

Max holidays a nurse may request: `MAX_REQ_HOLIDAYS = 5` (excess is randomly sampled down).

**Location:** `handleHolidaysAndReq()`

---

## 8. Requested shifts

**Rule:** If a nurse requests a shift cell on day `d`, every shift in that cell must be assigned.
A double-shift cell is ONE request for TWO shifts: `ช/บ` = DAY + EVENING, `ด/บ` = NIGHT + EVENING.

```
a[n][d][s] == 1    for each (d, shifts) in nurse[n].requested_shifts, for each s in shifts
```

`requested_shifts` is `{day: [Shift, ...]}`. Max shift requests per nurse = tier `max_req_shifts`
(≤ `MAX_REQ_SHIFT = 5`), counted **per cell**; excess cells are randomly sampled down whole.
Soft mode (`min_request_percent < 100`): one `sat` var per cell — both shifts kept or dropped together.

**Skip-guards** (request dropped, not a contradiction): head/deputy (§16/§17) asking for any cell
on a weekend or any cell containing EVENING/NIGHT; a new nurse (§14) asking for `ด/บ`.

**Location:** `handleHolidaysAndReq()`

---

## 9. Minimum nurse coverage

**Rule:** Each work shift must have at least the required number of nurses every day.
Weekday and weekend minimums differ.

```
sum(a[n][d][s] for n in 0..N-1) >= min_shifts[d][s]
    for each d, for s in {DAY, EVENING, NIGHT}
```

**Minimums (configurable in `initShiftSettings`):**

| Shift   | Weekday | Weekend |
|---------|---------|---------|
| DAY     | 5       | 4       |
| EVENING | 3       | 2       |
| NIGHT   | 3       | 2       |

**Nurses excluded from a coverage pool.** The sum is taken over *eligible* nurses
only — a nurse may be working and still not count toward this ward's coverage:

| Excluded from | Who | Rule |
|---|---|---|
| DAY, that day | meeting attendees | §15 |
| the named shift, that day | off-ward duty (`flt` / `ref`) | §21 |

**Location:** `_constraintMinimumNurse()`

---

## 10. Head nurse (nurse index 0)

**Rule:** The head nurse (index 0) may only work Day shift or be OFF on weekdays,
and must always be OFF on weekends.

```
Weekday:  a[0][d][DAY] + a[0][d][OFF] == 1
          a[0][d][EVENING] == 0
          a[0][d][NIGHT]   == 0

Weekend:  a[0][d][OFF]     == 1
          a[0][d][DAY]     == 0
          a[0][d][EVENING] == 0
          a[0][d][NIGHT]   == 0
```

**Settings:**
- `HEAD_NURSE_INDEX = 0` — which nurse is the head nurse
- `HEAD_NURSE_SPEACIAL_SHIFT = True` — set to `False` to disable this entire block

**Location:** `optionHeadNurse()`

---

## 11. Objective — fair shift distribution

**Rule:** Minimize the largest difference between any two nurses' count of each shift type.
Applied to DAY, EVENING, and NIGHT independently; the worst imbalance across all three is used.

```
imbalance[s] = max(count[n][s]) - min(count[n][s])    for s in {DAY, EVENING, NIGHT}
overall_imbalance = max(imbalance[DAY], imbalance[EVENING], imbalance[NIGHT])
```

**Objective weight:** `10` (see constraint 13)

**Location:** `distribute()`

---

## 12. Objective — fair holiday distribution

**Rule:** Minimize the difference between the nurse with the most OFF days and the nurse with the fewest.

```
holiday_imbalance = max(count[n][OFF]) - min(count[n][OFF])
```

**Objective weight:** `10` (see constraint 13)

**Location:** `distribute()`

---

## 13. Objective — minimize double shifts

**Rule:** Minimize the total number of nurse-day pairs where a nurse works 2 shifts in one day.

```
double[n][d] = 1  if  DAY[n][d] + EVENING[n][d] + NIGHT[n][d] >= 2
total_double_shifts = sum(double[n][d])
```

**Objective weight:** `1` (see combined objective below)

**Location:** `constraints()` loop (tracking) and `distribute()` (objective)

---

## Combined objective

```
Minimize:
    10 * overall_imbalance
  + 10 * holiday_imbalance
  +  1 * total_double_shifts
```

Weights control priority. Increase a weight to make that term more important.
Shift/holiday fairness (10) is currently prioritised over double-shift avoidance (1).

**Location:** `distribute()` → `model.Minimize(...)`

---

## 14. New nurse — no Evening + Night double shift

**Original (Thai):** น้องใหม่ห้ามขึ้นเวรบ่าย ดึก ด้วยกัน

**Rule:** A nurse flagged as "new" cannot be assigned both Evening and Night on the same day.

```
if nurse n is new:
    a[n][d][EVENING] + a[n][d][NIGHT] <= 1    for all d
```

**How to flag a new nurse:** Add a `Type` column to the schedule CSV.
Set the cell to `new` (or `junior` / `น้องใหม่`). Leave blank or write `senior` otherwise.

```
Name,Type,1,2,...,31
NurseA,senior,...
NurseB,new,...
```

**Location:** `constraints()` loop, "New nurses cannot work Evening + Night as a double shift"

---

## 15. Meeting day — attendees excluded from Day coverage

**Original (Thai):** เวรเช้าถ้ามีประชุม ไม่นับคนที่ไปประชุมเป็นอัตรากำลัง

**Rule:** On a day where a nurse has a scheduled meeting, that nurse is assigned Day shift
but does not count toward the minimum Day coverage for that day.

```
a[n][d][DAY] == 1                                   (nurse attends on Day shift)

sum(a[n][d][DAY] for n NOT attending meeting) >= min_shifts[d][DAY]
                                                    (minimum met by non-meeting nurses only)
```

**How to mark a meeting:** Write `mtg` in the nurse's cell for that day in the CSV.

```
Name,Type,1,  2,  3,...
NurseA,senior,mtg,  ,  ,...   ← NurseA has a meeting on day 1
```

**Location:** `handleHolidaysAndReq()` (forcing DAY), `_constraintMinimumNurse()` (exclusion)

**Generalised by §21.** The coverage exclusion is no longer a Day-only special case:
`_constraintMinimumNurse()` subtracts one `excluded` set per `(day, shift)`, into which
meetings contribute on DAY and off-ward duties contribute on their own shift.

---

## 16. Head nurse and deputy — Day shift only on weekdays

**Original (Thai):** หัวหน้า และรองหัวหน้าลอยเช้า

**Rule:** Both the head nurse (index 0) and the deputy head nurse (index 1) may only
work Day shift or be OFF on weekdays. Evening and Night shifts are forbidden.

```
Weekday:  a[n][d][DAY] + a[n][d][OFF] == 1
          a[n][d][EVENING] == 0
          a[n][d][NIGHT]   == 0
    for n in {HEAD_NURSE_INDEX, DEPUTY_NURSE_INDEX}
```

**Settings:**
- `HEAD_NURSE_INDEX = 0`
- `DEPUTY_NURSE_INDEX = 1`
- `HEAD_NURSE_SPEACIAL_SHIFT = True` — set to `False` to disable this rule entirely

**Location:** `optionHeadNurse()`

---

## 17. Head nurse and deputy — always OFF on weekends

**Original (Thai):** หัวหน้า และรองหัวหน้าไม่ขึ้นเวรวันหยุด

**Rule:** Both the head nurse and deputy head nurse must be OFF on all weekend days.

```
Weekend:  a[n][d][OFF]     == 1
          a[n][d][DAY]     == 0
          a[n][d][EVENING] == 0
          a[n][d][NIGHT]   == 0
    for n in {HEAD_NURSE_INDEX, DEPUTY_NURSE_INDEX}
```

**Location:** `optionHeadNurse()` (same block as constraint 16)

---

## 18. Seniority — nurse index assignment

**Original (Thai):** ชื่อที่ 1 และ 2 จะต้องเป็นหัวหน้าและรองหัวหน้าเสมอ

**Rule:** The first nurse in the CSV (row 0) is always the head nurse;
the second nurse (row 1) is always the deputy head nurse.

```
HEAD_NURSE_INDEX    = 0   (row 0 in the CSV)
DEPUTY_NURSE_INDEX  = 1   (row 1 in the CSV)
```

This is enforced by `optionHeadNurse()` using these index constants.
To change who the head/deputy is, reorder rows in the CSV or update the index constants.

**Location:** `initShiftSettings()` constants

---

## 19. Seniority ordering (data convention)

**Original (Thai):** เรียงชื่อตามอายุงาน

**Rule:** Nurses in the CSV must be listed in descending order of seniority
(most senior first). The solver does not enforce this — it is a data entry convention.

Most senior → row 0 (head nurse), row 1 (deputy), …, least senior → last row.
Newly joined nurses ("new") should appear at the bottom.

**Location:** Input CSV only — no solver constraint.

---

## 20. Infeasibility diagnosis (not a constraint — an explanation pass)

**Rule:** No constraint is added to the real model. When a solve returns `INFEASIBLE`,
the solver rebuilds the *same* hard rules with each relaxable group reified behind one
CP-SAT **assumption literal**, then asks for a sufficient infeasibility core — the subset
of assumptions that already makes the model contradictory.

```
Pass 1 — requests gated:
  for each request cell (shift / holiday / vacation / meeting):
      lit                                             (fresh BoolVar)
      a[n][d][s] == 1  for s in cell   OnlyEnforceIf(lit)
  AddAssumptions(all lits);  Solve
  core = SufficientAssumptionsForInfeasibility()      ->  the conflicting requests

Pass 2 — only if pass 1's core is EMPTY (the hard structure alone is contradictory);
         requests go back to hard, coverage (§9) is gated instead:
      lit_cov[d][shift]
      sum(a[n][d][shift] for eligible n) >= min_shifts[d][shift]
                                           OnlyEnforceIf(lit_cov[d][shift])
  core                                                ->  the binding (day, shift) minimums
```

Reason resolution: non-empty pass-1 core → `requests`; empty pass-1 core + non-empty
pass-2 core → `coverage`; both empty → `structural` (the daily/transition/senior rules
contradict each other with no request or coverage involved); anything else, including a
timeout, → `undetermined`.

**The diagnosis build omits `distribute()`.** With an objective in the model CP-SAT returns
a *sufficient* but non-minimal core (verified on ortools 9.15), which would blame requests
that are not actually part of the conflict. Dropping the objective is a correctness
requirement here, not a speed optimisation.

**The diagnosis reuses the loaded instance, never a fresh `Solver`.** `DataImporter.sample`
is unseeded, so re-importing the CSV re-samples over-quota requests and the diagnosis would
name cells the failed run never applied. `diagnose()` therefore rebuilds `self.model` and
`self.assignments` in place — which destroys any solution, so it raises `RuntimeError` if
called on a solved `Solver`.

Request cells the solver itself skips (senior-conflicting §16/§17, new-nurse บ+ด §14) are
skipped in diagnosis mode too, so a request that was never applied is never blamed.
In soft mode (§8, `min_request_percent < 100`) the percentage floor is **not** added in
diagnosis mode: every request is forced by its own assumption, so the floor is implied.

**Flags:** none user-facing. Budget comes from `SOLVER_DIAGNOSIS_MAX_SECONDS` /
`SOLVER_RELAX_MAX_SECONDS`, clamped so a job can never exceed `WORKER_JOB_TIMEOUT_SECONDS`.

**Location:** `Solver.diagnose()` / `_diag_build()` / `_diag_gate()` / `_diag_solve()` /
`_diag_cap()` in `shiftwork.py`; the `_diag_mode` branches in `handleHolidaysAndReq()` and
`_constraintMinimumNurse()`. Naming and text rendering live outside the solver in
`webapp/core/diagnosis.py`.

> **Drift note (2026-10-17):** `webapp/core/relaxation.py` does **not** exist, yet
> `solver_runner.py` imports it and `worker/tasks.py` passes a non-zero
> `relax_seconds`. The `except Exception` around the import swallows the
> `ImportError`, so the relaxation ladder never runs and no relaxation suggestions
> ever reach the UI. Tracked as tech debt; not fixed by the §21 change.

---

## 21. Off-ward duty — float (`flt`) and refer (`ref`)

**Original (customer):** บางวอร์ดต้องลอยไปช่วยหน่วยอื่น ไม่นับเป็นอัตรากำลังของวอร์ดเรา
แต่นับเป็นเวรของพยาบาลคนนั้น และต้องแสดงในตาราง · "refer" = ไปส่งผู้ป่วย

**Rule:** A nurse may be assigned an **off-ward duty** on one shift of one day:
`flt` (lent to another unit) or `ref` (accompanying a patient transfer). The two are
ONE mechanism and differ only in label. The duty is **hard and never searched** — the
head nurse writes it into the request grid and the solver always honours it; the
solver never chooses who goes.

```
for an off-ward cell (n, d, shift s, kind k):

  a[n][d][s]  == 1                                  (works exactly that shift)
  a[n][d][s'] == 0   for every s' != s, incl. OFF   (and nothing else that day)

  sum(a[n][d][s] for n NOT off-ward on (d, s)) >= min_shifts[d][s]
                                                    (§9: she does not cover our ward)
```

**She still counts as having worked that shift.** No objective term is added. The
fairness objective (§11–§13) sums the very same `a[n][d][s]` variable that the duty
forces to 1, so the shift already enters shift-type balance and already keeps the day
out of the OFF count (§12). Coverage and fairness read one variable; only the coverage
**pool** drops her. A floated night is a worked night — which is the requirement.

**How to mark an off-ward duty:** write `flt:<shift>` or `ref:<shift>` in the cell.

```
Name,Type,1,     2,     3,...
NurseA,,  flt:ด, ,      ref:ช,...   ← night float on day 1, refer duty on day 3
```

Valid tokens: `flt:ช` `flt:บ` `flt:ด` `ref:ช` `ref:บ` `ref:ด`. A bare `flt` or `ref`
is **rejected** with a message naming the valid forms — silently defaulting to Day
would produce a wrong schedule the head nurse would not notice.

**Exclusivity.** An off-ward duty occupies one shift *slot* (it counts as one shift
rather than blanking the day the way `vac` does), but it is the nurse's only work
shift that day. This is deliberate: it removes an output ambiguity with the double-shift
symbols `ช/บ` / `ด/บ`, which have no way to say "the night half was off-ward".

**Not a request.** Off-ward cells are never down-sampled against the tier's
`max_req_shifts` / `max_holidays` quotas, and are not tier-gated. They are management
facts, like `mtg` and `vac`, not preferences.

**Skip-guard (known failure mode 1).** §16/§17 pin head (0) and deputy (1) to
Day-or-OFF on weekdays and OFF at weekends. An off-ward cell on nurse 0 or 1 that
falls on a weekend/holiday, or asks for Evening or Night, would therefore make the
model infeasible, so it is **dropped** with a printed
`[Info] Nurse n flt:night on day D skipped (conflicts with senior nurse rule).`
`webapp/core/presolve.py` raises the matching `senior_offward` **warning** before the
solve so the user learns the cell will be ignored rather than wondering why.
Only cells that survive the skip-guard enter `_offward_applied`, and the coverage
exclusion and the output rendering both read that map — so a dropped cell can never
shrink a coverage pool.

**Rendering.** `_write_schedule()` writes the same token back (`flt:ด`), ahead of the
ordinary symbol cascade, so a solved schedule round-trips into the grid editor.
`webapp/core/fairness.py` counts the cell as a shift of its type and reports
`offward_days` separately.

**Interaction with §11 (accepted, not fixed).** Because the duty is forced, it is load
the objective cannot move: a nurse with many off-ward nights pulls the night-imbalance
term toward giving everyone else the same count, which can inflate total ward work.
This is identical in kind to the existing forced-`mtg` effect on Day balance and is
accepted.

**Interaction with §20.** In diagnosis mode the forced assignment is gated behind an
assumption literal (kind `flt`/`ref`, so the core names it), while the coverage
exclusion stays unconditional — matching the real model. A contradiction caused by the
*forced shift* therefore surfaces in pass 1, and one caused by the *reduced pool* in
pass 2 as a coverage item.

**Flags:** none. Available on every tier.

**Location:** `constant.py` (`OffWard`, `OFFWARD_TOKENS`, `parse_offward()`);
`DataImporter.transform()` → `reqOffward`; `Solver.offward_days` /
`_offward_applied` / `_offward_excluded` built in `loadReqShifts()` and
`handleHolidaysAndReq()`; enforced in `handleHolidaysAndReq()`; excluded in
`_constraintMinimumNurse()`; hinted in `_build_warm_start()`; rendered in
`_write_schedule()`.
