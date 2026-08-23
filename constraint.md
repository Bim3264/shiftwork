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

**Rule:** If a nurse requests a specific shift on day `d`, that shift must be assigned.

```
a[n][d][req_shift] == 1    for each (d, req_shift) in nurse[n].requested_shifts
```

Max shift requests per nurse: `MAX_REQ_SHIFT = 5` (excess is randomly sampled down).

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
