# Parallel build plan — month dashboard · duplicate month · pre-solve
Status: approved (specs) · build not started · Date: 2026-09-26 · Owner: lead-engineer

All three features are built **at the same time**, one agent per feature, each in its own git worktree
and branch, then merged by the lead. The three specs stay the source of truth; this file only fixes the
shared contracts, file ownership and merge order that make parallel work safe.

## 0. Lead pre-work (before any agent starts) — WP0
1. Commit the 3 specs, this plan, and `webapp/core/presolve.py` (kernel, unused until F3 wires it) to `main`.
2. Create `webapp/core/months.py` on `main` with the **shared contract only**:
   ```python
   THAI_MONTHS: list[str]          # ["", "มกราคม", ..., "ธันวาคม"]
   EN_MONTHS: list[str]            # ["", "January", ..., "December"]
   def parse_month_year(ward_meta: dict) -> tuple[int, int] | None   # (year, month) or None
   ```
   plus its 3 unit tests in `webapp/tests/test_months.py` (valid "9"/"2026", "09", invalid "13", missing, non-numeric).
3. Add `.worktrees/` to `.gitignore`; create worktrees inside the repo (the only folder the agents can reach):
   `git worktree add .worktrees/dash -b feat/month-dashboard`,
   `git worktree add .worktrees/dup -b feat/duplicate-month`,
   `git worktree add .worktrees/presolve -b feat/presolve`.
4. Fixed route contract: `POST /inputs/{input_id}/duplicate` (owned by F2; F1 may link to it before it exists).

## 1. File ownership (no file edited by two agents except where noted)
| File | F1 dashboard | F2 duplicate | F3 pre-solve |
|---|---|---|---|
| `webapp/core/months.py` | **owns** (adds `job_state`, `MonthRow`, `month_rows`, label/pill dicts) | — (imports `parse_month_year` only) | — |
| `webapp/core/duplicate.py` (new) | — | **owns** `next_month_input` | — |
| `webapp/core/presolve.py` | — | — | read-only (kernel) |
| `webapp/web/app.py` | `dashboard()` only | new `duplicate_input` route, placed **after `autofill_dates`** | `_render_edit` (+`check=None`), `solve_input`, new `check_input` route placed **after `solve_input`** |
| `dashboard.html` | **owns**, incl. the month-row "เดือนถัดไป / Next month" POST form → `/inputs/{id}/duplicate` | — | — |
| `edit_input.html` | — | header card (l.52–70) button only | error area (l.72) checklist, Check button beside Save, remove gap-note l.257–259 |
| tests | `test_months.py` (append), `test_web.py`: new class **directly after `HealthAndAuthRoutes`** | `test_duplicate.py` (new), `test_web.py`: new class **directly after `InputRoutes`** | `test_presolve.py` (new), `test_web.py`: new class **directly after `SolveAndJobRoutes`** + the one specified fixture change |
| `CODEBASE.md`, `FEATURE-GAPS.md`, spec status | lead only, after merge | | |

Spec deltas from this plan (override the individual specs where they differ):
- F2's `next_month_input` lives in `webapp/core/duplicate.py` (not `months.py`); its unit tests go in `webapp/tests/test_duplicate.py`.
- F2's dashboard-button test moves to F1 (`test_dashboard_links_next_month` asserts `/inputs/{id}/duplicate` in the page); F2 keeps the edit-page button assertion.
- F3 is one agent doing both its WPs (route/template + `test_presolve.py`).

## 2. Agents
| Agent | Role / skill | Model | Worktree | Spec |
|---|---|---|---|---|
| A1 | web-specialist | sonnet | `.worktrees/dash` | `2026-09-26-month-dashboard.md` |
| A2 | web-specialist | sonnet | `.worktrees/dup` | `2026-09-26-duplicate-month.md` |
| A3 | web-specialist + test-specialist | sonnet | `.worktrees/presolve` | `2026-09-26-presolve-validation.md` |
Each agent: works only inside its worktree, commits on its branch, runs the full suites **in its worktree**
(`python3 -m unittest discover -s webapp/tests -p "test_*.py"`, `python3 test_shiftwork.py`), reports per TEMPLATE §10.
Note: device shells time out at ~180 s — run test modules one at a time.

## 3. Merge & review (lead)
1. Review each branch diff against its spec (TEMPLATE §5 gate).
2. Merge order into `main`: F1 → F2 → F3 (F3 last: it changes solve behaviour and adjusts an existing test).
   Expected conflicts: `app.py` (disjoint functions) and `test_web.py` (disjoint classes) — textual only.
3. After each merge run both suites on `main`. After all three: the cross-feature check —
   duplicate a month from the dashboard → new version appears under the next month → Check → Solve.
4. Remove worktrees (`git worktree remove`), delete merged branches, update docs, set specs `done`.

## 4. Cost / risk
- 3 sonnet agents in parallel + lead review; roughly the token cost of building them one after another,
  in about a third of the wall-clock time.
- Risk: merge conflicts in shared files — bounded by the ownership table and fixed insertion points.
- Risk: agent drifts outside its worktree — prompts state the absolute worktree path; lead checks `git status` on `main` stays clean.
