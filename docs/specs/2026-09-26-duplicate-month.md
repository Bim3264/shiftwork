# Duplicate last month ("Next month from this")
Status: done · Size: S · Date: 2026-09-26 · Owner: lead-engineer

## 1. Objective & interpretation
One click creates next month's input from an existing one (wireframe 1f): same ward info, roster
(names, types, active flags, order) and all settings/coverage; **requests cleared** (every cell, incl. off/vac/mtg);
month advanced and dates recomputed. Lands on the edit page of the new input.

## 2. Current state
- `app.py` · `autofill_dates()` l.313–337: month→`month_dates(year, month, extra)` → sets `num_days`, `weekends=md["combined"]`.
- `webapp/core/calendar_util.py` · `month_dates(year, month, extra_holidays=None) -> {num_days, weekends, holidays, extra, combined}`.
- `app.py` · `create_input()` l.284–292: how a `ScheduleInputRow` is created (ward_id, created_by, source, data, original_csv).
- `ScheduleInputRow.source` is `String(32)`; existing values `csv_upload | xlsx_upload | grid`.

## 3. Behaviour spec
- `next_month_input(si) -> ScheduleInput` (pure):
  - dated (per `months.parse_month_year`): month+1, Dec→Jan with year+1; `ward_meta["month"]=str(m)`, `["year"]=str(y)`;
    `settings` deep-copied, then `num_days`, `weekends = month_dates(y, m, [])["combined"]`, `extra_holidays = []`.
  - undated: `ward_meta` and `settings` copied unchanged (user sets the month, then auto-fills).
  - `nurses`: same order, `Nurse(name, type, shifts={}, active)`.
  - All other `ward_meta` keys (ward_name, hospital, tier, license_key…) copied as-is. Input `si` not mutated.
- `POST /inputs/{id}/duplicate` (auth, ward-scoped via `_load_input` → 404 for other wards): new row
  `source="duplicate"`, `ward_id=row.ward_id`, `created_by=user.id`, `data=new.to_dict()`,
  `original_csv=new.to_solver_csv()`; 303 → `/inputs/{new_id}/edit`. No job created. Source row untouched.
- Buttons (POST forms): dashboard month row action `เดือนถัดไป / Next month` (every row, targets `input_id`);
  edit page header card `ทำเดือนถัดไป / Next month from this`.
- A target month that already exists simply becomes a new version of it (dashboard groups it).

## 4. Design
- NEW `webapp/core/duplicate.py`: `def next_month_input(si: ScheduleInput) -> ScheduleInput` (uses `months.parse_month_year`, `calendar_util.month_dates`).
- `webapp/web/app.py`: add route `duplicate_input(input_id, request, user=Depends(require_user), db=Depends(get_db))`
  after `autofill_dates`; copy the row-creation pattern from `create_input` l.284–292.
- `dashboard.html` (month row action), `edit_input.html` (header card l.52–70, add a small `<form method="post">` button).

## 5. Work packages
| WP | Owner | Model | Files | Depends on | Effort |
|----|-------|-------|-------|-----------|--------|
| 1 | web-specialist | sonnet | `duplicate.py` (new), `app.py` (new route only), `edit_input.html` (header card only), `test_duplicate.py`, `test_web.py` (new tests only) | WP0 in parallel-build plan | S |

Built in parallel — `2026-09-26-parallel-build.md` overrides: `next_month_input` lives in `webapp/core/duplicate.py`; the dashboard button + its test belong to F1.

## 6. Acceptance tests
`webapp/tests/test_duplicate.py`:
- `test_next_month_advances_and_recomputes_dates`: month "9"/year "2026", settings num_days 30, extra_holidays [3] →
  month "10", year "2026", num_days 31, weekends == `month_dates(2026,10,[])["combined"]`, extra_holidays [].
- `test_next_month_rolls_year`: month "12"/"2026" → "1"/"2027", num_days 31.
- `test_next_month_clears_requests_keeps_roster`: nurses A(ช on 1, active), B(type new, vac on 2, active False) →
  names/types/active/order preserved, all `shifts == {}`.
- `test_next_month_keeps_settings`: coverage_weekday_day 7, min_request_percent 80, head_nurse_special_shift False carried over.
- `test_next_month_undated_copies_unchanged`: no month → ward_meta and num_days/weekends identical, shifts cleared.
- `test_next_month_does_not_mutate_source`.
`test_web.py` `DuplicateMonthTests(WebTestBase)`:
- `test_duplicate_creates_next_month_and_redirects`: POST → 303 to `/inputs/<new>/edit`; 2 rows; new `source=="duplicate"`, month advanced; source row data unchanged.
- `test_duplicate_other_ward_404`: row in another ward → 404, no new row.
- `test_edit_shows_next_month_button`: edit page contains `/inputs/{id}/duplicate`. (Dashboard button test is F1's.)

## 7. Risks
- Known failure modes touched: **6 (input contract)** — new rows must round-trip: assert `ScheduleInput.from_dict(new.data).to_solver_csv()` parses (covered by using `to_dict`/`to_solver_csv` only).
- Buddhist-era years: `month_dates` computes weekdays for the literal year (same limitation as auto-fill).

## 8. Do NOT
- Copy any request cell. Change `create_input`/`autofill_dates`. Add schema. Weaken tests.

## 9. STOP if
- `month_dates` or `parse_month_year` signature differs; anything reads `source` expecting only the three old values.

## 10. Report format / 11. Done log — as TEMPLATE.
- [x] Tests green · [x] CODEBASE.md + FEATURE-GAPS.md updated · [x] Status → done
