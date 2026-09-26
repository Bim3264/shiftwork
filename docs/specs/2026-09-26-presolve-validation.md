# Pre-solve validation (pre-flight checklist)
Status: approved · Size: M · Date: 2026-09-26 · Owner: lead-engineer

## 1. Objective & interpretation
Before a solve is queued, run fast checks (≈0.5 s for 12 nurses × 31 days) and show a checklist
(wireframe 1i). **Errors block solving and are sound**: raised only when the real solver is guaranteed to be
INFEASIBLE or to crash. **Warnings never block** (quota down-sampling, ignored mtg, ignored head/deputy requests).
Users can also run the checklist on demand ("ตรวจสอบ / Check") without solving.

## 2. Current state
- `app.py` · `solve_input()` l.340–360: only `schedule_input.validate()` (structural) before creating the job.
- `ScheduleInput.validate()` (`core/schedule_input.py` l.450): num_days, ≥1 active nurse, head rule needs ≥2, token validity.
- Tier cap is enforced only inside the solve (`DataImporter.__init__` raises → job `failed`).
- `edit_input.html` l.257–259 gap-note says pre-solve validation isn't wired; l.303 Solve form; l.72 `{% if error %}`.
- `app.py` · `autofill_dates()` l.313: pattern for a secondary submit that saves the form then re-renders.
- **Kernel already written by the lead**: `webapp/core/presolve.py` — `check(si) -> PresolveReport`
  (`errors`, `warnings: list[Issue]`, `passed: list[str]`, `checks_total`, `checks_passed`, `ok`);
  `Issue(code, message, days, nurse)`. Codes: errors `tier_nurses | senior_meeting_weekend | request_transition |
  coverage_day | coverage_pair`; warnings `quota_off | quota_shift | tier_meetings | senior_request`.
  Do not change its logic; report a suspected kernel bug instead.

## 3. Behaviour spec
- `POST /inputs/{id}/check`: parse form exactly like autofill (`_schedule_input_from_form`), save `row.data`,
  then `validate()` (ValueError → render edit with `error`, 400) then `presolve.check()` → render edit 200 with the checklist.
- `POST /inputs/{id}/solve`: after `validate()` passes, run `check()`. If `not report.ok` → render edit, status 400,
  `error="Fix the blockers below before solving."`, checklist shown, **no job created**. Otherwise unchanged (warnings don't block).
- Checklist card (only when a report is passed to the template), placed right under the error div (l.72):
  title `พร้อมจัดตารางหรือยัง? / Ready to solve?`, summary `{checks_passed} of {checks_total} checks passed`;
  then each error (`✗`, class `error`), each warning (`!`, class `warn`), each passed line (`✓`). Issue text = `issue.message`.
- Edit form gets a button `ตรวจสอบ / Check` next to Save, `formaction="/inputs/{{ row.id }}/check"`.
- Replace gap-note l.257–259 with nothing (keep the footer off-count JS).
- Must NOT change: solver, importer, stored data format, job lifecycle.

## 4. Design
- `app.py`: `from webapp.core import presolve`; `_render_edit(..., check=None)` passes `"check": check` to the template;
  new route `check_input` next to `autofill_dates`; edit `solve_input`.
- `edit_input.html`: checklist card + Check button; remove gap-note.
- `CODEBASE.md`: add presolve to the file map + "hard rule changed? update presolve.py mirror" to Known failure modes list (lead does this).

## 5. Work packages
| WP | Owner | Model | Files | Depends on | Effort |
|----|-------|-------|-------|-----------|--------|
| 1+2 | web-specialist + test-specialist (one agent) | sonnet | `app.py` (check route, solve_input, _render_edit), `edit_input.html`, `test_web.py` (new/adjusted per §6), `webapp/tests/test_presolve.py` (new) | WP0 in parallel-build plan | M |

Built in parallel — see `2026-09-26-parallel-build.md` for worktree and insertion points.

## 6. Acceptance tests
`webapp/tests/test_presolve.py` (build `ScheduleInput.from_grid(ward_meta, settings, nurses)` directly; `tier: ward` unless stated):
- `test_feasible_fixture_has_no_errors`: every `input/Schedule *.csv` with `ward_meta["tier"]="department"` injected → `report.errors == []`.
- **`test_no_false_blockers_vs_real_solver`**: for 3 small feasible inputs (≤6 nurses, 5–7 days, coverage 1–2) run
  `solver_runner.run_schedule(si, 20)` → solved, AND `check(si).ok`. Guards soundness direction.
- **`test_errors_agree_with_real_solver`**: each crafted error case below → `run_schedule(si, 20).solver_status == "INFEASIBLE"`
  (skip the tier case: solver crashes, assert `ValueError` from `run_schedule`).
- `test_tier_nurse_cap`: 9 active nurses, no tier → error `tier_nurses`; 9 with one inactive → no such error.
- `test_coverage_day_error_names_day`: 4 nurses, coverage weekday D/E/N = 2/1/1, 3 nurses `off` on day 2 (head rule off, 7 days, min_request_percent 100) → one `coverage_day` error with `days == [2]`, message contains "Day 2".
- `test_relaxable_offs_do_not_block`: same but `min_request_percent 80`, `relax_days_off True` → no errors.
- `test_vacation_hardness_follows_setting`: vac instead of off → error with `enforce_vacation True`; none with `enforce_vacation False` + `min_request_percent 80`.
- `test_request_transition_night_then_day`: nurse ด day 3, ช day 4, allow_night_to_day False → `request_transition` days [3,4]; with True → none.
- `test_coverage_pair_error`: 3 nurses, head rule off, 2-day month, `weekends` = [] (set `si.settings["weekends"]=[]` after from_grid), coverage weekday D/E/N = 0/2/2 → exactly one error, `coverage_pair` days [1,2] (each day alone is coverable via บ+ด doubles; บ day 1 → ด day 2 is illegal). With `allow_evening_to_night True` → no errors. (Lead verified: real solver INFEASIBLE / solved respectively.)
- `test_senior_meeting_on_weekend`: tier ward, head (index 0) `mtg` on a weekend day → `senior_meeting_weekend`.
- `test_quota_warnings_not_errors`: tier free, nurse with 3 `off` → warning `quota_off`, not in errors; its offs are NOT treated as hard (coverage that would fail with them hard does not error).
- `test_mtg_on_free_tier_warns`: → `tier_meetings` warning.
- `test_senior_weekend_request_warns`: head asks ช on a weekend → `senior_request` warning, no error.
- `test_checklist_counts`: clean input → `checks_passed == checks_total == 4`.
`test_web.py` `PresolveRouteTests(WebTestBase)`:
- `test_solve_blocked_by_presolve_error`: input whose coverage can't be met (e.g. `_SAMPLE_CSV`: 3 nurses vs D=5) → 400, text contains "Ready to solve", "Day 1", no SolveJob.
- `test_check_route_saves_and_renders_checklist`: POST form to `/inputs/{id}/check` → 200, "checks passed" in text, row data saved.
- `test_solve_with_warnings_only_proceeds`: feasible input (tier free, one nurse 3 offs, coverage 1/1/1, 4 nurses) → 303 to job.
- **Adjust** `test_solve_creates_job_and_redirects_to_job_page`: it uses `_SAMPLE_CSV` (3 nurses, default coverage 5/3/3 — certainly infeasible), so it would now be blocked. Give it a feasible input instead (add module constant `_FEASIBLE_CSV`: `_SAMPLE_CSV` + settings `coverage_weekday_day,1` … all six coverage keys `1`, allow_night_to_day true). Keep every assertion. This is a legitimate behaviour change, not a weakening.
Suites: `python3 -m unittest discover -s webapp/tests -p "test_*.py"` and `python3 test_shiftwork.py`.

## 7. Risks
- Known failure modes touched: **1 (constraint vs request conflict)** — presolve mirrors the senior skip-guard; **2 (senior rules cover index 0 AND 1)**; **3 (mtg excluded from coverage)**; **4 (vac protected / enforce_vacation)**; **5 (tier resolution)** — uses `tiers.resolve_tier`, same precedence.
- New failure mode (lead adds to CODEBASE.md): presolve duplicates the hard rules; a hard-rule change in shiftwork.py/dataimporter.py must update `presolve.py`, else false blockers. Guarded by `test_no_false_blockers_vs_real_solver`.
- Known importer bug mirrored, not fixed: `ช/บ` forces only EVENING and `ด/บ` forces nothing (DataImporter merge collapses same-day entries). Tracked separately.
- Performance: ~0.5 s per check at 12×31; windows use 2 s time cap each and only a PROVEN infeasibility blocks.

## 8. Do NOT
- Modify `presolve.py` logic, `shiftwork.py`, `dataimporter.py`. Make warnings block. Weaken tests (the one adjustment above is specified).

## 9. STOP if
- Any crafted error case is NOT infeasible in the real solver (that's a kernel soundness bug — report it with the input).
- A feasible fixture produces an error.

## 10. Report format / 11. Done log — as TEMPLATE.
- [ ] Tests green · [ ] CODEBASE.md + FEATURE-GAPS.md updated · [ ] Status → done
