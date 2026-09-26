# Month-centric dashboard
Status: approved · Size: M · Date: 2026-09-26 · Owner: lead-engineer

## 1. Objective & interpretation
Dashboard shows **one row per (ward, month)** with a status (wireframe 1a), instead of two flat lists.
Derived view over existing rows — **no DB/schema change** (a `Schedule` table is deferred until
requests/publish need persisted month state). Month/year come from `ward_meta.month/year`.
A month's row represents its **latest input** (the working version) and that input's latest job.

## 2. Current state
- `webapp/web/app.py` · `dashboard()` (l.175–217): loads 20 latest inputs + 20 latest jobs; resolves ward names per job.
- `webapp/web/templates/dashboard.html`: gap-note about 1a + "Recent inputs" table + "Recent jobs" table.
- `ward_meta` values are strings (`month`,`year` set from the edit form, `app.py:_schedule_input_from_form` l.134–137).
- Job terminal states (`webapp/worker/tasks.py` l.9–12): succeeded+OPTIMAL/FEASIBLE = solved;
  succeeded+INFEASIBLE = proven infeasible; succeeded+other (UNKNOWN) = timed out; failed = crash.
- Soft-request summary: `job.result_grid["request_report"]` = `{"total", "accepted", ...}` (`core/solver_runner.py:_name_request_report`).
- Pills: `base.html` l.43–47 classes `done | draft | fail | run`.

## 3. Behaviour spec
- Group key: `(ward_name.strip().lower(), year, month)`; `year`/`month` = `int(str(v).strip())`, month valid iff 1..12, year valid iff > 0.
  Inputs with missing/invalid month or year are **undated**: each is its own row (never merged), label `ยังไม่ระบุเดือน / No month set`.
- Row fields: label (`<Thai month> <year> / <English month> <year>`), ward_name, hospital, active nurse count,
  versions (= inputs in group), status, requests (`"accepted/total"` from latest job's `request_report`, else `—`),
  links: Edit (latest input), View (latest job, if any).
- Latest input = max `(created_at, id)`. Latest job of an input = max `(created_at, id)`.
- Status of latest job: none → `draft`; queued/running → `running`; failed → `error`;
  succeeded & solver_status in {OPTIMAL, FEASIBLE} → `done`; succeeded & INFEASIBLE → `infeasible`; succeeded & anything else → `timeout`.
  Labels/pills: draft `ร่าง / Draft`·draft · running `กำลังจัด / Running`·run · done `เสร็จ / Done`·done ·
  infeasible `ไม่สำเร็จ / Infeasible`·fail · timeout `หมดเวลา / Timed out`·draft · error `ผิดพลาด / Error`·fail.
- Order: dated rows by (year, month) desc then ward_name asc; undated rows after, newest input first.
- Below the month table: `<details>` "ข้อมูลทั้งหมด / All inputs (N)" (the old inputs table, all versions) and
  `<details>` "Recent jobs" (old jobs table, 20 latest). Remove the 1a gap-note.
- Empty state: "No schedules yet. Create one to get started." + New button.
- Must NOT change: any other route, models, stored data.

## 4. Design
- NEW `webapp/core/months.py`:
  ```python
  THAI_MONTHS: list[str]   # index 1..12, e.g. [ "", "มกราคม", ..., "ธันวาคม" ]
  def parse_month_year(ward_meta: dict) -> tuple[int, int] | None
  def job_state(job) -> str                     # draft|running|done|infeasible|timeout|error ; job may be None
  @dataclass
  class MonthRow:
      label: str; ward_name: str; hospital: str
      year: int | None; month: int | None
      input_id: int; versions: int; nurse_count: int
      status: str; job_id: int | None; requests: str | None
  def month_rows(inputs, latest_job_by_input: dict[int, "SolveJob"]) -> list[MonthRow]
  ```
  Pure: takes ORM rows (duck-typed: `.id .created_at .data`, job `.id .status .solver_status .result_grid`), no DB access.
  `nurse_count` = nurses in `data["nurses"]` with `active` true (default true).
- `webapp/web/app.py` · `dashboard()`: query ALL inputs of `ward_ids` (order created_at desc, `.limit(500)`); query jobs
  `where input_id in ids` order `(created_at desc, id desc)`, keep first per input → dict; `rows = month_rows(...)`.
  Keep the existing `jobs` (20 latest) + `job_wards` computation for the Recent-jobs details. Pass `months=rows`,
  `inputs` (all), `jobs`, `job_wards`, plus `STATE_LABELS`/`STATE_PILL` dicts from months.py.
- `dashboard.html`: month table first; existing two tables moved inside `<details>`.
- Pattern to copy: route/query style `app.py:175`; template table `dashboard.html:17–36`.

## 5. Work packages
| WP | Owner | Model | Files | Depends on | Effort |
|----|-------|-------|-------|-----------|--------|
| 1 | web-specialist | sonnet | `webapp/core/months.py`, `webapp/web/app.py` (dashboard only), `dashboard.html`, `webapp/tests/test_months.py`, `webapp/tests/test_web.py` (new tests only) | — | M |
Built in parallel with F2/F3 — see `2026-09-26-parallel-build.md` for file ownership, worktree and test placement (it overrides this table).

## 6. Acceptance tests
`webapp/tests/test_months.py` (pure, fake objects via `types.SimpleNamespace`):
- `test_groups_versions_of_same_month`: two inputs ward "4B", month "9", year "2026" (ids 1,2) + one ward "4B" month "8" → 2 rows; Sep row `input_id==2`, `versions==2`; Sep sorted before Aug.
- `test_ward_name_case_insensitive_grouping`: "Ward 4B" vs "ward 4b " same month → 1 row.
- `test_undated_inputs_never_merge`: two inputs without month → 2 rows, both after dated rows, label contains "No month set".
- `test_invalid_month_is_undated`: month "13" → undated.
- `test_job_state_mapping`: None→draft; queued, running→running; failed→error; succeeded+OPTIMAL→done; +FEASIBLE→done; +INFEASIBLE→infeasible; +UNKNOWN→timeout.
- `test_requests_from_report`: latest job result_grid `{"request_report":{"total":45,"accepted":42}}` → `"42/45"`; no report → None.
- `test_status_uses_latest_input_only`: old version has a done job, newest version has none → row status `draft`.
- `test_nurse_count_counts_active_only`: 3 nurses, one `active: False` → 2.
`webapp/tests/test_web.py` new class `MonthDashboardTests(WebTestBase)`:
- `test_dashboard_one_row_per_month`: two inputs with `ward_meta` month 9 / year 2026 (use `_SAMPLE_CSV` + `[ward]` month,year lines) → response contains "กันยายน 2026" exactly once in the month table and "2 versions"-style count (assert text `2` in versions cell via a `data-versions="2"` attribute on the row).
- `test_dashboard_status_pill_infeasible`: input + job succeeded/INFEASIBLE → text "ไม่สำเร็จ / Infeasible".
- `test_dashboard_links_next_month`: month row contains a POST form to `/inputs/{input_id}/duplicate` (route itself is F2's).
- Existing `test_dashboard_*` stay unmodified and green.
Suites: `python3 -m unittest discover -s webapp/tests -p "test_*.py"` and `python3 test_shiftwork.py`.

## 7. Risks
- Known failure modes touched: none (no solver/input-contract change).
- Scale: 500-input cap on the dashboard query; fine for MVP. Lead verifies no N+1 lazy loads in template (all data precomputed).
- Buddhist-era years (2569) sort correctly but label shows as given — acceptable.

## 8. Do NOT
- Add tables/columns/migrations. Touch routes other than `dashboard()`. Add dependencies.
- Rewrite or weaken existing tests.

## 9. STOP and report instead of guessing if
- A cited symbol/line differs from §2. The existing dashboard tests can't stay green unmodified.

## 10. Report format
Files changed · test commands + pass/fail counts · deviations + why · open questions. No diffs.

## 11. Done / deviations log
- [ ] Acceptance tests + suites green (run by the lead) · [ ] CODEBASE.md + FEATURE-GAPS.md updated · [ ] Status → done
