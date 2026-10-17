# Infeasibility diagnosis + pre-run relaxations (FEATURE-GAPS items 8 & 11)
Status: draft · Size: L · Date: 2026-10-16 · Owner: lead-engineer

Scope split, decided up front:

| Track | Wireframe | Verdict |
|---|---|---|
| A. Infeasibility diagnosis (unsat core) | `1s`, `1u` | **BUILD** — additive, runs only after INFEASIBLE |
| B. Relaxation suggestions ("drop these 2 and it solves") | `1t` | **BUILD** — additive, same job, bounded budget |
| C. Persisted diagnosis + first-class UI | `1s`, `1t` | **ESCALATE** — needs a `SolveJob` column, `create_all` can't add it |
| D. Best-so-far streaming | `1o` | **ESCALATE / DEFER** — DB + worker lifecycle change, and useless without `1m`/`1n` |

## 1. Objective & interpretation

Today an infeasible solve tells the head nurse nothing actionable. After this change, an
INFEASIBLE job's log names **either** the specific request cells that conflict (nurse index,
day, requested shift) **or** the specific day+shift whose coverage minimum cannot be met, and
then lists which single relaxation would make the month solvable ("allow Night→Day", "drop
these 3 requests", "lower weekday night coverage to 2").

Interpretation / assumptions:
- The diagnosis runs **only** when the main solve returned `INFEASIBLE`. A solved or
  timed-out job is untouched. `UNKNOWN` (timeout) is not a proven contradiction, so there is
  nothing to diagnose — leave that branch alone.
- `solver_status`, `solved`, `grid`, `csv_text` and the worker's outcome mapping are
  **unchanged**. The only observable differences on an infeasible run are extra text in
  `job.log` and two new optional fields on `SolveResult`.
- Diagnosis + relaxation share ONE wall-clock budget, clamped so a job can never exceed
  `WORKER_JOB_TIMEOUT_SECONDS`. No new config invariant assertion (see §4, rejected alt.).
- The diagnosis **must not** re-state the solver's hard rules in a second place. It reuses
  `shiftwork.Solver.constraints()` itself (Known failure mode 9). This is the main
  architectural constraint on the design.

**Challenge to the roadmap framing.** Item 11 calls these "*pre*-run relaxations". Running N
relaxation trials *before* every solve costs N solves on jobs that would have succeeded
anyway, and `presolve.check` already blocks the provably-broken inputs. The value lands
entirely in the infeasible case, so this spec runs relaxation trials **after** an INFEASIBLE
verdict, inside the same job, where the main solve has already returned early and left budget
on the table. If Bim wants a genuine pre-run "what if" explorer from the edit page, that is a
separate feature (new endpoint, its own job kind) and should not be bundled here.

## 2. Current state

- `shiftwork.py` · `Solver.handleHolidaysAndReq` — already reifies requests, but **only in
  soft mode**:
  ```python
  soft = self.MIN_REQUEST_PERCENT < 100
  if soft and relaxable:
      sat = self.model.NewBoolVar(f'req_{kind}_{n}_{d}')
      for s in shifts:
          self.model.Add(self.assignments[n][d][s] == 1).OnlyEnforceIf(sat)
  ```
  Default `MIN_REQUEST_PERCENT == 100`, so in the common case there are **no literals at
  all** and `self.request_soft == []`. The existing `sat` vars are therefore *not* a usable
  handle for a core — a separate diagnosis build is required. Meetings are always hard and
  never reified.
- `shiftwork.py` · `Solver.build()` — `constraints()` + `distribute()` + `_build_warm_start()`.
  `solve()` → on OPTIMAL/FEASIBLE calls `_build_request_report()` + `_write_schedule()`,
  otherwise sets `self.request_report = None` and prints `"No solution found."`.
- `shiftwork.py` · `Solver._constraintMinimumNurse` · constraint.md §9 — one
  `Add(sum(pool) >= required)` per (day, shift); meeting nurses excluded from the DAY pool.
- `dataimporter.py:261` · `DataImporter.sample` — `random.sample`, **unseeded**. Rebuilding a
  second `Solver` from the same CSV re-samples over-quota requests differently. Diagnosis must
  therefore reuse the **already-loaded** `self.nurses` / `self.meeting_days` of the instance
  that was infeasible, not construct a new `Solver`.
- `webapp/core/solver_runner.py` · `run_schedule` / `SolveResult` — only caller of
  `shiftwork.Solver`; chdir into a temp dir; `SolveResult(solver_status, solved, grid,
  csv_text, log, request_report=None)`; `log` is `contextlib.redirect_stdout` output.
- `webapp/worker/tasks.py` · `run_solve_job` — infeasible ⇒ `status=succeeded`,
  `result_grid=None`, `result_csv=""`, `job.log = result.log`.
- `webapp/web/templates/status_fragment.html` — the INFEASIBLE branch already renders
  `{% if job.log %}<details><summary>Solver log</summary><pre>{{ job.log }}</pre></details>`.
  So anything printed during the solve is **already user-visible** with no contract change.
- `webapp/db/session.py` — schema via `Base.metadata.create_all` only; **no Alembic**.
  `create_all` does not ALTER existing tables, so a new `SolveJob` column would silently not
  exist on the deployed Postgres. This is why track C is an escalation, not a judgment call.
- `webapp/core/presolve.py` · `Issue(code, message, days, nurse)` / `PresolveReport` — the
  existing error vocabulary. Codes already in use: `tier_nurses`,
  `senior_meeting_weekend`, `request_transition`, `coverage_day`, `coverage_pair`,
  `quota_off`, `quota_shift`, `tier_meetings`, `senior_request`, `new_request`.
- `webapp/config.py:84` — `assert SOLVER_MAX_TIME_SECONDS < WORKER_JOB_TIMEOUT_SECONDS`.

### Verified facts (ortools 9.15.6755, python 3.14.6)
- `model.AddAssumptions([lits])` + `solver.SufficientAssumptionsForInfeasibility()` work, with
  1 and with 8 search workers.
- **With an objective present the core is NOT minimal** (probe: 3 literals returned where 2
  suffice; without an objective exactly 2 were returned). The diagnosis build therefore
  **omits `distribute()`** — this is a correctness requirement, not an optimisation.
- Core elements come back as proto literal indices: positive ⇒ var index, negative ⇒
  `-c-1` negated. Map with `var.Index()`.
- Measured on the two fixtures in §6: request core 5/8 literals in 0.06 s; coverage core
  2/15 literals in 0.01 s.

## 3. Behaviour spec

### A. `Solver.diagnose()`
Input: a `Solver` whose `solve()` returned `INFEASIBLE`. Output: a dict

```python
{
  'reason': 'requests' | 'coverage' | 'structural' | 'undetermined',
  'status': str,          # CP-SAT status name of the diagnosis pass that decided
  'requests': [ {'kind': 'shift'|'holiday'|'vacation'|'meeting',
                 'nurse_index': int, 'day': int,      # 1-based
                 'shift': str} ],                     # 'off' | 'day' | 'day+evening' | ...
  'coverage': [ {'day': int, 'shift': 'day'|'evening'|'night', 'required': int} ],
  'gated': int,           # assumption literals created in the deciding pass
  'truncated': bool,      # core was longer than _DIAG_MAX_ITEMS (20)
  'seconds': float,
}
```

Reason resolution:
| Pass 1 (requests gated) | Pass 2 (requests hard, coverage gated) | `reason` |
|---|---|---|
| INFEASIBLE, core non-empty | not run | `requests` |
| INFEASIBLE, core empty | INFEASIBLE, core non-empty | `coverage` |
| INFEASIBLE, core empty | INFEASIBLE, core empty | `structural` |
| INFEASIBLE, core empty | anything else | `undetermined` |
| anything else | not run | `undetermined` |

Edge cases:
- No requests at all ⇒ pass 1 gates 0 literals ⇒ empty core ⇒ pass 2 runs. Required.
- Budget exhausted ⇒ pass returns `UNKNOWN` ⇒ `reason='undetermined'`, lists stay empty.
  Never raise.
- `max_time_seconds <= 0` ⇒ return `reason='undetermined'`, `seconds=0.0`, run no solve.
- Called on a solved `Solver` ⇒ `RuntimeError` (it destroys the solved model).
- Core longer than 20 items ⇒ truncate, set `truncated=True`.
- Senior-conflicting and new-nurse-conflicting requests are **skipped** in diagnosis mode
  exactly as in normal mode, so a request the solver never applied is never blamed
  (Known failure mode 1).
- Soft mode: the `MIN_REQUEST_PERCENT` floor constraint is **not** added in diagnosis mode
  (every request is forced by its assumption, so the floor is implied).

### B. Relaxation trials
`relaxation.suggest(si, diagnosis, *, budget_seconds)` returns an ordered list of
`RelaxationTrial`, least-concession first. Each trial is probed with the **real solver** in
feasibility-only mode.

Candidate ladder (a candidate that cannot apply is omitted, not reported as failed):
1. `drop_requests` — clear exactly the cells named by `diagnosis['requests']`. Only if that
   list is non-empty.
2. `min_request_percent` at 90, then 75, then 50 — only if the input has ≥1 request cell and
   the current value is above the candidate.
3. `allow_evening_to_night` → true — only if currently false.
4. `allow_night_to_day` → true — only if currently false.
5. `coverage_minus_one` — for each distinct `(band, shift)` named by `diagnosis['coverage']`,
   lower `coverage_{band}_{shift}` by 1 (floor 0). Only if `diagnosis['coverage']` is
   non-empty. `band` = `weekend` if the day is in `settings['weekends']` else `weekday`.
6. `head_nurse_special_shift` → false — only if currently true **and** the input has ≥2
   active nurses.

Each trial: `feasible` = `True` (probe FEASIBLE/OPTIMAL) / `False` (probe INFEASIBLE) /
`None` (probe UNKNOWN **or** budget exhausted before the trial ran — `None` means "not
determined", and must never be rendered as "won't work").

Edge cases:
- `budget_seconds <= 0` ⇒ return the ladder with every `feasible=None`, run no solve.
- Trials stop as soon as the remaining budget is under `_MIN_TRIAL_SECONDS` (2.0); the rest
  keep `feasible=None`.
- Stop after the **first** trial that comes back `True`? **No** — run the whole ladder within
  budget so the user sees the cheapest working option *and* the alternatives. Ordering already
  puts the cheapest first.
- The candidate `ScheduleInput` is always a deep copy (`from_dict(to_dict())`); `si` is never
  mutated.

### C. Must NOT change
- `SolveResult.solver_status` / `.solved` / `.grid` / `.csv_text` / `.request_report` values
  for any input, solved or not.
- `run_solve_job`'s outcome mapping, `SolveJob` columns, any template, any route.
- `presolve.check` behaviour or its codes.
- Behaviour of `build()` / `solve()` / `run()` when the new kwargs are not passed.
- Wall clock of a *solvable* job (diagnosis never runs on it).

## 4. Design

### Files
| File | Change |
|---|---|
| `shiftwork.py` | **modify** — diagnosis mode in `handleHolidaysAndReq` + `_constraintMinimumNurse`; new `diagnose()` + `_diag_*` helpers; `build(*, with_objective=True)`; `solve(..., write_output=True)` |
| `webapp/core/diagnosis.py` | **new** — index→name mapping + human text rendering of a diagnosis dict |
| `webapp/core/relaxation.py` | **new** — `RelaxationTrial`, `suggest()`, candidate ladder |
| `webapp/core/solver_runner.py` | **modify** — `probe_feasible()`; `SolveResult.diagnosis` / `.relaxations`; wire the post-INFEASIBLE pass; print rendered text into the captured log |
| `webapp/config.py` | **modify** — `SOLVER_DIAGNOSIS_MAX_SECONDS` (20), `SOLVER_RELAX_MAX_SECONDS` (25), `diagnosis_budget()` clamp helper |
| `test_shiftwork.py` | **modify** — diagnose tests |
| `webapp/tests/test_diagnosis.py` | **new** |
| `webapp/tests/test_relaxation.py` | **new** |
| `constraint.md` | **modify** — new §20 "Infeasibility diagnosis" |
| `CODEBASE.md` | **modify** — solver section, two new core modules, config keys, failure mode 9 note |
| `FEATURE-GAPS.md` (local only, **never committed**) | **modify** — items 8 & 11 status |

### Exact signatures
```python
# shiftwork.py
def build(self, *, with_objective: bool = True)                       # changed
def solve(self, max_time_seconds: float = 120, *, write_output: bool = True)   # changed
def diagnose(self, max_time_seconds: float = 20.0) -> dict            # new
def _diag_build(self, *, gate_coverage: bool) -> list                 # new, returns [(lit, record)]
def _diag_solve(self, gated: list, max_time_seconds: float) -> tuple[str, list]  # new

# webapp/core/solver_runner.py
def probe_feasible(schedule_input: ScheduleInput, max_time_seconds: float) -> str   # new, status name
def run_schedule(schedule_input, max_time_seconds: float,
                 *, diagnose_seconds: float = 0.0,
                 relax_seconds: float = 0.0) -> SolveResult                        # changed
@dataclass
class SolveResult:
    ...                                   # existing 6 fields unchanged, same order
    diagnosis: Optional[dict] = None      # new
    relaxations: Optional[list] = None    # new  (list[dict], relaxation.to_dict())

# webapp/core/diagnosis.py
def name_diagnosis(diagnosis: dict | None, schedule_input) -> dict | None
def render_text(named: dict | None, relaxations: list | None) -> str

# webapp/core/relaxation.py
@dataclass
class RelaxationTrial:
    code: str; label: str; detail: str
    feasible: bool | None = None
    settings_delta: dict = field(default_factory=dict)
    cleared_cells: list = field(default_factory=list)
    def to_dict(self) -> dict: ...
def suggest(si, diagnosis: dict | None, *, budget_seconds: float) -> list[RelaxationTrial]

# webapp/config.py
SOLVER_DIAGNOSIS_MAX_SECONDS: int = 20
SOLVER_RELAX_MAX_SECONDS: int = 25
def diagnosis_budget(solver_seconds: int, *, margin: int = 10) -> tuple[float, float]
```

`diagnosis_budget` clamps so the worst case still fits:
```
room = WORKER_JOB_TIMEOUT_SECONDS - solver_seconds - margin
if room <= 0: return (0.0, 0.0)
diag  = min(SOLVER_DIAGNOSIS_MAX_SECONDS, room)
relax = min(SOLVER_RELAX_MAX_SECONDS, room - diag)
return (float(diag), float(max(0.0, relax)))
```
Defaults: `180 - 120 - 10 = 50` ⇒ `(20.0, 25.0)`. A misconfigured `SOLVER_MAX_TIME_SECONDS=175`
⇒ `(0.0, 0.0)` ⇒ diagnosis silently skipped. **No new assert**: an existing deployment with a
tight timeout must keep booting.

### Data flow
1. `worker/tasks.run_solve_job` → `run_schedule(si, max_time_seconds=job.max_time_seconds,
   diagnose_seconds=d, relax_seconds=r)` with `d, r = config.diagnosis_budget(job.max_time_seconds)`.
2. `run_schedule` runs the solve as today. If `status_int == cp_model.INFEASIBLE` **and**
   `diagnose_seconds > 0`: `solver.diagnose(diagnose_seconds)` on the **same instance**
   (reuses the already-sampled request data — see §2 `DataImporter.sample`).
3. `diagnosis.name_diagnosis(raw, si)` maps `nurse_index` → name via `si.active_nurses()`
   (same mapping as `_name_request_report`).
4. If `relax_seconds > 0`: `relaxation.suggest(si, raw, budget_seconds=relax_seconds)`, each
   trial probed by `probe_feasible` (its own temp dir, feasibility-only model).
5. `diagnosis.render_text(named, relaxations)` is **printed inside the redirected-stdout
   block**, so it lands in `SolveResult.log` → `job.log` → the existing
   `status_fragment.html` INFEASIBLE `<details>`. No DB or template change.

### Rejected alternatives
- **Reuse the existing soft-mode `request_soft` `sat` literals as assumptions.** Rejected:
  they only exist when `MIN_REQUEST_PERCENT < 100`, which is not the default, so the common
  case has no literals; and reusing them keeps `distribute()` in the model, which the probe
  proved yields non-minimal cores.
- **A second model in `webapp/core/diagnose.py`, mirroring the hard rules like
  `presolve.py` does.** Rejected: a second mirror of the solver's rules doubles Known failure
  mode 9. The diagnosis build reuses `Solver.constraints()` so it cannot drift.
- **Construct a fresh `Solver` for the diagnosis pass.** Rejected: `DataImporter.sample` is
  unseeded, so an over-quota ward would be re-sampled and the diagnosis would blame request
  cells the failed run never applied.
- **Extend the `config.py` timeout assert to cover the new budgets.** Rejected: it would turn
  an existing tight-but-working deployment into a boot failure. Clamp instead.

### Kernel — `Solver.diagnose()` and the gated builds (paste verbatim)

```python
    # ── Infeasibility diagnosis (constraint.md §20) ───────────────────────────
    # Assumption-based unsat core. We rebuild the model from the request data
    # ALREADY loaded on this instance (never re-import: DataImporter.sample is
    # unseeded, so a second import would re-sample over-quota requests and we
    # would blame cells the failed run never applied), reify each relaxable
    # group behind one assumption literal, and ask CP-SAT which assumptions are
    # sufficient for infeasibility.
    #
    # The diagnosis build deliberately omits distribute(): with an objective
    # present CP-SAT returns a *sufficient* but non-minimal core (verified on
    # ortools 9.15), which would blame innocent requests.
    _DIAG_MAX_ITEMS = 20
    _DIAG_MIN_PASS_SECONDS = 2.0

    def diagnose(self, max_time_seconds: float = 20.0) -> dict:
        """Explain an INFEASIBLE model: which requests, or which day's coverage.

        Must only be called after a solve that did NOT find a solution; it
        REPLACES self.model and self.assignments, so any solution on this
        instance is destroyed. Never raises on solver trouble — an unhelpful
        answer comes back as reason='undetermined'.
        """
        if getattr(self, 'status', None) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError('diagnose() invalidates the model; '
                               'do not call it on a solved Solver')
        out = {'reason': 'undetermined', 'status': '', 'requests': [],
               'coverage': [], 'gated': 0, 'truncated': False, 'seconds': 0.0}
        if max_time_seconds <= 0:
            return out

        t0 = time.time()
        budget1 = max(self._DIAG_MIN_PASS_SECONDS, max_time_seconds * 0.6)
        gated = self._diag_build(gate_coverage=False)
        status, core = self._diag_solve(gated, budget1)
        out['status'] = status
        out['gated'] = len(gated)

        if status == 'INFEASIBLE' and core:
            out['reason'] = 'requests'
            out['requests'], out['truncated'] = self._diag_cap(core)
        elif status == 'INFEASIBLE':
            # The hard structure alone is contradictory: gate coverage instead
            # and let the core name the binding day(s) and shift(s).
            left = max_time_seconds - (time.time() - t0)
            if left < self._DIAG_MIN_PASS_SECONDS:
                out['reason'] = 'structural'
            else:
                gated = self._diag_build(gate_coverage=True)
                status2, core2 = self._diag_solve(gated, left)
                out['status'] = status2
                out['gated'] = len(gated)
                if status2 == 'INFEASIBLE' and core2:
                    out['reason'] = 'coverage'
                    out['coverage'], out['truncated'] = self._diag_cap(core2)
                elif status2 == 'INFEASIBLE':
                    out['reason'] = 'structural'
        out['seconds'] = round(time.time() - t0, 3)
        return out

    def _diag_cap(self, core: list) -> tuple:
        """Trim a core to _DIAG_MAX_ITEMS; sort by day so the message reads in
        calendar order."""
        core = sorted(core, key=lambda r: (r.get('day', 0), str(r.get('shift', ''))))
        if len(core) > self._DIAG_MAX_ITEMS:
            return core[:self._DIAG_MAX_ITEMS], True
        return core, False

    def _diag_build(self, *, gate_coverage: bool) -> list:
        """Rebuild the model with one assumption literal per relaxable group.

        Reuses constraints() so the hard rules can never drift from the real
        model (Known failure mode 9). No distribute(), no warm start.
        """
        self.model = cp_model.CpModel()
        self.assignments = {}
        self.double_shift_vars = []
        self.initAssignments()
        self._diag_gated = []
        self._diag_mode = True
        self._diag_gate_coverage = gate_coverage
        try:
            self.constraints()
        finally:
            self._diag_mode = False
        return self._diag_gated

    def _diag_gate(self, n, d, shifts, kind):
        """Reify one request cell behind a fresh assumption literal."""
        lit = self.model.NewBoolVar(f'diag_{kind}_{n}_{d}')
        for s in shifts:
            self.model.Add(self.assignments[n][d][s] == 1).OnlyEnforceIf(lit)
        self._diag_gated.append((lit, {
            'kind': kind, 'nurse_index': n, 'day': d + 1,
            'shift': '+'.join(x.value for x in shifts)}))

    def _diag_solve(self, gated: list, max_time_seconds: float) -> tuple:
        """Solve the gated model under its assumptions and map the core back to
        the records collected by _diag_build."""
        if gated:
            self.model.AddAssumptions([lit for lit, _rec in gated])
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = max(self._DIAG_MIN_PASS_SECONDS,
                                               max_time_seconds)
        s.parameters.num_search_workers = 1
        st = s.Solve(self.model)
        name = s.StatusName(st)
        if st != cp_model.INFEASIBLE:
            return name, []
        by_index = {lit.Index(): rec for lit, rec in gated}
        core = []
        for c in s.SufficientAssumptionsForInfeasibility():
            v = c if c >= 0 else -c - 1          # CP-SAT literal encoding
            rec = by_index.get(v)
            if rec is not None:
                core.append(rec)
        return name, core
```

`handleHolidaysAndReq` gains a diagnosis branch at the top of `_add_request`, keeping every
skip-guard (senior / new nurse) and the meeting loop intact:

```python
        def _add_request(n, d, shifts, kind, relaxable):
            shifts = list(shifts) if isinstance(shifts, (list, tuple, set)) else [shifts]
            if getattr(self, '_diag_mode', False):
                # Diagnosis: gate EVERY request, relaxable or not, so the core
                # can name it. The MIN_REQUEST_PERCENT floor is skipped below.
                self._diag_gate(n, d, shifts, kind)
                return
            if soft and relaxable:
                ...   # unchanged
```
the meeting loop becomes
```python
        for n, days in self.meeting_days.items():
            for d in days:
                if getattr(self, '_diag_mode', False):
                    self._diag_gate(n, d, [Shift.DAY], 'meeting')
                else:
                    self.model.Add(self.assignments[n][d][Shift.DAY] == 1)
```
and the floor block becomes `if soft and self.request_soft and not getattr(self,
'_diag_mode', False):`.

`_constraintMinimumNurse` gains, after `nurses_assigned` is built:
```python
                if getattr(self, '_diag_mode', False) and self._diag_gate_coverage:
                    lit = self.model.NewBoolVar(f'diag_cov_{d}_{shift.value}')
                    self.model.Add(sum(nurses_assigned) >= required).OnlyEnforceIf(lit)
                    self._diag_gated.append((lit, {
                        'day': d + 1, 'shift': shift.value, 'required': required}))
                else:
                    self.model.Add(sum(nurses_assigned) >= required)
```

`__init__` gains, beside `request_soft`:
```python
        # Diagnosis state (see diagnose()). _diag_mode is only true while
        # _diag_build() is assembling the gated model.
        self._diag_mode = False
        self._diag_gate_coverage = False
        self._diag_gated: list = []
```

## 5. Work packages

| WP | Owner | Model | Files (disjoint) | Depends on | Effort |
|----|-------|-------|------------------|-----------|--------|
| 1 | **lead** | — | `shiftwork.py` | — | M |
| 2 | web-specialist | sonnet | `webapp/core/diagnosis.py`, `webapp/core/solver_runner.py`, `webapp/config.py` | WP1 | M |
| 3 | web-specialist | sonnet | `webapp/core/relaxation.py` | WP2 (signature only — fixed in §4) | M |
| 4 | test-specialist | sonnet | `test_shiftwork.py`, `webapp/tests/test_diagnosis.py`, `webapp/tests/test_relaxation.py` | WP1–3 | M |
| 5 | **lead** | — | `constraint.md`, `CODEBASE.md`, `FEATURE-GAPS.md` | WP1–4 | S |

**WP1 (lead).** Apply the Kernel in §4 verbatim.

**WP2.** Steps, in order:
1. `webapp/config.py`: add the two ints next to `SOLVER_MAX_TIME_SECONDS`
   (pattern: `webapp/config.py:36`) and `diagnosis_budget()` exactly as in §4. Do **not**
   touch the existing assert.
2. `webapp/core/diagnosis.py` (new):
   - `name_diagnosis(diagnosis, schedule_input)` — returns a copy with each
     `requests[i]['name']` added from `schedule_input.active_nurses()`; fall back to
     `f"Nurse {i}"` out of range. Copy the pattern at
     `webapp/core/solver_runner.py:_name_request_report`.
   - `render_text(named, relaxations)` — plain text, ≤ ~25 lines, starts with
     `"[Diagnosis] "`. One line per core item. `reason='requests'`: "These N request(s)
     cannot all be satisfied — dropping any one of them may be enough:" then
     `  · {name} day {day}: {shift} ({kind})`. `reason='coverage'`: "Coverage cannot be met:"
     then `  · day {day}: needs {required} {shift}`. `reason='structural'`: say the ward's
     fixed rules (senior/transition/daily) are contradictory on their own.
     `reason='undetermined'`: say the diagnosis did not finish in its time budget — do **not**
     imply anything about the schedule. Then, if `relaxations`: "Relaxations tested:" and
     `  · [works | no | untested] {label} — {detail}`. Returns `""` for `None`/empty input.
3. `webapp/core/solver_runner.py`:
   - `probe_feasible(schedule_input, max_time_seconds) -> str`: same temp-dir/chdir pattern as
     `run_schedule` (`solver_runner.py:66-78`), then
     `Solver(...).build(with_objective=False)` and
     `.solve(max_time_seconds=..., write_output=False)`; return
     `_STATUS_NAMES.get(status_int, str(status_int))`. Redirect stdout to a throwaway buffer.
     Always restore cwd and remove the temp dir in `finally`.
   - Add the two `SolveResult` fields **at the end** with defaults.
   - In `run_schedule`, after `solved` is computed, add an
     `if not solved and status_int == cp_model.INFEASIBLE:` block **inside** the
     `contextlib.redirect_stdout(log_buf)` scope (so the rendered text reaches `log`) that
     calls `solver.diagnose(...)`, `name_diagnosis`, `relaxation.suggest`, `render_text`, and
     `print(...)`s the text. Wrap the whole block in `try/except Exception` and
     `print(f"[Diagnosis] unavailable: {exc}")` on failure — **a diagnosis failure must never
     change the job's outcome.**
   - Pass `diagnosis` / `relaxations` into the returned `SolveResult`.
4. `webapp/worker/tasks.py` is **in WP2's scope for exactly two lines**: compute
   `d, r = config.diagnosis_budget(job.max_time_seconds)` and pass them to `run_schedule`.
   Change nothing else in that file.

**WP3.** `webapp/core/relaxation.py` only. Implement `RelaxationTrial` + `suggest()` per §3.B.
Deep-copy candidates with `ScheduleInput.from_dict(si.to_dict())`. Probe with
`from webapp.core.solver_runner import probe_feasible`. Settings keys and defaults:
`webapp/core/schedule_input.py` `_DEFAULT_SETTINGS`. Clearing a cell = `del
nurse.shifts[day]` on the matching **active** nurse index (guard `KeyError`).

**WP4.** See §6.

## 6. Acceptance tests (must exist and pass)

`test_shiftwork.py` (fixtures below are the two the lead already verified):

- `test_diagnose_names_conflicting_requests` — ward `department` tier, 10 days, coverage 2/2/2
  every band, `head_nurse_special_shift,false`, 8 nurses, N0–N5 request `off` on day 5, N6–N7
  request `ช` on day 5. `run()` ⇒ `INFEASIBLE`. `diagnose(15)` ⇒ `reason == 'requests'`,
  `len(requests) >= 3`, **every** returned record has `day == 5`, and at least one has
  `kind == 'holiday'`. Fails if the gating in `_add_request` is removed (core would be empty
  ⇒ reason `coverage`/`structural`).
- `test_diagnose_names_coverage_day` — 3 nurses, 5 days, coverage 2/2/2, **no requests**,
  `head_nurse_special_shift,false`. `run()` ⇒ `INFEASIBLE`. `diagnose(15)` ⇒
  `reason == 'coverage'`, `coverage` non-empty, every entry's `shift` in
  `{'day','evening','night'}` and `required == 2`. Fails if the coverage gating in
  `_constraintMinimumNurse` is removed (reason would be `structural`).
- `test_diagnose_rejects_solved_solver` — the existing feasible `_MINI_WARD_CSV`: `run()`
  solves, then `diagnose()` raises `RuntimeError`.
- `test_diagnose_zero_budget` — infeasible fixture, `diagnose(0)` ⇒
  `reason == 'undetermined'`, `seconds == 0.0`, no exception.
- `test_build_without_objective_still_feasible` — `_MINI_WARD_CSV`,
  `build(with_objective=False).solve(max_time_seconds=20, write_output=False)` ⇒ status in
  `{OPTIMAL, FEASIBLE}` **and no file** matching `output/*Final.csv` was created. Fails if
  `write_output` is ignored.
- Existing tests in this file must stay green unchanged.

`webapp/tests/test_diagnosis.py`:
- `test_name_diagnosis_adds_names` — a diagnosis dict with `nurse_index 0/1` + a
  `ScheduleInput` with named nurses ⇒ names attached; index past the end ⇒ `"Nurse 7"`.
- `test_name_diagnosis_uses_active_nurses_only` — a `ScheduleInput` whose nurse 0 is
  `active=False` ⇒ `nurse_index 0` maps to the **first active** nurse's name. Fails if the
  mapping uses `si.nurses` instead of `si.active_nurses()` (Known failure mode: Active
  nurses / index alignment).
- `test_render_text_requests` — reason `requests` with two records ⇒ text contains both nurse
  names, both day numbers, and does not contain the word "timeout".
- `test_render_text_undetermined_makes_no_claim` — reason `undetermined` ⇒ text mentions the
  time budget and contains neither "conflict" nor "coverage".
- `test_render_text_empty` — `render_text(None, None) == ""`.
- `test_diagnosis_budget_clamps` — with `SOLVER_MAX_TIME_SECONDS=120`,
  `WORKER_JOB_TIMEOUT_SECONDS=180` ⇒ `(20.0, 25.0)`; `diagnosis_budget(175)` ⇒ `(0.0, 0.0)`;
  `diagnosis_budget(165)` ⇒ `(5.0, 0.0)`. Fails if the clamp is dropped.

`webapp/tests/test_relaxation.py` (solver-backed, keep budgets small):
- `test_suggest_zero_budget_marks_all_untested` — every trial `feasible is None`, no solve run
  (assert it returns in well under a second).
- `test_suggest_finds_transition_relaxation` — a ward infeasible **only** because
  `allow_night_to_day` is false (N requested day d, ช requested day d+1 for the one nurse who
  must cover) ⇒ the `allow_night_to_day` trial comes back `feasible is True`. Fails if the
  candidate ladder drops the transition candidates.
- `test_suggest_omits_inapplicable_candidates` — input with `allow_evening_to_night=true`
  already ⇒ no trial with `code == 'allow_evening_to_night'`.
- `test_suggest_does_not_mutate_input` — `si.to_dict()` identical before and after.
- `test_drop_requests_candidate_clears_named_cells` — a diagnosis naming (nurse 0, day 5) ⇒
  the `drop_requests` candidate's `cleared_cells` contains that pair and the candidate's
  `ScheduleInput` has no cell at day 5 for that nurse.

`webapp/tests/test_solver_runner.py` (add, do not rewrite):
- `test_infeasible_run_puts_diagnosis_in_log` — an infeasible `ScheduleInput`,
  `run_schedule(si, 20, diagnose_seconds=10, relax_seconds=0)` ⇒ `solved is False`,
  `solver_status == "INFEASIBLE"`, `grid is None`, `csv_text is None`,
  `"[Diagnosis]" in result.log`, `result.diagnosis['reason'] != 'undetermined'`.
- `test_solved_run_has_no_diagnosis` — a feasible input with `diagnose_seconds=10` ⇒
  `result.diagnosis is None` and `"[Diagnosis]" not in result.log`. Fails if diagnosis runs on
  solvable jobs.
- `test_diagnosis_failure_does_not_change_outcome` — monkeypatch `Solver.diagnose` to raise ⇒
  `solver_status == "INFEASIBLE"`, `solved is False`, and the job-visible fields are
  unchanged; log mentions `unavailable`.

Suites that must stay green (from repo root):
`python -m unittest discover -s webapp/tests -p "test_*.py"` · `python -m unittest test_shiftwork`

**Windows harness note:** ~49 `PermissionError`s raised in `tearDown` (temp sqlite file still
open) are a pre-existing harness artifact, not failures. Verify any error is in `tearDown`
before treating it as a regression.

## 7. Risks

Known failure modes touched (from `CODEBASE.md` → "Known failure modes"):
- **1. Constraint vs request conflict → silent infeasibility** — diagnosis mode must keep the
  senior and new-nurse skip-guards, or it will blame a request the solver never applied.
- **2. Senior rules cover head AND deputy** — the skipped-request branch covers both indices;
  unchanged, but the gated build runs through it.
- **3. `mtg` days** — meetings are gated as `kind='meeting'`; the DAY-coverage exclusion in
  `_constraintMinimumNurse` must survive the coverage-gating edit.
- **4. `vac` is protected** — vacations are gated as `kind='vacation'`; diagnosis may name an
  enforced vacation as part of a conflict (correct: it *is* a cause), but nothing in the
  diagnosis path may make it droppable in a real solve.
- **7. Half-wired settings** — the two new config keys are read in exactly one place
  (`diagnosis_budget`) and are not user-editable, so the full setting checklist does not apply.
- **9. Pre-solve mirror drift** — the whole point of reusing `Solver.constraints()`: the
  diagnosis adds **no** third mirror. Nothing in `presolve.py` changes.
- **Active nurses / index alignment** (Gotchas) — `name_diagnosis` must use
  `active_nurses()`, like `_name_request_report`.
- **Timeout ≠ infeasible** (Gotchas) — diagnosis runs on `INFEASIBLE` only, and
  `reason='undetermined'` must never be rendered as a claim about the schedule.

Accepted risks:
- The CP-SAT core is *sufficient*, not guaranteed minimal. No deletion-filter minimisation in
  v1 — dropping `distribute()` already produced minimal cores on both fixtures, and the
  20-item cap bounds the blowup. If real wards return large cores, add the filter then.
- An infeasible job gets slower by up to `diag + relax` seconds (default ≤ 45 s). Bounded by
  the clamp; a solvable job is unaffected.
- `probe_feasible` chdirs, so it inherits `run_schedule`'s single-run-at-a-time constraint.
  Trials are sequential inside one job, so this is safe today, but it is one more reason the
  solve path can never be made concurrent without fixing the chdir.

What the lead will verify at review: diagnosis never runs on a solved job; the skip-guards are
intact in diagnosis mode; `distribute()` is absent from the gated build; `build`/`solve`
defaults unchanged; `name_diagnosis` uses `active_nurses()`; no `SolveJob`/template/route
change; every new test fails when its rule is deleted.

## 8. Do NOT
- Edit files outside your WP's list (return proposed diffs instead). Add dependencies. Change
  signatures not listed in §4.
- Rewrite or weaken a test to make it pass. Do not touch existing assertions in
  `test_shiftwork.py` or `webapp/tests/test_solver_runner.py`.
- Add a `SolveJob` column, an Alembic migration, or any template/route change. **Track C is
  escalated and out of scope.**
- Implement best-so-far streaming or a solution callback. **Track D is out of scope.**
- Change `presolve.py`, `fairness.py`, `dataimporter.py`, `constant.py`, or
  `schedule_input.py`.
- Change `distribute()`, any existing constraint formula, or the existing `config.py` assert.
- `git add -A`, or stage/commit `FEATURE-GAPS.md`, `assets/`, `Templates/`, `.claude/`,
  `output/`.
- Seed `random` in `dataimporter.py` to make diagnosis reproducible (that is an input-contract
  change — escalation).

## 9. STOP and report instead of guessing if
- A named file/symbol is missing or its signature differs from §2/§4.
- An acceptance test cannot pass without breaking §8.
- A fixture that §6 says is INFEASIBLE solves on your machine.
- The diagnosis would need a DB column or template change to be useful.

## 10. Report format
Files changed (paths only) · test commands + pass/fail counts · deviations from spec + why ·
open questions. No diffs, no file contents.

## 11. Done / deviations log
- [ ] Acceptance tests + suites green (run by the lead)
- [ ] `constraint.md` §20 + `CODEBASE.md` updated
- [ ] Status → done
- Deviations: …
