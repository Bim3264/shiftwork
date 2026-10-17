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

**Skip-guards** (request dropped, not a contradiction): senior nurses (§16/§17) asking for any cell
on a weekend or any cell containing EVENING/NIGHT; a new nurse (§14) asking for `ด/บ`.

**What is relaxable — order of hardness: shifts < off < vacation.**

| Cell | Relaxable when |
|---|---|
| shift (`ช` `บ` `ด` `ช/บ` `ด/บ` `no:X`) | always, in soft mode |
| `off` (requested day off) | `relax_days_off` (**on by default**) |
| `vac` (approved leave) | `not enforce_vacation` **and** `relax_days_off` |

An ordinary requested day off is meant to be the easy thing to give up when a
month is tight, and approved leave the hardest. The `and relax_days_off` on the
vacation row is what enforces that: without it a `vac` dropped while an `off` on
the same ward did not, so leave was *easier* to lose than a day off — backwards,
and the opposite of what the two checkboxes appear to promise.

**Acceptance floor (soft mode).** At least `min_request_percent` of the relaxable
requests must be satisfied:

```
sum(sat) >= floor,   floor = ceil(pct/100 * total)
                     floor = min(floor, total - 1)   when pct < 100
```

The second line matters. A bare `ceil` rounds a *small* set up to "keep
everything" — at 80% a 2-request group gets `ceil(1.6) = 2`, so nothing can ever
be dropped and the setting silently does nothing. That bites hardest since RN and
PN/NA became separate solves (see the staff-group split in `CODEBASE.md`), because
each group's requests are now counted on their own and the smaller group loses all
its slack. Below 100% the user has
explicitly asked for relaxation, so at least one drop is always possible; at 100%
the cap does not apply and every request stays hard.

**Per staff group.** The threshold is set per group: `min_request_percent_rn` and
`min_request_percent_pnna` (0..100), because RN and PN/NA are independent solves and the
floor above is already computed per solve. `ScheduleInput.to_solver_csv(group=...)` writes the
group's value under the legacy name `min_request_percent`, so `shiftwork.py` / `DataImporter`
never see the group keys. A group key absent from a saved input inherits the legacy
`min_request_percent` (`_coerce_settings`), so every existing input behaves identically; an
ungrouped ward uses the legacy key alone. Presolve's `soft` flag follows the same per-group
value (`min_request_percent_for(group)`). `relax_days_off` and `enforce_vacation` stay ward-wide.

**Location:** `handleHolidaysAndReq()`

---

## 9. Minimum nurse coverage

**Rule:** Each work shift must have at least the required number of nurses every day.
Weekday, weekend and special-holiday minimums differ (three bands — see §22).

```
sum(a[n][d][s] for n in 0..N-1) >= min_shifts[d][s]
    for each d, for s in {DAY, EVENING, NIGHT}
```

**Minimums (configurable in `initShiftSettings`, overridden per file):**

| Shift   | Weekday | Weekend | Holiday |
|---------|---------|---------|---------|
| DAY     | 5       | 4       | 4       |
| EVENING | 3       | 2       | 2       |
| NIGHT   | 3       | 2       | 2       |

The holiday column **defaults to the weekend column** and is resolved per day by
`_rebuild_days()` with precedence `holiday > weekend > weekday` (§22).

**Nurses excluded from a coverage pool.** The sum is taken over *eligible* nurses
only — a nurse may be working and still not count toward this ward's coverage:

| Excluded from | Who | Rule |
|---|---|---|
| DAY, that day | meeting attendees | §15 |
| the named shift, that day | off-ward duty (`flt` / `ref`) | §21 |

**Location:** `_constraintMinimumNurse()`

---

## 10. Senior nurse (Type column)

**Rule:** Every nurse typed `senior` (see §18) may only work Day shift or be OFF on weekdays,
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
- `senior_nurse_indices` — rows typed senior in the Type column (any number, zero = rule is a no-op)
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

**Rule:** Every senior nurse (Type column, §18; any number) may only
work Day shift or be OFF on weekdays. Evening and Night shifts are forbidden.

```
Weekday:  a[n][d][DAY] + a[n][d][OFF] == 1
          a[n][d][EVENING] == 0
          a[n][d][NIGHT]   == 0
    for n in senior_nurse_indices
```

**Settings:**
- `senior_nurse_indices` (from the Type column)
- `HEAD_NURSE_SPEACIAL_SHIFT = True` — set to `False` to disable this rule entirely

**Location:** `optionHeadNurse()`

---

## 17. Head nurse and deputy — always OFF on weekends

**Original (Thai):** หัวหน้า และรองหัวหน้าไม่ขึ้นเวรวันหยุด

**Rule:** Every senior nurse must be OFF on all weekend days.

```
Weekend:  a[n][d][OFF]     == 1
          a[n][d][DAY]     == 0
          a[n][d][EVENING] == 0
          a[n][d][NIGHT]   == 0
    for n in senior_nurse_indices
```

**Location:** `optionHeadNurse()` (same block as constraint 16)

---

## 18. Seniority — from the Type column

**Rule:** Seniority is NOT positional. A nurse is senior iff the `Type` column is
`senior`, `อาวุโส` or `ซีเนียร์` (case-insensitive; same mechanism as the `new`
flags `new`/`junior`/`น้องใหม่`). Any number of seniors works: zero, one, five.
No senior typed = §10/§16/§17 do nothing. There is no minimum nurse count.
The rule applies in every staff group (a senior PN is constrained like a senior RN).

**Location:** `DataImporter.transform()` fills `seniorNurseIndices`; `Solver`
copies it to `senior_nurse_indices`. Mirrored in `presolve._SENIOR_FLAGS` and
`app._fairness_for_job` (the web layer reads `type` from the stored input).

---

## 19. Seniority ordering (data convention)

**Original (Thai):** เรียงชื่อตามอายุงาน

**Rule:** Nurses in the CSV must be listed in descending order of seniority
(most senior first). The solver does not enforce this — it is a data entry convention.

Most senior first, …, least senior last (display convention only; the senior rule follows the Type column, not the position).
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

Each pass-2 core item is `{day (1-based), shift, required, band}`. `band` is
`weekday`/`weekend`/`holiday` (§22), so an infeasible **holiday** floor is nameable
rather than reading as an ordinary day; `webapp/core/diagnosis.py` renders it as
`day 13 (holiday): needs 4 ช` and omits the parenthetical for `weekday`. No extra
gating pass is needed for the third band: `_rebuild_days()` has already resolved the
band into `self.days[d]['min_shifts']` before `_constraintMinimumNurse()` runs.

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

**Skip-guard (known failure mode 1).** §16/§17 pin every senior nurse to
Day-or-OFF on weekdays and OFF at weekends. An off-ward cell on a senior nurse that
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

---

## 22. Special-holiday coverage band

**Original (customer):** วันหยุดนักขัตฤกษ์อัตรากำลังไม่เหมือนเสาร์-อาทิตย์ ต้องตั้งแยกได้
("a public holiday is not staffed like a Saturday — we need to set it separately").

**Rule:** A day belongs to exactly one **coverage band**, which supplies the §9
minimums for that day. There are five bands and the precedence is strict:

```
band(d) = 'holiday'  if d in holidays
          'sunday'   elif d in sundays
          'saturday' elif d in saturdays
          'weekend'  elif d in weekends
          'weekday'  otherwise
```

A public holiday that falls on a Saturday is in both sets, and `holiday` wins.

**Saturday / Sunday bands (follow-on, same pattern).** Wards asked to staff Saturday and
Sunday differently, so `saturdays` and `sundays` are two more 1-based day lists that pick
the `saturday` / `sunday` band and **nothing else**. `weekends` stays the non-working-day
union: `Solver.weekends = weekends ∪ holidays ∪ saturdays ∪ sundays`, so §16/§17, the §21
skip-guard, warm start, presolve and fairness are untouched. `'weekend'` remains the band
for a rest day that is in no list (a ward that typed its rest days by hand has no weekday
information). Backwards compatible: absent `saturdays`/`sundays` default to `[]`, so every
existing weekend day keeps the weekend band, and the new `coverage_{saturday,sunday}_*`
keys inherit the weekend numbers exactly like the holiday keys do. Legacy (ungrouped)
`coverage_saturday_*` / `coverage_sunday_*` keys **exist** (an ungrouped ward edits the
legacy keys on the edit page), inheriting `coverage_weekend_*`. `calendar_util.month_dates`
returns `saturdays` / `sundays`; auto-fill and "next month" write them. Code:
`shiftwork.py:_rebuild_days`, `dataimporter.py`, `schedule_input._coerce_settings`,
`presolve._day_band`. Tests: `test_shiftwork.SaturdaySundayBandTest`,
`test_schedule_input.SaturdaySundayBandSettingsTest`, `test_presolve` (saturday/sunday).

**The holiday band defaults to the weekend band.** This is the whole point of the
design, not a convenience: it makes the split behaviour-preserving. Resolution order,
first hit wins:

| Key | Falls back to |
|---|---|
| `coverage_holiday_{shift}` | `coverage_weekend_{shift}` (from the same file) → 4/2/2 |
| `coverage_{rn,pnna}_holiday_{shift}` | that group's `_weekend_` → legacy `coverage_holiday_` → legacy `coverage_weekend_` → 4/2/2 |
| `coverage_{saturday,sunday}_{shift}` | `coverage_weekend_{shift}` (from the same file) → 4/2/2 |
| `coverage_{rn,pnna}_{saturday,sunday}_{shift}` | that group's `_weekend_` → legacy `coverage_{saturday,sunday}_` → legacy `coverage_weekend_` → 4/2/2 |

**`weekends` keeps its old meaning everywhere except the band.** Before this section,
the month auto-fill stored `weekends ∪ public holidays ∪ extra_holidays` into
`settings["weekends"]`, so a holiday was indistinguishable from a Sunday. The split
moves holidays into their own list for **band selection only**. `Solver.weekends` is
therefore set to the **union** of the two lists, so every rule that treats a day as
non-working sees exactly what it saw before:

| Consumer | Reads |
|---|---|
| `_rebuild_days()` band selection | `self.holidays`, `self.sundays`, `self.saturdays`, then `self.weekends` |
| §16 / §17 senior nurse rules | `self.weekends` (the union) |
| §21 off-ward senior skip-guard | `self.weekends` (the union) |
| `_build_warm_start()` | `self.weekends` (the union) |
| `presolve` senior rules, `_explain_day` | union; `presolve._day_band` splits |
| `fairness_stats` weekend columns | union (the caller passes it) |

**Where the three bands can be set.** All three bands, and their per-staff-group
halves, are now reachable from **both** input paths:

| Path | Holiday list | Holiday band | PN/NA bands |
|---|---|---|---|
| Web edit page | holidays input + auto-fill | coverage table, holiday row | PN/NA columns (shown once a nurse is classed) |
| CSV (`[settings]`) | `holidays,` | `coverage_holiday_*` | `coverage_pnna_holiday_*` |
| Excel template | `Special holidays` card row | `Holiday need D / E / N` | `PN/NA holiday need D / E / N` |
| Saturday / Sunday: web | `saturdays` / `sundays` inputs + auto-fill | coverage table, Saturday / Sunday rows | same rows per group |
| Saturday / Sunday: CSV | `saturdays,` / `sundays,` | `coverage_{saturday,sunday}_*` | `coverage_{rn,pnna}_{saturday,sunday}_*` |
| Saturday / Sunday: Excel | `Saturdays` / `Sundays` card rows | `Saturday need` / `Sunday need` | `PN/NA saturday need` / `PN/NA sunday need` |

**Edit-page layout (regression).** The coverage control is ONE table with the three shift
symbols (ช บ ด) as its only numeric columns and band (and, for a grouped ward, a
group sub-header row) going down the rows. A band-per-column layout reached 527px and
overflowed its 399px `settings-grid` column, hiding the PN/NA inputs. Guarded by
`test_web.CoverageBandsEditTests.test_coverage_table_stays_three_numeric_columns`.

The Excel card is keyed by label text through `ScheduleInput._label_key`, which
strips a `/`-delimited segment only when it contains Thai characters — a plain
`split("/")[0]` truncated `PN/NA weekday need` to `pn` and silently discarded the
per-group rows (CODEBASE.md known failure mode 13). An **absent** card row writes no
settings key, which is what preserves the fallback chain above; the shipped workbooks
are generated by `tools/gen_templates.py` and the parity is held by
`webapp/tests/test_excel_templates.py`.

> **Naming caveat.** `Solver.weekends` means "weekend **or** holiday". The name is kept
> because renaming it would touch four correct call sites inside the most delicate
> rules for no behavioural gain. A future rule that needs calendar weekends only must
> use `self.weekends - self.holidays`.

**Already-saved months are not migrated and must not be.** An existing input has its
holidays buried inside `weekends` with no record of which days they were, and they are
not recoverable. An absent `holidays` key defaults to `[]`, so every one of those days
keeps the **weekend** band and the month solves exactly as before. The lists separate
only when the user presses auto-fill again, and because the bands are equal by default
that re-run changes staffing by nothing. Re-deriving the holidays from the `holidays`
library at read time was rejected: the Thai calendar changes between library releases,
so the reclassification would depend on an installed package version rather than on
what the user saw when they pressed the button, and undated inputs cannot be derived
at all.

**No new variables and no new constraints.** The band changes an integer on the
right-hand side of the §9 constraint that already exists. A too-high holiday floor is
not a silent infeasibility: it surfaces as presolve's existing `coverage_day` error
and, after a failed solve, as a §20 `coverage` diagnosis item carrying
`band: 'holiday'`.

**Flags / settings:** `holidays` (1-based in the file and in `ScheduleInput.settings`,
0-based in `Solver`; **empty means empty**, unlike `weekends` whose empty value means
"the default weekends"). Minimums: `coverage_holiday_{day,evening,night}` and
`coverage_{rn,pnna}_holiday_{day,evening,night}`. Written by
`POST /inputs/{id}/autofill` from `calendar_util.month_dates(...)["holiday_days"]`
(Thai public holidays ∪ `extra_holidays`); editable as the Holiday row of the coverage
table and the "holidays" field on the edit page.

**Location:** `Solver.holidays` + the union assignment in `loadReqShifts()`; band
precedence in `_rebuild_days()`; `'holiday'` defaults in `initShiftSettings()`; band
label in `_constraintMinimumNurse()`'s diagnosis branch.
`DataImporter._parse_holidays()` and the `coverage_holiday_*` inheritance in its
settings dict; `ScheduleInput._DEFAULT_SETTINGS` / `_COVERAGE_BANDS` /
`_coerce_settings` inheritance chain / `to_solver_csv()`;
`calendar_util.month_dates()["holiday_days"]`; `presolve._day_band()` / `_band()`;
`webapp/core/diagnosis.py` band rendering; `duplicate.next_month_input()`;
`edit_input.html` coverage table.

---

## 23. Negative shift request — `no:X` ("anything except X")

**Original (customer):** ขอไม่บ่าย 24 · "please don't give me Evening on the 24th". The
nurse accepts Day, Night or a day off; she only rules one shift out.

**Rule:** a request cell `no:ช` / `no:บ` / `no:ด` forbids that one shift on that day.
Pure negation — it is **not** "must work something else": OFF stays legal. It is an
ordinary *request*: hard when `min_request_percent == 100`, droppable below that,
counted in the acceptance report, nameable by `Solver.diagnose()`, and it consumes
the per-nurse request quota (`max_req_shifts`) together with the positive cells.

```
for a negation cell (n, d, shift s):
  a[n][d][s] == 0                      (hard)
  a[n][d][s] == 0  OnlyEnforceIf(sat)  (soft; one `sat` per cell, §8 floor applies)
```

`ช/บ` and `ด/บ` both set the Evening variable, so `no:บ` rules them out with no extra
code. `no:off` / bare `no` are not tokens (rejected by `validate()`); "must work" is a
different request and is deliberately not offered.

**Skip-guard: none needed.** One cell holds one token, so a negation can never share a
day with `mtg`, an off-ward cell, `off` or `vac`. The senior rule (§16/§17) leaves
Day-or-OFF on weekdays, so `no:ช` for a senior just means OFF — feasible. The only
way a negation can make the model infeasible is coverage (everyone negating a shift
that is needed), which presolve reports as `coverage_day` and, in soft mode, the
request floor lets the solver drop.

**Location:** `constant.py` `NEGATE_KIND`/`NEGATE_TOKENS`/`parse_negation()`;
`DataImporter.transform()` → `reqNegShifts` (`{day: Shift}` per nurse; sampled in the
same pool as `reqShifts`); `Solver.loadReqShifts()` → `nurse['requested_negations']`;
`Solver.handleHolidaysAndReq()` calls the shared `_add_request(..., negate=True)`
(kind `'avoid'`) — `_diag_gate(..., negate)` and `_request_label()` give diagnosis and
the report the `no:บ` label; `_build_warm_start` never hints a negated shift.
Web: `schedule_input.VALID_CELL_TOKENS` / `_normalize_token` (`NO:บ` → `no:บ`);
`presolve.py` (`_NurseDay.forbidden`, `_feasible`, `_explain_day`);
`edit_input.html` legend + `cell--neg` tint; `tools/gen_templates.py` dropdown
(`negate_tokens()`), conditional format, legend cell, Guide row.


## 24. Fatigue rules — max consecutive nights, no consecutive ช/บ

**Original (customer):** "max consecutive night shift (ด) = 3" and "no consecutive ช/บ".
Two **opt-in** hard rules; both default OFF so existing wards are unchanged.

**(a) Max consecutive nights** — `limit_consecutive_nights` (bool, false) with
`max_consecutive_nights` (int ≥ 1, default 3). Sliding window: for every nurse and every
window of `k+1` consecutive days, `sum(Night) <= k`. No-op when `k >= num_days`.

**(b) No consecutive ช/บ** — `no_consecutive_day_eve` (bool, false). A nurse works at most
two shifts a day, so "ช/บ on d" is exactly `Day_d + Eve_d == 2`; forbidding it on `d` and
`d+1` is the linear constraint `Day_d + Eve_d + Day_{d+1} + Eve_{d+1} <= 3`. It adds **no
variables** and deliberately does not use `double_shift_vars` (that means "any 2 shifts",
ด/บ included, and feeds the objective). **ด/บ is not covered**; with the default E→N ban
(§5) two adjacent ด/บ days are already impossible anyway.

**Conflict with requests:** a request cell is hard, so `ด` x4 at limit 3 or ช/บ on two
adjacent days is a silent "No solution found". `Solver.diagnose()` (§20) rebuilds through
`constraints()`, gates every request, and the unsat core names exactly those cells
(tested: reason `requests`, the 4 night cells / the 2 ช/บ cells). Presolve mirrors both
as errors `request_consecutive_nights` / `request_consecutive_day_eve`, and the coverage
pair check models rule (b) (and (a) when k = 1).

**Location:** `shiftwork.py` `Solver._add_fatigue_rules()` (called at the end of
`_add_shift_transition_rules()`); flags `LIMIT_CONSECUTIVE_NIGHTS`,
`MAX_CONSECUTIVE_NIGHTS`, `NO_CONSECUTIVE_DAY_EVE` set in `loadReqShifts`;
`dataimporter.py` settings dict; `webapp/core/presolve.py` §3 block + `_feasible`.
Tests: `test_shiftwork.FatigueRulesTest`, `webapp/tests/test_fatigue_rules.py`.
