# ShiftWork — Performance Development Log

Tracks every performance-motivated change: what, why, where, and notes for future work.
Add a new entry for each optimization pass or regression fix.

---

## Pass 1 — 2026-06-16

**Baseline:** No solver hints; hardcoded 8 workers; 5× CSV parse passes per nurse;
`importlib.reload` on every GUI run; `AddMaxEquality` in objective; redundant constraints
for senior nurses on weekends.

---

### A1 — Dynamic `num_search_workers`

**File:** `shiftwork.py` → `main()`

**Change:**
```python
# before
self.solver.parameters.num_search_workers = 8
# after
self.solver.parameters.num_search_workers = os.cpu_count() or 8
```

**Why:** Hardcoded 8 under-uses machines with more cores, and over-subscribes machines
with fewer. `os.cpu_count()` gives CP-SAT the actual number of logical CPUs available,
maximising parallel search throughput at no code cost.

**Expected impact:** Free speedup on any machine that isn't exactly 8-core.

**Notes:** If this is ever run inside a container with CPU limits, `os.cpu_count()` may
return the host count, not the container quota. Use `len(os.sched_getaffinity(0))` on
Linux if container awareness is needed.

---

### A2 — Greedy warm-start hint

**File:** `shiftwork.py` → new method `_build_warm_start()`, called from `main()`

**Change:** Added `model.AddHint()` for every BoolVar before `solver.Solve()`.

**Strategy:** senior nurses follow their role rules; nurses with explicit requests honour
them; all others cycle round-robin `(DAY, EVENING, NIGHT, OFF)[(nurse_idx + day_idx) % 4]`.

**Why:** CP-SAT starts from zero with no knowledge of a feasible region. A plausible
hint lets the LNS (Large Neighbourhood Search) phase start from a near-feasible point
instead of constructing a solution from scratch. For scheduling problems this typically
cuts time-to-first-feasible from tens of seconds to under a second.

**Expected impact:** Largest single win. Most visible when `max_time_in_seconds` budget
is tight or the ward has many nurses with dense shift requests.

**Notes:**
- Hints don't need to be constraint-feasible; CP-SAT repairs violations automatically.
- The round-robin distributes DAY/EVE/NIGHT/OFF at ratio 1:1:1:1 across nurses. For a
  typical 20-nurse ward that yields ~5 per shift type per day, which oversatisfies
  coverage minimums (5 DAY, 3 EVE, 3 NIGHT required) — so the hint is close to feasible.
- If future constraints make the hint systematically infeasible (e.g. hard max-OFF rules),
  revisit the cycle to bias more toward work shifts.

---

### A3 — Linear sum objective (replaces `AddMaxEquality`)

**File:** `shiftwork.py` → `distribute()`

**Change:**
```python
# before — introduces a nonlinear max variable
overall_imbalance = self.model.NewIntVar(0, self.num_days, 'overall_imb')
self.model.AddMaxEquality(overall_imbalance, imbalance_vars)

# after — purely linear; no auxiliary IntVar
overall_imbalance = cp_model.LinearExpr.Sum(imbalance_vars)
```

**Why:** `AddMaxEquality` injects a nonlinear term into the objective, which forces
CP-SAT to handle it specially in the LP relaxation (via extra binary variables or big-M
linearisation). A plain linear sum gives CP-SAT tighter dual bounds immediately, which
prunes the search tree faster.

**Semantic change:** The old objective penalised only the *worst* shift-type imbalance;
the new one penalises the *total* cross-shift imbalance. In practice both drive the solver
toward uniform distribution but the new form also penalises having two shift types with
moderate imbalance, which is arguably better for fairness.

**Expected impact:** Medium. Helps most when the LP relaxation is the bottleneck (common
for tightly constrained wards). Little effect if CP-SAT is already spending most time in
tree search.

**Notes:** If the old "minimise worst imbalance" semantics are preferred, restore:
```python
overall_imbalance = self.model.NewIntVar(0, self.num_days, 'overall_imb')
self.model.AddMaxEquality(overall_imbalance, imbalance_vars)
```

---

### A4 — Solver progress logging flag (gated)

**File:** `shiftwork.py` → `main()`

**Change:** Added commented-out line:
```python
# self.solver.parameters.log_search_progress = True
```

**Why:** Enables per-iteration CP-SAT output (objective bound, gap, worker activity)
without permanently polluting the log. Uncomment when tuning or diagnosing slow solves.

---

### B1 — Remove redundant weekend constraints for senior nurses

**File:** `shiftwork.py` → `optionHeadNurse()`

**Change:** Weekend block for senior nurses reduced from 4 constraints to 1:
```python
# before — 4 constraints
self.model.Add(self.assignments[n][d][Shift.OFF] == 1)
self.model.Add(self.assignments[n][d][Shift.DAY] == 0)      # redundant
self.model.Add(self.assignments[n][d][Shift.EVENING] == 0)  # redundant
self.model.Add(self.assignments[n][d][Shift.NIGHT] == 0)    # redundant

# after — 1 constraint; implication chain handles the rest
self.model.Add(self.assignments[n][d][Shift.OFF] == 1)
```

**Why:** `constraints()` already adds `AddImplication(off_v, day_v.Not())` etc. for every
(nurse, day). When `OFF == 1` is forced, CP-SAT presolve propagates those implications
immediately. The three `== 0` constraints are redundant but still consume presolve budget.

**Scope:** 2 senior nurses × 8 weekend days = 16 (n,d) pairs → 48 constraints removed.

**Expected impact:** Small but free. Slightly faster presolve; smaller model footprint.

**Notes:** This change is safe only because the `AddImplication` constraints are always
added in `constraints()`. If those implications are ever removed, restore the explicit
`== 0` constraints here.

---

### B2 — Precompute `_meeting_nurses_per_day`

**File:** `shiftwork.py` → `loadReqShifts()` (build), `_constraintMinimumNurse()` (use)

**Change:**
```python
# before — rebuilt every day iteration inside _constraintMinimumNurse()
meeting_nurses_today = {n for n, days in self.meeting_days.items() if d in days}

# after — built once in loadReqShifts(), O(1) lookup in loop
self._meeting_nurses_per_day.get(d, set())
```

**Why:** The old form scanned the entire `meeting_days` dict on every day iteration,
giving O(D × M) work where D = days and M = nurses with meetings. The precomputed reverse
map turns each lookup to O(1).

**Expected impact:** Negligible on typical wards (few meeting nurses). Worth having for
correctness and as prep for larger datasets.

---

### C1/C2 — DataImporter single-pass per nurse row

**File:** `dataimporter.py` → `DataImporter.transform()`

**Change:** Replaced 5 separate list comprehensions (each iterating `day_cols` and
calling `clean()`) with a single `for col in day_cols` loop that builds all five lists
in one pass.

**Why:** Each comprehension called `clean()` independently on the same cell, so a row
with D day columns incurred 5×D `clean()` calls. The single pass does 1×D `clean()`
calls and dispatches into the correct list via a short if/elif chain.

**Cut:** For 20 nurses × 31 days: ~3,100 → ~620 `clean()` calls (5× reduction).

**Notes:**
- A cell can legitimately match both `day_shift` and `evening_shift` (value `"ช/บ"`
  = DAY_EVENING). The single-pass uses separate `if` (not `elif`) for those two, so
  both lists are populated correctly.
- `"off"` and `"mtg"` are matched with `val.lower()` to handle case variations.

---

### D2 — `DEV_MODE` gate for `importlib.reload`

**File:** `gui.py`

**Change:** Added `DEV_MODE = False` constant; gated `importlib.reload(_sw)` behind it.

**Why:** `importlib.reload` reloads and re-initialises the shiftwork module on every
solver run. In production this is pure overhead; it was added to pick up live file edits
during development.

**How to enable:** Set `DEV_MODE = True` at the top of `gui.py` when actively editing
`shiftwork.py` and needing live reload.

---

### D3 — Sort `FileLoader.files`

**File:** `dataimporter.py` → `FileLoader.__init__()`

**Change:** Added `self.files.sort()` after `os.scandir` loop.

**Why:** `os.scandir` returns entries in filesystem order, which is non-deterministic
across OS and filesystem. Sorting guarantees the same processing order every run,
making output diffs meaningful and test results reproducible.

---

## Deferred / Future Work

| ID | Description | Reason deferred |
|----|-------------|-----------------|
| D1 | Parallel file solving via `multiprocessing` | OR-Tools already parallelises within each solve via `num_search_workers`. Running N solvers concurrently would over-subscribe CPUs and likely hurt individual solve times. Revisit if typical input grows to many small files (e.g., >10 wards). |
| A5 | Symmetry breaking constraints | Most nurse symmetry is already broken by individualised shift requests and seniority rules. CP-SAT's internal symmetry detector handles the rest. Revisit if a future problem variant has many identical nurses. |
| A6 | Adaptive time limit | `max_time_in_seconds = 120` is a fixed ceiling. Could auto-extend if no feasible solution found, or reduce if solution found quickly. Needs a `SolutionCallback` to implement cleanly. |
| A7 | `assignments` flat array | Replacing `assignments[n][d][s]` dict-of-dict-of-dict with a flat list reduces Python dict overhead in constraint loops. Estimated gain: <5% model build time. Low priority. |
