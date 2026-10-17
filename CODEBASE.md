# ShiftWork — Codebase Map

Quick-reference for the whole repo. Two parts: the **core OR-Tools solver** (repo
root) and the **FastAPI web app** (`webapp/`). The web app never reimplements
scheduling — it wraps the solver.

---

## Data flow (the one path to know)

```
CSV upload ─┐
            ├─► ScheduleInput ─► to_solver_csv() ─► temp dir ─► shiftwork.Solver
grid edit ──┘   (canonical)                                        │ build()+solve()
                                                                   ▼
        result grid + request_report ◄── solver_runner._read_output ◄── output/*Final.csv
                    │
                    ▼  stored on SolveJob.result_grid (JSON) + result_csv
             status_fragment.html renders it
```

`ScheduleInput` is the single canonical shape; CSV upload and the grid editor
both normalize into it, and everything downstream reads it.

---

## Core solver (repo root)

### `shiftwork.py` — `Solver`
Lifecycle (construction is side-effect-free):
- `__init__(filename, num_days, weekends, allow_shift_e_n, allow_shift_n_d)` → config + `loadReqShifts` + `initAssignments` (creates BoolVars `a[n][d][shift]`). **Does not solve.**
- `build(*, with_objective=True)` → `constraints()` + `distribute()` + `_build_warm_start()`. `with_objective=False` builds a pure feasibility model (no objective, no warm start) — used by the relaxation probes.
- `solve(max_time_seconds=120, *, write_output=True)` → configure `CpSolver`, `Solve`, `_build_request_report()`, `_write_schedule()`; returns CP-SAT status. `write_output=False` skips the CSV write.
- `run(max_time_seconds=120)` = `build()` then `solve()`.
- `diagnose(max_time_seconds=20.0) -> dict` → **only after an unsolved solve** (constraint.md §20). Rebuilds the model in place with each request cell (and, in pass 2, each coverage minimum) reified behind a CP-SAT **assumption literal**, then reads `SufficientAssumptionsForInfeasibility()`. Returns `{reason: requests|coverage|structural|undetermined, status, requests:[{kind,nurse_index,day,shift}], coverage:[{day,shift,required}], gated, truncated, seconds}` (days 1-based). Raises `RuntimeError` on a solved Solver (it destroys the model). Helpers `_diag_build/_diag_gate/_diag_solve/_diag_cap`; `_diag_mode` branches live in `handleHolidaysAndReq()` and `_constraintMinimumNurse()`. Two non-obvious invariants: the diagnosis build **omits `distribute()`** (an objective makes CP-SAT return non-minimal cores) and it **reuses the loaded instance**, never a fresh `Solver` (`DataImporter.sample` is unseeded → a re-import re-samples over-quota requests).

`constraints()` orchestrates named rule methods:
- `handleHolidaysAndReq()` — applies requested shifts/holidays. **Hard** if `MIN_REQUEST_PERCENT==100`; else **soft** (reified with a `sat` BoolVar per request, tracked in `self.request_soft`, plus a floor `sum(sat) >= ceil(pct*total)`). Senior-nurse conflicting requests are dropped here. Meetings always hard. **Off-ward duties (§21)** are applied here too: hard, exclusive (`a[n][d][s]==1`, every other shift incl. OFF `==0`), skip-guarded against §16/§17, and the surviving cells build `_offward_applied`/`_offward_excluded`. **Negative requests (§23, `no:X`)** go through the same `_add_request(..., negate=True)` (`==0`, kind `'avoid'`), so soft mode, the acceptance report and `diagnose()` cover them with no parallel path.
- `_add_daily_shift_rules()` — must-assign, OFF exclusivity, per-day cap (`MAX_SHIFT_PER_DAY`), Night+Day ban, new-nurse Evening+Night ban.
- `_add_shift_transition_rules()` — E→N and N→D across days (flag-gated).
- `_add_double_shift_tracking()` — double-shift indicators (feed objective).
- `_constraintMinimumNurse()` — per-day coverage minimums. One `excluded` set per `(day, shift)`: meeting nurses on Day (§15) **and** off-ward duties on their own shift (§21). The Day-only special case is gone.
- `optionHeadNurse()` — head(0)+deputy(1): weekdays Day/OFF, weekends OFF. **Assumes ≥2 nurses when on.**

`distribute()` — objective `Minimize(10*shift_imbalance + 10*holiday_imbalance + double_shifts [+ 100000*dropped_requests])`. The huge weight makes keeping requests dominate fairness.

`_write_schedule()` — renders solved assignments to a transposed grid (an off-ward cell wins over the symbol cascade and re-emits its own token, e.g. `flt:ด`, so results round-trip into the grid editor), writes `output/{label} Final.csv` (`utf-8-sig`). Returns label.
`_build_request_report()` — fills `self.request_report = {total, accepted, dropped:[{nurse_index,day,shift,kind}], percent}` (None unless soft mode).

Key attributes/flags: `MAX_SHIFT_PER_DAY`, `ALLOW_SHIFT_E_N/_N_D`, `HEAD_NURSE_SPEACIAL_SHIFT`, `HEAD_NURSE_INDEX=0`, `DEPUTY_NURSE_INDEX=1`, `enable_meetings`, `MIN_REQUEST_PERCENT`, `RELAX_DAYS_OFF`, `new_nurse_indices`, `meeting_days`, `request_soft`, `request_report`, `status`, `offward_days`/`_offward_applied`/`_offward_excluded` (§21), `holidays` (§22), `_diag_mode`/`_diag_gate_coverage`/`_diag_gated` (diagnosis only).

**Five coverage bands (§22).** `self.coverage` has `weekday`/`weekend`/`saturday`/`sunday`/`holiday` entries and `_rebuild_days()` resolves each day with precedence **holiday > sunday > saturday > weekend > weekday**. `self.holidays`, `self.saturdays`, `self.sundays` (0-based) drive *only* that lookup (absent → empty → the day keeps the weekend band); **`self.weekends` is the UNION of weekends, holidays, saturdays and sundays**, so §16/§17, the §21 off-ward skip-guard and `_build_warm_start` see a holiday exactly as they did when auto-fill merged holidays into `weekends`. The name is a known lie — a rule needing calendar weekends only must use `self.weekends - self.holidays`. The holiday band defaults to the weekend band, which is what makes the split behaviour-preserving.
Solver params in `solve()`: `num_search_workers=os.cpu_count()`, `linearization_level=2`, `max_time_in_seconds`.
`__main__` processes every file in `input/` via `Solver(...).run()`.

### `dataimporter.py`
- `DataImporter(filename, maxDayOff, maxReqShift)` → `.transform()`. Parses sectioned CSV `[ward]`/`[settings]`/`[schedule]` (legacy = grid only). Produces `reqShifts` (`{day: [Shift, ...]}` per nurse — `ช/บ`/`ด/บ` = one cell, two shifts; **sampled down per cell** to tier `max_req_shifts`), `reqDayOff`, `reqMeetings`, `reqNegShifts` (`{day: Shift}` per nurse — `no:X` cells, §23; share the `max_req_shifts` sampling pool with `reqShifts`), `reqOffward` (`[(0-based day, Shift, kind)]` per nurse — off-ward `flt`/`ref` cells, **never sampled**, **not tier-gated**), `newNurseIndices`. Enforces tier `max_nurses` (raises ValueError if exceeded). `.settings` dict, `.ward_info`, `.tier`.
- `TIER_LIMITS` / `LICENSE_REGISTRY` — tier definitions + Phase-0 license keys.
- Static parsers: `_parse_sections`, `_parse_kv_section`, `_parse_weekends` (1-based→0-based; **empty string → DEFAULT weekends, not empty**), `_parse_holidays` (same conversion but **empty string → `[]`** — the deliberate opposite, §22; also parses `saturdays`/`sundays`). The `coverage_{holiday,saturday,sunday}_*` settings default to **that same file's** parsed `coverage_weekend_*` values, not to constants.
- `SettingLoader` (legacy defaults), `FileLoader` (scans a folder).

### `constant.py`
- `Shift` enum: `DAY/EVENING/NIGHT/OFF`.
- `LocaleShift`: Thai symbols — DAY `ช`, EVENING `บ`, NIGHT `ด`, DAY_EVENING `ช/บ`, NIGHT_EVENING `ด/บ`, OFF `off` Plus VACATION `vac` (approved leave; assigned as OFF, rendered distinctly).
- **Off-ward duty** (constraint.md §21): `OffWard` enum (`flt` float, `ref` refer), `OFFWARD_SEP=":"`, `OFFWARD_SHIFTS` (symbol→Shift) and its inverse `SHIFT_LOCALE`, `OFFWARD_KINDS`, `OFFWARD_TOKENS` (the six valid cells `flt:ช`…`ref:ด`), and `parse_offward(token) -> (kind, Shift) | None` — the ONE definition of the token grammar, imported by `dataimporter`, `schedule_input`, `presolve` and `fairness`. A bare `flt`/`ref` parses as `None` (rejected by `validate()`).
- **Negative request** (constraint.md §23): `NEGATE_KIND="no"`, `NEGATE_TOKENS` (`no:ช` `no:บ` `no:ด`), `parse_negation(token) -> Shift | None`. `no:off` and bare `no` are invalid. In the Excel roster it is just a day-cell token (dropdown + pink fill + legend cell next to `ref:ด` + Guide row); no new column or setting.

### `gui.py` — legacy Tkinter desktop app (runs `Solver` in a thread). Not used by the web app.
### `test_shiftwork.py` — solver lifecycle + per-rule (force-illegal→infeasible) tests.

---

## Web app (`webapp/`)

### `config.py` — all env-driven. Key vars:
`SOLVER_MAX_TIME_SECONDS`(120), `WORKER_JOB_TIMEOUT_SECONDS`(180) [invariant asserted: solver < worker], `DATABASE_URL`(sqlite default), `REDIS_URL`, `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`, `SESSION_HTTPS_ONLY`(False), `AUTH_ENABLED`(True), `DEV_AUTH_BYPASS`(False), `DEV_USER_EMAIL`, `SOLVE_INLINE`(False), `ALLOWED_EMAILS`(set), `ALLOWED_DOMAIN`, `MAX_UPLOAD_BYTES`(1 MiB), `PRESOLVE_BLOCKS_SOLVE`(False).

`PRESOLVE_BLOCKS_SOLVE` is off: the pre-solve checklist still runs and still
renders on the Check page, but a blocker no longer refuses to queue a solve.
An infeasible ward now gets a job whose INFEASIBLE verdict is explained by
`Solver.diagnose()` (§20), instead of being refused before it starts. Set it to
1 to restore the hard block.

### `logging_setup.py`
`setup_logging(path=None)` — lazy ERROR `FileHandler` to `logs/shiftwork_<timestamp>.log` (created only on first error) + installs `sys.excepthook`. `log_path()`. Called from `web/app.py` import and `worker/run_worker.py`.

### `core/schedule_input.py` — the canonical model
- `Nurse(name, type="", shifts={day:token}, active=True, staff_class="")`. `active=False` = disabled (kept, not scheduled). **`staff_class`** in `rn`/`pn`/`na`/`""` is an axis **orthogonal to `type`** (seniority) — a PN can be senior; normalised in `__post_init__`.
- Staff groups: `GROUP_RN`/`GROUP_PN_NA`/`GROUP_LABELS`/`STAFF_CLASSES`, `group_of(staff_class)` (`pn`+`na` → ONE `pn_na` pool; the PN-vs-NA label is display only). `solve_groups()` → `[]` when no active nurse is classed (legacy single solve), else the present groups in order `["rn","pn_na"]`. `active_nurses(group=None)`, `group_indices(group)` (group-local row → global active index).
- `ScheduleInput(ward_meta, settings, nurses)`.
  - `from_csv(text)` (reuses DataImporter parsers), `from_xlsx(bytes)` (reads the Excel roster template's Roster sheet directly into `from_grid` — label-driven cards + day-number-header detection; needs openpyxl), `from_grid(ward_meta, settings, nurses)`, `to_solver_csv()`, `to_dict()`/`from_dict()`, `validate()` (raises ValueError; counts **active** nurses; needs ≥2 active if senior rule on), `active_nurses()`.
- `_DEFAULT_SETTINGS` keys (plus the 15 legacy + 30 per-group coverage keys): `num_days`, `weekends`(1-based list), `holidays`/`saturdays`/`sundays`(1-based lists, **default `[]`**), `allow_evening_to_night`, `allow_night_to_day`, `head_nurse_special_shift`, `min_request_percent`(100), `relax_days_off`(False), `enforce_vacation`(True). `_COVERAGE_BANDS = ("weekday","weekend","saturday","sunday","holiday")` generates the group key names; `_coerce_settings` holds the §22 inheritance chain for holiday/saturday/sunday (group band ← group weekend ← legacy band ← legacy weekend ← default), which is what keeps an already-saved ward's numbers identical.
- `_BOOL_SETTING_KEYS` (coerced + emitted as true/false). `VALID_CELL_TOKENS`.
- `to_solver_csv(*, group=None)` emits **only active nurses**, including `holidays,` / `saturdays,` / `sundays,` lines. With a group it emits only that group's nurses, maps `coverage_{rn|pnna}_{band}_{shift}` onto the fifteen **solver-facing** key names, and for `pn_na` forces `head_nurse_special_shift,false` (head/deputy are RNs). A `Class` column is emitted only when some active nurse is classed, so legacy CSVs are unchanged and classed wards round-trip.
- **This is the whole seam for staff classes**: `shiftwork.py` and `dataimporter.py` learn nothing about them — each is handed one group's roster under the existing key names.

### `core/solver_runner.py` — the ONLY caller of `shiftwork.Solver`
- `run_schedule(schedule_input, max_time_seconds) -> SolveResult`.
- `SolveResult(solver_status, solved, grid, csv_text, log, request_report)`.
- Runs Solver in an isolated temp dir (chdir; **not concurrency-safe** — worker runs 1 at a time). Reads `output/*Final.csv`, maps rows to `active_nurses()` (index→name). `grid = {days:[...], rows:[{nurse_index,name,cells:[...]}], request_report?}`.
- `_name_request_report()` maps dropped-request indices → names.

### `core/calendar_util.py`
- `month_dates(year, month, extra_holidays) -> {num_days, weekends, saturdays, sundays, holidays:[{day,name}], extra, combined, holiday_days}` (`saturdays`/`sundays` split `weekends` by weekday → `settings["saturdays"/"sundays"]`). Uses `calendar` + the `holidays` library (Thailand, `HOLIDAY_COUNTRY="TH"`). `holiday_days` = public holidays ∪ manual extras → `settings["holidays"]`; `weekends` (Sat/Sun only) → `settings["weekends"]`. **`combined` (the full union) is legacy** — it is what the auto-fill stored into `weekends` before §22 and is kept only so nothing reading it breaks; new code wants `weekends` + `holiday_days`.

### `core/tiers.py`
- `resolve_tier(ward_meta) -> (name, source)` — license key > declared tier > free. Uses `TIER_LIMITS`/`LICENSE_REGISTRY` (no log side effects).
- `tier_view(schedule_input) -> {name, source, max_nurses, nurse_count(active), over_max, max_req_shifts, max_holidays, meetings_enabled, options:[{label,available,detail}]}`. Only meetings is tier-gated; other toggles available on all tiers.

### `core/fairness.py`
- `fairness_stats(grid, weekend_days=None, senior_indices=None) -> dict | None` — pure derivation from a solved `result_grid` (no solver run, works on old jobs). Per-nurse Day/Evening/Night/total/off/**vac_days**/weekend counts (double cells `ช/บ`,`ด/บ` count both slots). `vac` cells (approved leave) are counted as `vac_days`, kept OUT of `off_days` (so working+off+vac == num_days), summed in `totals`, and flagged by `has_vacations`; `off_days` therefore means *discretionary* off, matching the solver. UI shows a Vac column + total only when `has_vacations`; `spread` (min/max/range/mean) computed over NON-senior nurses so day-only head/deputy don't skew the range; `totals` over everyone. Wired into `job_page`+`job_status` via `_fairness_for_job` (reads `job.input.data["settings"]` for weekends + `head_nurse_special_shift`).

### `core/months.py`
- `parse_month_year(ward_meta) -> (year, month) | None` (strings "9"/"09"; month 1..12, year > 0), `THAI_MONTHS`/`EN_MONTHS`.
- `job_state(job)` → draft|running|done|infeasible|timeout|error (+`STATE_LABELS`, `STATE_PILL`); `month_rows(inputs, latest_job_by_input) -> [MonthRow]` — **derived** month view (no Schedule table): groups by `(ward_name.lower(), year, month)`; row = latest input (max created_at,id) + its latest job; undated inputs never merge.

### `core/duplicate.py`
- `next_month_input(si)` — "Next month from this": roster (name/type/active/order) + all settings kept, **every request cell cleared**, month+1 (Dec→Jan+1), `num_days`/`weekends` via `month_dates(y, m, [])`, `extra_holidays=[]`; undated → meta/settings copied unchanged. Pure, no mutation.

### `core/presolve.py` — pre-flight checklist
- `check(si) -> PresolveReport(errors, warnings, passed, checks_total=4)`; `Issue(code, message, days, nurse)`.
- **Errors are sound** (solver guaranteed INFEASIBLE / crash): `tier_nurses`, `senior_meeting_weekend`, `request_transition`, `coverage_day` (exact 1-day CP-SAT relaxation), `coverage_pair` (2-day window, only when a transition rule is on). Only a PROVEN infeasible window blocks.
- Warnings never block: `quota_off`, `quota_shift` (sampled requests treated as unknown), `tier_meetings`, `senior_request`.
- Mirrors the solver's hard rules + importer token parsing (`_TOKEN_SHIFTS`) — see Known failure mode 9. ~0.5 s at 12 nurses × 31 days.
- `_day_band(ctx, d)` mirrors `Solver._rebuild_days`'s holiday > sunday > saturday > weekend > weekday precedence; `_band(s, band, group)` takes a **band name**, not a bool. `ctx["weekends"]` is the union (so the senior rules match the solver) with `ctx["holidays"]` beside it for the band.

### `db/models.py` (SQLAlchemy 2.0)
- `Base`, `JobStatus`(queued/running/succeeded/failed).
- `User`(google_sub, email, name, role), `Ward`, `WardMember`(ward_id,user_id).
- `ScheduleInputRow`(ward_id, created_by, source, `data` JSON = `ScheduleInput.to_dict()`, original_csv).
- `SolveJob`(input_id, ward_id, submitted_by, status, max_time_seconds, solver_status, `result_grid` JSON [holds `request_report`], result_csv, log, error, timestamps).

### `db/session.py`
`engine`, `SessionLocal`, `init_db()`, `make_session_factory(url)` (tests), `make_engine()`, `normalize_db_url()` (postgres:// / postgresql:// → `postgresql+psycopg://`).

### `worker/tasks.py`
- `run_solve_job(job_id, session_factory=None)` — marks running, validates, `run_schedule`, writes status/result/report; **outcome mapping**: solved→succeeded+grid; infeasible/timeout→succeeded, grid None, `solver_status` set; crash→failed+error. Testable without Redis.
- `enqueue_solve(job_id)` — **inline** (`run_solve_job` in-process) if `SOLVE_INLINE`, else RQ enqueue with `job_timeout=WORKER_JOB_TIMEOUT_SECONDS`.

### `worker/run_worker.py` — `python -m webapp.worker.run_worker` (setup_logging + RQ worker on "solves").

### `web/auth.py`
- `auth_is_off()` = `DEV_AUTH_BYPASS or not AUTH_ENABLED` → single shared local user, no OAuth.
- `require_user` (FastAPI dependency; redirects to /login via `AuthRedirect` when logged out), `is_email_allowed`, `get_or_create_default_ward`, `user_ward_ids`, `ensure_ward_access`, `install_auth(app)` (SessionMiddleware https_only + same_site=lax; registers Google client only when auth on), `router` (/login, /auth/callback, /logout).

### `web/app.py` — FastAPI app + routes (design §12)
`/` dashboard (**one row per ward+month** via `months.month_rows`; all inputs + recent jobs in `<details>`), `/inputs/new`, `POST /inputs` (upload detects CSV vs Excel by extension + magic bytes `PK\x03\x04`: xlsx→from_xlsx (source `xlsx_upload`), csv→from_csv; grid→from_grid; size-capped; friendly ValueError), `/inputs/{id}/edit` (grid + constraint selector + tier panel), `POST /inputs/{id}` (**`request.form(max_fields=100_000)`** — grid is many fields), `POST /inputs/{id}/duplicate` (next month → new row source `duplicate` → edit), `POST /inputs/{id}/check` (save form → validate → `presolve.check` → edit page with checklist), `POST /inputs/{id}/solve` (validate → **`presolve.check`: errors block with 400 + checklist, warnings don't** → SolveJob queued → enqueue_solve), `/jobs/{id}`, `/jobs/{id}/status` (HTMX poll fragment), `/jobs/{id}/download` (CSV **with UTF-8 BOM** for Excel/Thai), `/healthz`.
Helpers: `_settings_from_form`, `_nurses_from_form` (reads `active_{r}`, `cell_{r}_{d}`), `_load_input`/`_load_job` (ward-scoped, 404 on foreign), `_render_edit` (passes `tier`, `ward_meta`, `check`), global `@app.exception_handler(Exception)` → logs + 500 page.

### `web/templates/`
`base.html`, `dashboard.html` (month table + Next-month button), `new_input.html`, `edit_input.html` (tier panel, constraint selector incl. min_request_percent + relax_days_off, grid with Active checkbox — **cell name `cell_{{r}}_{{d}}` where `r` is captured nurse index**), `job.html`, `status_fragment.html` (grid + acceptance report; distinguishes INFEASIBLE vs timeout).

### `webapp/tests/`
`test_schedule_input`, `test_solver_runner`, `test_worker_tasks`, `test_web`, `test_tiers`, `test_logging`, `test_db_url`, `test_fairness`, `test_calendar_util`, `test_months`, `test_duplicate`, `test_double_shift`, `test_presolve` (cross-checks errors against the real solver).
Staff classes (§A), off-ward duties (§21) and the holiday band (§22) are covered in `test_solver_runner` (two-group solve, index remap, combined tier cap, budget split, off-ward round-trip), `test_schedule_input` (group CSV emission, coverage inheritance chains, token vocabulary), `test_fairness` (an off-ward cell is a worked shift, not a day off), `test_presolve` (off-ward hard shifts, per-group messages, holiday band vs the real solver) and `test_shiftwork.py` (off-ward exclusivity/coverage/skip-guard, the three bands).
**Windows note:** the run reports ~49 `PermissionError`s in `tearDown` (temp sqlite files held open). Pre-existing harness artifact, **not** failures — test bodies pass. A real failure shows up as some other exception type.
Run: `python3 -m unittest discover -s webapp/tests -p "test_*.py"` (from repo root). Solver-backed tests take ~8s each.

---

## Settings reference (`[settings]` / ScheduleInput.settings)

| Key | Default | Excel | Notes |
|---|---|---|---|
| `num_days` | 31 | yes · `Days in month` | schedule length |
| `weekends` | (per file) | yes · `Rest days` | 1-based in file/ScheduleInput; 0-based in Solver; empty→DEFAULT |
| `holidays` | `[]` | yes · `Special holidays` | special holidays (§22). 1-based in file/ScheduleInput, 0-based in Solver; **empty→empty** (the opposite of `weekends`). Absent key→`[]`, which is what keeps already-saved months on the weekend band |
| `allow_evening_to_night` | false | yes · `Allow Evening→Night` | E→N transition |
| `allow_night_to_day` | false | yes · `Allow Night→Day` | N→D transition |
| `head_nurse_special_shift` | true | yes · `Head-nurse special shift` | senior Day/OFF rule; needs ≥2 active nurses |
| `min_request_percent` | 100 | **no** — web/CSV only | 100 = hard requests; <100 = soft (keep max, floor at %) |
| `relax_days_off` | false | **no** — web/CSV only | if soft: also drop requested days-off (else shifts only) |
| `enforce_vacation` | true | **no** — web/CSV only | `vac` cells: true = leave is hard (never dropped, shown as `vac`); false = droppable like off |
| `enable_meetings` | tier | indirect · via `Plan` | **tier-derived**, not user-editable |
| `coverage_weekday_{day,evening,night}` | 5/3/3 | yes · `Weekday need D / E / N` | min nurses per shift on weekdays |
| `coverage_weekend_{day,evening,night}` | 4/2/2 | yes · `Weekend need D / E / N` | min nurses per shift on weekends |
| `coverage_holiday_{day,evening,night}` | 4/2/2 | yes · `Holiday need D / E / N` | special-holiday minimums (§22); inherit `coverage_weekend_*` when absent |
| `saturdays`, `sundays` | `[]` | yes · `Saturdays` / `Sundays` | 1-based band-selecting day lists (§22); `weekends` stays the union. Empty/absent → those days keep the weekend band. Auto-fill and next-month set them from the calendar |
| `coverage_{saturday,sunday}_{day,evening,night}` | 4/2/2 | yes · `Saturday need` / `Sunday need D / E / N` | inherit `coverage_weekend_*` when absent; per-group `coverage_{rn,pnna}_{saturday,sunday}_*` (card rows `PN/NA saturday need`, `PN/NA sunday need`) |
| `coverage_rn_{weekday,weekend,holiday}_*` | 5/3/3 · 4/2/2 | indirect · inherited | **no dedicated card row**: the general `Weekday/Weekend/Holiday need` rows ARE the RN numbers, through `_coerce_settings` inheritance. `from_xlsx` deliberately emits no `coverage_rn_*` key, or it would freeze that chain |
| `coverage_pnna_{weekday,weekend,holiday}_*` | 5/3/3 · 4/2/2 | yes · `PN/NA … need D / E / N` | per-group minimums; remapped onto the legacy names by `to_solver_csv(group=...)`. The card label contains a `/`, so `from_xlsx` must key labels with `_label_key`, **never** `split("/")[0]` — Known failure mode 13 |
| `extra_holidays` | [] | **no** — web only | manual 1-based holiday days; an *input* to auto-fill, which folds them into `holidays` |

18 group keys + 9 legacy keys = the full coverage vocabulary. The edit page renders
them as ONE table (3 band rows × 3 or 6 columns), and the PN/NA half appears only
once a nurse is classed, so a single-class ward sees 9 fields.

Month auto-fill: `POST /inputs/{id}/autofill` reads `ward_meta.month/year`, calls
`month_dates`, and sets `num_days`, `weekends` (**Sat/Sun only**) and `holidays`
(= Thai public holidays + extra_holidays). Before §22 it stored the whole union into
`weekends`; because the holiday band inherits the weekend band, re-running auto-fill
on an existing ward moves days between the two lists and changes staffing by nothing.
Edit page has month/year + extra-holidays + holidays + saturdays + sundays inputs + the button. Auto-fill also sets `saturdays`/`sundays` (and "next month" recomputes them).

Coverage minimums are read into `Solver.coverage` in `loadReqShifts` and applied
in `_rebuild_days()` → `_constraintMinimumNurse()`. Defaults preserve the old
hard-coded values; lower them for small wards (a weekday at 5 Day + 3 Night needs
≥8 nurses working, disjoint).

## Tiers (`TIER_LIMITS` in dataimporter.py)
free(8 nurses,2 req,2 hol,no mtg) · ward(15,5,5,mtg) · ward+(25) · ward pro(40) · department(unlimited). Resolution: license key → declared `tier` → free.

**`max_holidays` is the per-nurse REQUESTED-DAYS-OFF cap**, not a cap on the
special-holiday list. It feeds `DataImporter.maxDayOff` (`min(maxDayOff, limits['max_holidays'])`)
and `presolve`'s `quota_off` warning. **Nothing caps `settings["holidays"]`** on any
tier — the §22 holiday list is unlimited everywhere. The "2 hol" above means
"2 requested days off per nurse"; read it that way before writing tier copy.

**`max_nurses` is ONE combined ceiling across RN and PN/NA** (confirmed 2026-10-17): PN and NA count, so 8 RN + 6 NA consumes 14. `DataImporter`'s check is per-file and would give each group its own allowance, so `tiers.enforce_max_nurses()` enforces the combined figure before the roster is split.

## Run / deploy
- Local, no auth/Redis: `webapp/run_local.bat` (sets `DEV_AUTH_BYPASS=1`, `SOLVE_INLINE=1`, `SOLVER_MAX_TIME_SECONDS=90`).
- Prod: Railway (`DEPLOY_RAILWAY.md`), `Procfile` (web + worker), `requirements.txt`, `.python-version`=3.12. Postgres via `DATABASE_URL`; Redis via `REDIS_URL`; set `AUTH_ENABLED`/`SESSION_HTTPS_ONLY`.

## Known failure modes (canonical list — skills and agents point here; update ONLY here)
Every spec's Risks section names the ones it touches; every change touching one needs its regression test.
1. **Constraint vs request conflict → silent infeasibility.** A rule restricting which shifts a nurse may work contradicts a CSV `== 1` request → "No solution found". Add a skip-guard in `handleHolidaysAndReq`.
2. **Senior-nurse rules cover BOTH head (index 0) AND deputy (index 1)** — `senior_indices = {HEAD_NURSE_INDEX, DEPUTY_NURSE_INDEX}` (constraint.md §16/§17).
3. **`mtg` days**: nurse forced to Day but EXCLUDED from minimum-coverage counts. The exclusion is now general (one `excluded` set per `(day, shift)`), shared with failure mode 10 — don't re-add a Day-only special case.
4. **`vac` is protected**: assigned internally as `Shift.OFF`, not down-sampled by `maxDayOff`, excluded from discretionary-off fairness (`working + off + vac == num_days`); hardness governed by `enforce_vacation` (default True).
5. **Tier resolution**: `license_key` > declared `tier` > free; only meetings are tier-gated in code, rule toggles are all-tier.
6. **Input contract**: CSV and Excel must round-trip identically through `ScheduleInput.to_solver_csv()`. Changing columns/cell semantics is an escalation.
7. **Half-wired settings**: a setting must pass through every layer of the shiftwork-dev "add a setting" checklist (solver → dataimporter → schedule_input → app form → template → display → tests), or it silently doesn't propagate.
8. **No redundant model structure**: completeness is already enforced by OFF implications + `must_assign`; don't re-force it (old `_assignShifts` = 1,116 redundant vars).
9. **Pre-solve mirror drift**: `webapp/core/presolve.py` re-states the solver's HARD rules and the importer's token parsing. Any change to a hard rule, request hardness, sampling, or token semantics in `shiftwork.py`/`dataimporter.py` must update presolve in the same change, or it raises false blockers. Guarded by `test_presolve.test_no_false_blockers_vs_real_solver` / `test_errors_agree_with_real_solver`.
10. **Off-ward duty (`flt`/`ref`, constraint.md §21) is exclusive, and only `_offward_applied` counts.** The cell forces one shift and zeroes every other shift *including OFF*; it is removed from that one shift's coverage pool but still counted as a worked shift by `distribute()`, because coverage and fairness read the SAME assignment variable — only the coverage *pool* drops the nurse. Head/deputy cells that clash with §16/§17 are skip-guarded, and the exclusion/render maps are built from the surviving cells only, so a dropped cell can never shrink a coverage pool. Never sampled against tier quotas, never tier-gated. A bare `flt`/`ref` is rejected, never defaulted to Day.
11. **`Solver.weekends` means "weekend OR holiday" (§22).** It is deliberately the UNION of `settings["weekends"]` and `settings["holidays"]`, because every rule that treats a day as non-working — §16/§17 senior head/deputy, the §21 off-ward skip-guard, `_build_warm_start`, `presolve`'s senior rules, the fairness weekend column — must see a public holiday exactly as it did when auto-fill merged holidays into `weekends`. **Only the coverage-band lookup separates them** (`_rebuild_days` / `presolve._day_band`, precedence holiday > weekend > weekday). Break the union and head nurses silently start getting scheduled on public holidays in every existing ward. A rule needing calendar weekends only must use `self.weekends - self.holidays` (and likewise `saturdays`/`sundays` are in the union too: the Saturday/Sunday bands follow the same rule, precedence holiday > sunday > saturday > weekend > weekday). Also: the holiday band *inherits* the weekend band at three layers (`_coerce_settings`, `DataImporter`, `Solver.loadReqShifts`) — that inheritance is what makes re-running auto-fill on a saved ward a no-op, so do not replace any of those fallbacks with a constant. Already-saved months are **not** migrated and must not be: their holidays are unrecoverable from `weekends`, an absent `holidays` key means `[]`, and re-deriving them from the `holidays` library at read time would make staffing depend on an installed package version.
12. **Staff-group split happens at `to_solver_csv`, and indices must be remapped.** Two independent solves means each `Solver` numbers its nurses 0..k group-locally; `ScheduleInput.group_indices(group)` maps those back to the GLOBAL `active_nurses()` index that `result_grid.rows[].nurse_index`, `fairness_stats` and the request report all assume. Also: `max_nurses` is one COMBINED ceiling (`tiers.enforce_max_nurses`) because `DataImporter`'s check is per-file; and every time budget must be divided across groups or two serial solves breach `WORKER_JOB_TIMEOUT_SECONDS`. A roster where only some active nurses are classed is rejected by `validate()` — otherwise the unclassed nurse appears in neither solve and silently vanishes.

13. **The Excel SETTINGS card is keyed by LABEL TEXT, and the Thai half is stripped by `_label_key`, not `split("/")[0]`.** Card labels are bilingual (`"English / ไทย"`), so the parser drops a `/`-delimited segment **only when that segment contains Thai characters** (`_THAI_RE`). The old plain `split("/")[0]` truncated `"PN/NA weekday need D / E / N"` to `"pn"`, which matched no branch — so the shipped templates' per-group coverage rows were parsed as nothing and silently inherited the general row instead (measured: card said 3/2/2, solver got 5/3/3). Any new card label must either avoid `/` in its English half or be matched through `_label_key`. Two further invariants: a label row that is **absent** must write no settings key at all, or it freezes the §22 inheritance chain (`coverage_holiday_*` ← weekend, `coverage_rn_*` ← legacy) and changes staffing on re-upload; and `Special holidays` must parse with `_parse_day_list` (empty → `[]`), never `_parse_weekends_1based` (empty → DEFAULT) — failure mode 11. The templates are gitignored business artifacts, so the committed generator `tools/gen_templates.py` plus `webapp/tests/test_excel_templates.py` are the ONLY place template/parser drift is visible; a new cell token added to `constant.py` must be added to the generator's dropdown or that suite fails.

## Gotchas (bugs already fixed — don't reintroduce)
- **Grid cell names**: must use captured nurse-row index (`{% set r = loop.index0 %}`), not `loop.index0` inside the day loop.
- **Form field limit**: grid POST needs `request.form(max_fields=...)` (default 1000 truncates big grids).
- **CSV download**: prepend UTF-8 BOM so Excel renders Thai.
- **Active nurses**: `to_solver_csv` emits active-only; solver output maps back via `active_nurses()` (index alignment).
- **Timeout ≠ infeasible**: `UNKNOWN` = ran out of time; only `INFEASIBLE` is a proven contradiction. UI distinguishes them.
- **`optionHeadNurse`** indexes nurses 0/1 → validate requires ≥2 active nurses when senior rule on.
- **weekends empty string → DEFAULT** (can't express "no weekends" via CSV).

## Design docs
`WEB_MVP_DESIGN.md` (architecture), `DEPLOY_RAILWAY.md` (deploy), `docs/specs/` (one spec per feature; `TEMPLATE.md` is the format — see the lead-engineer skill). Solver dev workflow also in the `shiftwork-dev` skill (partially stale re: `main()` → now `build()/solve()/run()`).
