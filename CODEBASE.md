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
- `build()` → `constraints()` + `distribute()` + `_build_warm_start()`.
- `solve(max_time_seconds=120)` → configure `CpSolver`, `Solve`, `_build_request_report()`, `_write_schedule()`; returns CP-SAT status.
- `run(max_time_seconds=120)` = `build()` then `solve()`.

`constraints()` orchestrates named rule methods:
- `handleHolidaysAndReq()` — applies requested shifts/holidays. **Hard** if `MIN_REQUEST_PERCENT==100`; else **soft** (reified with a `sat` BoolVar per request, tracked in `self.request_soft`, plus a floor `sum(sat) >= ceil(pct*total)`). Senior-nurse conflicting requests are dropped here. Meetings always hard.
- `_add_daily_shift_rules()` — must-assign, OFF exclusivity, per-day cap (`MAX_SHIFT_PER_DAY`), Night+Day ban, new-nurse Evening+Night ban.
- `_add_shift_transition_rules()` — E→N and N→D across days (flag-gated).
- `_add_double_shift_tracking()` — double-shift indicators (feed objective).
- `_constraintMinimumNurse()` — per-day coverage minimums (meeting nurses excluded from Day count).
- `optionHeadNurse()` — head(0)+deputy(1): weekdays Day/OFF, weekends OFF. **Assumes ≥2 nurses when on.**

`distribute()` — objective `Minimize(10*shift_imbalance + 10*holiday_imbalance + double_shifts [+ 100000*dropped_requests])`. The huge weight makes keeping requests dominate fairness.

`_write_schedule()` — renders solved assignments to a transposed grid, writes `output/{label} Final.csv` (`utf-8-sig`). Returns label.
`_build_request_report()` — fills `self.request_report = {total, accepted, dropped:[{nurse_index,day,shift,kind}], percent}` (None unless soft mode).

Key attributes/flags: `MAX_SHIFT_PER_DAY`, `ALLOW_SHIFT_E_N/_N_D`, `HEAD_NURSE_SPEACIAL_SHIFT`, `HEAD_NURSE_INDEX=0`, `DEPUTY_NURSE_INDEX=1`, `enable_meetings`, `MIN_REQUEST_PERCENT`, `RELAX_DAYS_OFF`, `new_nurse_indices`, `meeting_days`, `request_soft`, `request_report`, `status`.
Solver params in `solve()`: `num_search_workers=os.cpu_count()`, `linearization_level=2`, `max_time_in_seconds`.
`__main__` processes every file in `input/` via `Solver(...).run()`.

### `dataimporter.py`
- `DataImporter(filename, maxDayOff, maxReqShift)` → `.transform()`. Parses sectioned CSV `[ward]`/`[settings]`/`[schedule]` (legacy = grid only). Produces `reqShifts` (dict per nurse, **sampled down** to tier `max_req_shifts`), `reqDayOff`, `reqMeetings`, `newNurseIndices`. Enforces tier `max_nurses` (raises ValueError if exceeded). `.settings` dict, `.ward_info`, `.tier`.
- `TIER_LIMITS` / `LICENSE_REGISTRY` — tier definitions + Phase-0 license keys.
- Static parsers: `_parse_sections`, `_parse_kv_section`, `_parse_weekends` (1-based→0-based; **empty string → DEFAULT weekends, not empty**).
- `SettingLoader` (legacy defaults), `FileLoader` (scans a folder).

### `constant.py`
- `Shift` enum: `DAY/EVENING/NIGHT/OFF`.
- `LocaleShift`: Thai symbols — DAY `ช`, EVENING `บ`, NIGHT `ด`, DAY_EVENING `ช/บ`, NIGHT_EVENING `ด/บ`, OFF `off` Plus VACATION `vac` (approved leave; assigned as OFF, rendered distinctly).

### `gui.py` — legacy Tkinter desktop app (runs `Solver` in a thread). Not used by the web app.
### `test_shiftwork.py` — solver lifecycle + per-rule (force-illegal→infeasible) tests.

---

## Web app (`webapp/`)

### `config.py` — all env-driven. Key vars:
`SOLVER_MAX_TIME_SECONDS`(120), `WORKER_JOB_TIMEOUT_SECONDS`(180) [invariant asserted: solver < worker], `DATABASE_URL`(sqlite default), `REDIS_URL`, `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`, `SESSION_HTTPS_ONLY`(False), `AUTH_ENABLED`(True), `DEV_AUTH_BYPASS`(False), `DEV_USER_EMAIL`, `SOLVE_INLINE`(False), `ALLOWED_EMAILS`(set), `ALLOWED_DOMAIN`, `MAX_UPLOAD_BYTES`(1 MiB).

### `logging_setup.py`
`setup_logging(path=None)` — lazy ERROR `FileHandler` to `logs/shiftwork_<timestamp>.log` (created only on first error) + installs `sys.excepthook`. `log_path()`. Called from `web/app.py` import and `worker/run_worker.py`.

### `core/schedule_input.py` — the canonical model
- `Nurse(name, type="", shifts={day:token}, active=True)`. `active=False` = disabled (kept, not scheduled).
- `ScheduleInput(ward_meta, settings, nurses)`.
  - `from_csv(text)` (reuses DataImporter parsers), `from_xlsx(bytes)` (reads the Excel roster template's Roster sheet directly into `from_grid` — label-driven cards + day-number-header detection; needs openpyxl), `from_grid(ward_meta, settings, nurses)`, `to_solver_csv()`, `to_dict()`/`from_dict()`, `validate()` (raises ValueError; counts **active** nurses; needs ≥2 active if senior rule on), `active_nurses()`.
- `_DEFAULT_SETTINGS` keys: `num_days`, `weekends`(1-based list), `allow_evening_to_night`, `allow_night_to_day`, `head_nurse_special_shift`, `min_request_percent`(100), `relax_days_off`(False), `enforce_vacation`(True).
- `_BOOL_SETTING_KEYS` (coerced + emitted as true/false). `VALID_CELL_TOKENS`.
- `to_solver_csv()` emits **only active nurses**.

### `core/solver_runner.py` — the ONLY caller of `shiftwork.Solver`
- `run_schedule(schedule_input, max_time_seconds) -> SolveResult`.
- `SolveResult(solver_status, solved, grid, csv_text, log, request_report)`.
- Runs Solver in an isolated temp dir (chdir; **not concurrency-safe** — worker runs 1 at a time). Reads `output/*Final.csv`, maps rows to `active_nurses()` (index→name). `grid = {days:[...], rows:[{nurse_index,name,cells:[...]}], request_report?}`.
- `_name_request_report()` maps dropped-request indices → names.

### `core/calendar_util.py`
- `month_dates(year, month, extra_holidays) -> {num_days, weekends, holidays:[{day,name}], extra, combined}`. Uses `calendar` + the `holidays` library (Thailand, `HOLIDAY_COUNTRY="TH"`). `combined` = weekends ∪ public holidays ∪ manual extras — stored into `settings["weekends"]` by the auto-fill (holidays act like weekends).

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
`test_schedule_input`, `test_solver_runner`, `test_worker_tasks`, `test_web`, `test_tiers`, `test_logging`, `test_db_url`, `test_fairness`, `test_calendar_util`, `test_months`, `test_duplicate`, `test_presolve` (cross-checks errors against the real solver).
Run: `python3 -m unittest discover -s webapp/tests -p "test_*.py"` (from repo root). Solver-backed tests take ~8s each.

---

## Settings reference (`[settings]` / ScheduleInput.settings)

| Key | Default | Notes |
|---|---|---|
| `num_days` | 31 | schedule length |
| `weekends` | (per file) | 1-based in file/ScheduleInput; 0-based in Solver; empty→DEFAULT |
| `allow_evening_to_night` | false | E→N transition |
| `allow_night_to_day` | false | N→D transition |
| `head_nurse_special_shift` | true | senior Day/OFF rule; needs ≥2 active nurses |
| `min_request_percent` | 100 | 100 = hard requests; <100 = soft (keep max, floor at %) |
| `relax_days_off` | false | if soft: also drop requested days-off (else shifts only) |
| `enforce_vacation` | true | `vac` cells: true = leave is hard (never dropped, shown as `vac`); false = droppable like off |
| `enable_meetings` | tier | **tier-derived**, not user-editable |
| `coverage_weekday_{day,evening,night}` | 5/3/3 | min nurses per shift on weekdays |
| `coverage_weekend_{day,evening,night}` | 4/2/2 | min nurses per shift on weekends |
| `extra_holidays` | [] | manual 1-based holiday days; merged into `weekends` by auto-fill |

Month auto-fill: `POST /inputs/{id}/autofill` reads `ward_meta.month/year`, calls
`month_dates`, and sets `num_days` + `weekends` (= Sat/Sun + Thai public holidays
+ extra_holidays). Edit page has month/year + extra-holidays inputs + the button.

Coverage minimums are read into `Solver.coverage` in `loadReqShifts` and applied
in `_rebuild_days()` → `_constraintMinimumNurse()`. Defaults preserve the old
hard-coded values; lower them for small wards (a weekday at 5 Day + 3 Night needs
≥8 nurses working, disjoint).

## Tiers (`TIER_LIMITS` in dataimporter.py)
free(8 nurses,2 req,2 hol,no mtg) · ward(15,5,5,mtg) · ward+(25) · ward pro(40) · department(unlimited). Resolution: license key → declared `tier` → free.

## Run / deploy
- Local, no auth/Redis: `webapp/run_local.bat` (sets `DEV_AUTH_BYPASS=1`, `SOLVE_INLINE=1`, `SOLVER_MAX_TIME_SECONDS=90`).
- Prod: Railway (`DEPLOY_RAILWAY.md`), `Procfile` (web + worker), `requirements.txt`, `.python-version`=3.12. Postgres via `DATABASE_URL`; Redis via `REDIS_URL`; set `AUTH_ENABLED`/`SESSION_HTTPS_ONLY`.

## Known failure modes (canonical list — skills and agents point here; update ONLY here)
Every spec's Risks section names the ones it touches; every change touching one needs its regression test.
1. **Constraint vs request conflict → silent infeasibility.** A rule restricting which shifts a nurse may work contradicts a CSV `== 1` request → "No solution found". Add a skip-guard in `handleHolidaysAndReq`.
2. **Senior-nurse rules cover BOTH head (index 0) AND deputy (index 1)** — `senior_indices = {HEAD_NURSE_INDEX, DEPUTY_NURSE_INDEX}` (constraint.md §16/§17).
3. **`mtg` days**: nurse forced to Day but EXCLUDED from minimum-coverage counts.
4. **`vac` is protected**: assigned internally as `Shift.OFF`, not down-sampled by `maxDayOff`, excluded from discretionary-off fairness (`working + off + vac == num_days`); hardness governed by `enforce_vacation` (default True).
5. **Tier resolution**: `license_key` > declared `tier` > free; only meetings are tier-gated in code, rule toggles are all-tier.
6. **Input contract**: CSV and Excel must round-trip identically through `ScheduleInput.to_solver_csv()`. Changing columns/cell semantics is an escalation.
7. **Half-wired settings**: a setting must pass through every layer of the shiftwork-dev "add a setting" checklist (solver → dataimporter → schedule_input → app form → template → display → tests), or it silently doesn't propagate.
8. **No redundant model structure**: completeness is already enforced by OFF implications + `must_assign`; don't re-force it (old `_assignShifts` = 1,116 redundant vars).
9. **Pre-solve mirror drift**: `webapp/core/presolve.py` re-states the solver's HARD rules and the importer's token parsing. Any change to a hard rule, request hardness, sampling, or token semantics in `shiftwork.py`/`dataimporter.py` must update presolve in the same change, or it raises false blockers. Guarded by `test_presolve.test_no_false_blockers_vs_real_solver` / `test_errors_agree_with_real_solver`.

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
