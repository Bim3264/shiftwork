# Excel template parity — holidays, holiday band, per-group needs, generator
Status: done · Size: M · Date: 2026-10-17 · Owner: lead-engineer

Branch `feature/staff-classes-and-offward`, HEAD `b1c9c45`. Follows
`2026-10-17-staff-classes-and-offward.md` (tracks A/B) and
`2026-10-17-holiday-band-and-test-coverage.md` (track C): those shipped solver +
web support for staff groups, off-ward duties and the holiday band, but the
**Excel upload path cannot express any of them**.

## 1. Objective & interpretation

A head nurse filling the Excel roster template must be able to express
everything the CSV path can: a `holidays` list, holiday-band coverage, and
per-staff-group (RN vs PN/NA) coverage for all three bands. Today she cannot,
and worse: the shipped templates **already contain** `PN/NA weekday need` /
`PN/NA weekend need` rows whose values are **silently discarded** by the parser.

Scope correction vs the intake brief: the six `Templates/*.xlsx` workbooks are
**already** current for staff classes and off-ward duties — each has a `Class`
column with an `RN,PN,NA` dropdown, all six off-ward tokens in the cell
dropdown and the legend (rows A17/A18), off-ward conditional formatting, and a
Guide entry. So the template work is **not** "add the new shift types"; it is
(a) add the three missing holiday rows and (b) stop losing the PN/NA rows. The
seventh workbook, `Shift Template (Ward 4B).xlsx` at the repo root, is the
**old** layout (no Class column, no off-ward dropdown, grid header row 17) and
is deliberately left alone as a real backward-compatibility artifact.

## 2. Current state

- `webapp/core/schedule_input.py:296` · `ScheduleInput.from_xlsx` — label key is
  built as:

  ```python
  label = _cell_str(gv(r, lc)).split("/")[0].strip().lower()
  ```

  The `split("/")[0]` exists to drop the Thai half of a bilingual
  `"English / ไทย"` label. It also truncates `"PN/NA weekday need D / E / N"` to
  `"pn"`, which matches no branch — so both PN/NA rows are dropped.
  **Measured** on `Templates/ShiftWork Template — Ward (blank).xlsx`: the card
  says `PN/NA weekday need D / E / N = 3 / 2 / 2`, the parser yields
  `coverage_pnna_weekday_day == 5` (inherited from the legacy weekday row).
- Recognised labels today: ward name, hospital, head nurse, line contact, month,
  year, plan, days in month, rest days, allow…evening, allow…day,
  special shift, `weekday need`, `weekend need`. **No holidays, no holiday
  need, no group needs.**
- `webapp/core/schedule_input.py:167` · `_parse_day_list(value) -> list[int]` —
  space/comma day list, **empty → `[]`** (deliberately unlike
  `_parse_weekends_1based`, whose empty → DEFAULT). §22 / known failure mode 11.
- `webapp/core/schedule_input.py:440-459` · `_coerce_settings` — group keys
  inherit the legacy key when **absent from `raw`**; holiday chain is
  group holiday → group weekend → legacy holiday → legacy weekend → default.
  Consequence the parser must respect: emitting only `coverage_pnna_*` and
  leaving `rn` absent makes RN inherit the legacy `Weekday/Weekend need` rows.
  That is exactly the template's layout, so **`from_xlsx` must not synthesise
  `coverage_rn_*` keys.**
- `webapp/core/schedule_input.py:695` · `_normalize_token` already routes cells
  through `constant.parse_offward`, so `flt:ช` in a **grid cell** parses today.
  The off-ward gap was template-only, and is already closed.
- Grid/class detection (`:340-371`): the day-header row is found by scanning for
  numeric cells `1,2,3` anywhere in a row, so inserting a `Class` column does
  not break it; `class_col` is a header cell starting with `"class"` in columns
  1–6 of the header row or the row above.

Verified live workbook structure (`Ward (blank)`, Roster sheet): cards at rows
5–13 (left labels col A / values col C; right labels col H / values col P),
legend rows 15–18, day-number header row 19 (`A19` Nurse, `B19` Type, `C19`
Class, `D19:AH19` days 1–31), weekday-letter row 20, nurse rows 21+. 51 merged
ranges, `freeze_panes="D21"`, gridlines off, 9 conditional-formatting rules
(including `EXACT(LEFT(D21,4),"flt:")` and `"ref:"`), 3 data validations.

## 3. Behaviour spec

### 3.1 Label normalisation (the kernel)

Drop a `/`-delimited segment **only when that segment contains Thai
characters**. Everything else is kept, so `/` inside an English label survives.

| Card label | Old key | New key | Branch |
|---|---|---|---|
| `Ward name / ชื่อวอร์ด` | `ward name` | `ward name` | unchanged |
| `Weekday need D / E / N` | `weekday need d` | `weekday need d / e / n` | `startswith("weekday need")` — unchanged |
| `PN/NA weekday need D / E / N` | `pn` (dropped) | `pn/na weekday need d / e / n` | **new** |
| `Allow Evening→Night / บ่ายต่อดึก` | `allow evening→night` | `allow evening→night` | unchanged |

Must not change: every exact-equality branch (`ward name`, `hospital`,
`head nurse`, `line contact`, `month`, `year`, `plan`, `days in month`,
`rest days`) — all nine have a Thai right-hand half in all seven workbooks, so
their keys are byte-identical before and after. All other branches use
`startswith`/`in`, and the new key is a suffix-superset of the old one, so
matches are preserved.

### 3.2 New labels recognised

| Label (English half) | Setting(s) written | Value format |
|---|---|---|
| `Special holidays` | `holidays` (1-based list) | `"13 14"` → `[13, 14]`; **empty → `[]`** |
| `Holiday need D / E / N` | `coverage_holiday_{day,evening,night}` | `"4 / 2 / 2"` |
| `PN-NA weekday need …` / `PN/NA weekday need …` | `coverage_pnna_weekday_*` | `"3 / 2 / 2"` |
| `PN-NA weekend need …` / `PN/NA weekend need …` | `coverage_pnna_weekend_*` | `"2 / 1 / 1"` |
| `PN-NA holiday need …` / `PN/NA holiday need …` | `coverage_pnna_holiday_*` | `"2 / 1 / 1"` |

**Label naming decision.** The templates keep `PN/NA …` (matching the product's
own `GROUP_LABELS` wording) and the parser is fixed to handle the `/`, rather
than renaming the rows to `PN-NA`. Reason: workbooks with `PN/NA` rows have
already shipped to customers; renaming would make an already-downloaded,
already-filled workbook keep silently losing its PN/NA numbers, while fixing the
normaliser makes those same workbooks start working. The parser accepts the
`PN-NA` spelling too, so a future rename is non-breaking either way.

Matching rules (order matters, most specific first):

- `holiday need` is tested **before** `holidays`: `"holiday need d / e / n"`
  does not start with `"holidays"`, so either order is in fact safe, but the
  order is fixed explicitly so a later edit cannot introduce the collision.
- group needs are tested **before** the legacy `weekday need` / `weekend need`
  branches, matched by a `pn/na `-or-`pn-na ` prefix.
- An absent row writes **no key at all** (so `_coerce_settings` inheritance
  runs). An empty value cell writes no key either, except `Special holidays`,
  where empty explicitly means `[]`.

Edge cases:

- `Special holidays` present but blank → `holidays = []` (not DEFAULT). This is
  the §22 / failure-mode-11 asymmetry; it must be `_parse_day_list`, never
  `_parse_weekends_1based`.
- `Holiday need` absent → `coverage_holiday_*` absent from `raw` → inherits the
  weekend band. Re-parsing an existing workbook therefore changes staffing by
  nothing.
- `PN/NA holiday need` absent but `PN/NA weekend need` present → group holiday
  inherits group weekend (existing chain, unchanged).
- A value like `"4/2/2"` (no spaces) must parse — `_parse_triple` uses
  `re.findall(r"\d+")`, so it already does.

### 3.3 Templates

Add to the SETTINGS card of each of the **six** `Templates/*.xlsx`:

- `Special holidays / วันหยุดพิเศษ` — blank value (meaning: no special
  holidays) in the five blank templates; `Ward (example)` gets a filled value.
- `Holiday need D / E / N` = `4 / 2 / 2`
- `PN/NA holiday need D / E / N` = `2 / 1 / 1`

Must not change: existing label text, existing values, the grid layout, the
legend, the dropdowns, the conditional formatting, or the Guide sheet's existing
entries (one new Guide row for special holidays is in scope).

Free tier: `max_holidays = 2` (`TIER_LIMITS`). The Free template's Guide
"Plan limits" block gains a `Special holidays` line; the blank value keeps it
within every tier's cap.

## 4. Design

### 4.1 Files

| File | Change |
|---|---|
| `webapp/core/schedule_input.py` | **WP1.** `_label_key()` helper + `_THAI_RE`; `from_xlsx` uses it; five new label branches. |
| `tools/gen_templates.py` | **WP2, new.** One definition → the six workbooks. Importable: `build_workbook(spec) -> openpyxl.Workbook`. |
| `tools/__init__.py` | **WP2, new.** Empty, so tests can import `tools.gen_templates`. |
| `webapp/tests/test_schedule_input.py` | **WP3a.** New-label parsing tests on the in-test `_make_xlsx`. |
| `webapp/tests/test_excel_templates.py` | **WP3b, new.** Generator → `from_xlsx` round-trip. |
| `CODEBASE.md`, `constraint.md` | **WP4.** Settings table gains an "Excel" column. |

**Not touched:** `shiftwork.py`, `dataimporter.py`, `constant.py`,
`webapp/web/`. This change adds no setting and no solver behaviour — it only
lets an existing setting reach the existing pipeline through a second door.

### 4.2 Kernel (lead-written, paste verbatim)

```python
# Bilingual card labels are written "English / ไทย". The Thai half is dropped —
# but ONLY when the part after a "/" actually contains Thai characters. A plain
# split("/")[0] also truncated "PN/NA weekday need" to "pn", which matched no
# branch and silently discarded both per-group coverage rows that the shipped
# templates already carry.
_THAI_RE = re.compile(r"[฀-๿]")


def _label_key(raw) -> str:
    """Normalize a bilingual WARD INFO / SETTINGS card label to its English key."""
    parts = _cell_str(raw).split("/")
    kept = [parts[0]]
    for part in parts[1:]:
        if _THAI_RE.search(part):
            break
        kept.append(part)
    return "/".join(kept).strip().lower()
```

and in `from_xlsx`, replacing line 296:

```python
label = _label_key(gv(r, lc))
```

New branches, inserted **before** the existing `weekday need` branch:

```python
elif label.startswith("special holidays") or label == "holidays":
    # Empty means EMPTY (§22 / failure mode 11), never DEFAULT.
    settings["holidays"] = _parse_day_list(_cell_str(val))
elif label.startswith(_PNNA_PREFIXES) and "need" in label:
    band = ("holiday" if "holiday need" in label
            else "weekend" if "weekend need" in label
            else "weekday" if "weekday need" in label else None)
    if band is not None:
        d, e, n = _parse_triple(val)
        settings.update({f"coverage_pnna_{band}_day": d,
                         f"coverage_pnna_{band}_evening": e,
                         f"coverage_pnna_{band}_night": n})
elif label.startswith("holiday need"):
    d, e, n = _parse_triple(val)
    settings.update(coverage_holiday_day=d,
                    coverage_holiday_evening=e,
                    coverage_holiday_night=n)
```

with `_PNNA_PREFIXES = ("pn/na", "pn-na", "pn na")` (a tuple, for
`str.startswith`). `_parse_triple` returns a 3-tuple and is already defined at
`:729`.

### 4.3 Generator (WP2)

`build_workbook(spec) -> openpyxl.Workbook` where `spec` is a dataclass:

```python
@dataclass(frozen=True)
class TemplateSpec:
    filename: str          # "ShiftWork Template — Ward (blank).xlsx"
    tier: str              # "ward" -> looked up in TIER_LIMITS for the Guide
    nurse_rows: int        # 8 free / 15 ward / 25 ward+ / 40 pro / 50 department
    example: bool = False  # Ward (example): filled roster + filled holidays
```

`TEMPLATES: tuple[TemplateSpec, ...]` holds the six. `main()` writes each to
`Templates/`. Running it must be idempotent.

The generated Roster sheet must reproduce the verified structure in §2
exactly — same label text, same cell addresses for everything that exists
today, same merges, `freeze_panes="D21"`, gridlines off, the three data
validations (cell tokens built from `constant.OFFWARD_TOKENS` +
`LocaleShift`, **not** hardcoded), and all 9 conditional-formatting rules —
plus the three new card rows from §3.3.

**Verification gate for WP2** (this is what makes the generator safe): a
throwaway comparison script must diff each generated workbook against
`Templates/_previous/<same name>` on cell values, fills, merged ranges, data
validations, conditional formatting, column widths and freeze panes, and report
**only** the three intended new rows as differences. Do not hand the work back
until that diff is clean. Do not commit the comparison script.

### 4.4 Rejected alternative

**Mutate the six existing workbooks in place** (open, insert three rows, save).
Preserves styling perfectly and is less code. Rejected: it is not idempotent,
it leaves no single source of truth, it cannot be unit-tested (the `.xlsx` are
gitignored, so no test may read them), and the next token addition is again
seven manual edits. The 2 KB `Department (blank).xlsx` currently on disk — 14.5
KB in `Templates/_previous/` — is what ad-hoc editing already cost us today.

### 4.5 Why a generator at all, given the workbooks are gitignored

The asymmetry is the argument. The `.xlsx` cannot be committed, so they cannot
be tested or reviewed; a committed generator can be both. It also makes the
round-trip test possible at all: the test builds a workbook **in memory** from
the same definition the shipped files come from, so a template that drifts from
the parser fails CI instead of failing a customer.

## 5. Work packages

| WP | Owner | Model | Files | Depends on | Effort |
|----|-------|-------|-------|-----------|--------|
| 1 | lead-engineer | — | `webapp/core/schedule_input.py` | — | S |
| 2 | data-import-specialist | sonnet | `tools/gen_templates.py`, `tools/__init__.py`, `Templates/*.xlsx` | WP1 | L |
| 3a | test-specialist | sonnet | `webapp/tests/test_schedule_input.py` | WP1 | M |
| 3b | lead-engineer | — | `webapp/tests/test_excel_templates.py` | WP2 | S |
| 4 | lead-engineer | — | `CODEBASE.md`, `constraint.md` | WP1–3 | S |

WP2 and WP3a run in parallel; their file lists are disjoint.

Patterns to copy:

- new label branch → `webapp/core/schedule_input.py:328` (`weekday need`).
- xlsx-building test → `webapp/tests/test_schedule_input.py:344` (`_make_xlsx`).
- off-ward / group assertions → `webapp/tests/test_schedule_input.py` §
  "Staff classes / off-ward duties / holiday band".
- tier limits for the Guide block → `dataimporter.py` · `TIER_LIMITS`.

## 6. Acceptance tests

In `webapp/tests/test_schedule_input.py` (WP3a), extending `_make_xlsx` with the
new rows:

- `test_special_holidays_parsed` — card `Special holidays = "3 4"` →
  `settings["holidays"] == [3, 4]`. Fails if the branch is removed.
- `test_special_holidays_blank_means_empty` — blank value → `holidays == []`,
  **not** the weekends default. Fails if `_parse_weekends_1based` is used.
- `test_holiday_need_parsed` — `Holiday need = "4 / 2 / 2"` →
  `coverage_holiday_{day,evening,night} == (4, 2, 2)`.
- `test_holiday_need_absent_inherits_weekend` — row omitted, weekend `= "1 / 1 / 1"`
  → `coverage_holiday_day == 1`.
- `test_pnna_needs_parsed` — `PN/NA weekday need = "3 / 2 / 2"` →
  `coverage_pnna_weekday_day == 3`, **and** `coverage_rn_weekday_day == 2`
  (inherited from the legacy `Weekday need = "2 / 1 / 1"` row). This single
  assertion is the regression test for the `/` collision: before WP1 the first
  value is 2, not 3.
- `test_pnna_dash_spelling_parsed` — `PN-NA weekend need` parses identically.
- `test_label_key_keeps_english_slash` — `_label_key("PN/NA weekday need D / E / N")`
  == `"pn/na weekday need d / e / n"`; `_label_key("Ward name / ชื่อวอร์ด")` ==
  `"ward name"`.
- `test_existing_labels_unchanged` — every one of the nine exact-equality labels
  still parses (guards §3.1).

In `webapp/tests/test_excel_templates.py` (WP3b):

- `test_generated_ward_template_round_trips` — `build_workbook(ward_spec)` →
  bytes → `from_xlsx` → a `ScheduleInput` whose `validate()` passes and which
  has: two staff groups (`solve_groups() == ["rn", "pn_na"]`), a non-empty
  `settings["holidays"]`, holiday-band coverage distinct from the weekend band,
  and at least one off-ward cell token surviving into `nurses[i].shifts`.
- `test_generated_templates_all_parse` — every spec in `TEMPLATES` builds and
  parses without raising.
- `test_cell_dropdown_covers_every_valid_token` — the generated cell data
  validation lists exactly `LocaleShift` symbols + `off` + `vac` +
  `OFFWARD_TOKENS`. **This is the anti-drift test**: adding a token to
  `constant.py` without updating the generator fails here.

Suites that must stay green, from repo root:
`python -m unittest discover -s webapp/tests -p "test_*.py"` and
`python -m unittest test_shiftwork`.

## 7. Risks

Known failure modes touched (CODEBASE.md → "Known failure modes"):

- **6. Input contract (CSV and Excel must round-trip identically).** The whole
  point of the change. Mitigated by WP3b. Note this is *additive*: no existing
  column or cell semantic changes, so it is not an input-contract escalation.
- **7. Half-wired settings.** `holidays` and the holiday/group coverage keys
  already pass every other layer; this adds the missing template layer. The
  "Excel" column added to CODEBASE.md in WP4 is what keeps the gap visible.
- **11. `Solver.weekends` is the UNION, and the holiday band inherits the
  weekend band at three layers.** `from_xlsx` must emit **no** holiday-coverage
  key when the row is absent, or it would freeze the inheritance and change
  staffing for a re-uploaded workbook. Covered by
  `test_holiday_need_absent_inherits_weekend`.
- **12. Staff-group split / combined tier cap.** The template must not imply
  each group gets its own `max_nurses`; the Guide "Plan limits" block says
  *total*.

Lead will verify at review: the baseline re-parse of all seven workbooks is
unchanged except for the intended keys; the WP2 styling diff against
`_previous/` is clean; each acceptance test genuinely fails with its rule
removed (mutation check).

## 8. Do NOT

- Do **not** `git add -A`. Add by name; `git check-ignore -v <path>` before
  assuming a file is stageable. `/real/` and `/docs/business/` must never be
  committed.
- Do **not** commit any `.xlsx`. `/Templates/` and `/Shift Template*.xlsx` stay
  gitignored.
- Do **not** touch `Shift Template (Ward 4B).xlsx` — it is the old-layout
  backward-compatibility artifact.
- Do **not** rename the existing `PN/NA …` labels in the templates (§3.2).
- Do **not** change `shiftwork.py`, `dataimporter.py` or `constant.py`.
- Do **not** hardcode the token list in the generator; import from `constant.py`.
- Do **not** chase the ~56 Windows `PermissionError`s in `tearDown` — pre-existing
  harness artifact.
- Do **not** push or open a PR. Stay on `feature/staff-classes-and-offward`.
- Do **not** run the test suites while another agent is mid-edit; report the exact
  command and its output.

## 9. STOP conditions

- The styling diff against `Templates/_previous/` cannot be made clean.
- An existing label's parsed key changes under `_label_key`.
- A round-trip test needs a parser change beyond WP1's contract.
- Any acceptance test can only be made green by weakening it.

## 10. Report format

1. Files changed (path · symbol).
2. Each acceptance test: name, and the command + result proving it passes.
3. Mutation check: which rule you deleted, which test failed.
4. Anything you could not do, and why.

## 11. Done log

Status: **done**. Three of this spec's own facts were wrong and were corrected
during the build; they are recorded here rather than silently patched above.

- WP1 (lead): `_label_key` + `_THAI_RE` + five new branches landed in
  `webapp/core/schedule_input.py`. Baseline re-parse of all seven workbooks:
  only `coverage_pnna_*` changed (5/3/3 → 3/2/2 weekday, 4/2/2 → 2/1/1
  weekend), i.e. the previously-discarded rows now land; no other key moved.
  `Shift Template (Ward 4B).xlsx` parses byte-identically.
- WP2 (data-import-specialist): `tools/gen_templates.py` (+ `tools/__init__.py`),
  all six workbooks regenerated. Token vocabulary derived from `constant.py`
  with a module-level assert on `LocaleShift` membership; Guide "Plan limits"
  from `TIER_LIMITS`.
- WP3a (test-specialist): 8 tests in `test_schedule_input.py`, each
  mutation-checked. Lead fixed two defects on review: a factually wrong comment
  about the weekends default, and an unguarded `extra_rows` range that would
  silently overwrite the grid header at five entries.
- WP3b (lead): 11 tests in `webapp/tests/test_excel_templates.py`; 5 mutations
  each confirmed to fail the intended test.
- WP4 (lead): `CODEBASE.md` settings table gained an **Excel** column, plus new
  known failure mode 13 and a correction to the `max_holidays` tier line;
  `constraint.md` §22 gained a "where the three bands can be set" table.

**Correction 1 — §4.3's styling gate was unsatisfiable.** `Templates/_previous/`
is NOT the pre-change snapshot; it is the **pre-Track-A/B layout** (verified:
`freeze_panes="C18"`, day 1 at row 16, no Class column, no off-ward tokens in
the dropdown). WP2 correctly raised this as a §9 STOP, snapshotted the live
workbooks instead and diffed against that: `TOTAL UNEXPECTED: 0` across all six,
and 1755 diffs before its generator was finished, so the harness was not
vacuous. Accepted.

**Correction 2 — the three new rows force a row shift.** §2/§4.3 asserted the
old addresses would be preserved; they cannot be, because right-card rows 14–15
held the spacer and the SHIFT CODES header. New layout: cards 5–16, legend
18–21, grid header 22–23, nurses from 24, `freeze_panes="D24"`. Harmless —
`from_xlsx` locates the day-header row dynamically — and the WP3b tests
deliberately hardcode no row numbers.

**Correction 3 — §3.3's Free-tier holiday cap does not exist.** `max_holidays`
in `TIER_LIMITS` is the **per-nurse requested-days-off** cap
(`DataImporter.maxDayOff = min(maxDayOff, limits['max_holidays'])`, and
`presolve`'s `quota_off`). Nothing caps `settings["holidays"]` on any tier, so
the Free Guide line reads "not limited by plan" instead of "2". CODEBASE.md's
tier line was corrected so this misreading does not recur.

**Lead fix on review — the example workbook did not exercise the features.**
WP2's `_EXAMPLE_ROWS` carried no staff class and no off-ward cell, so
`solve_groups()` returned `[]` and no `flt:`/`ref:` token round-tripped — i.e.
the one standard this spec exists to meet. Lead added classes to all 12 rows
(7 RN + 5 PN/NA; `validate()` requires all-or-none), four off-ward cells, and
`_EXAMPLE_SETTINGS` lowering the example's needs (5/3/3 split across two groups
would need 11 RNs of 7 and be infeasible on sight). Also moved a `ช/บ` request
off the RN deputy row, where §16/§17 would have silently dropped it.

**Known debt, not fixed (pre-existing, reproduced deliberately for parity):**
the templates' default `Rest days` (`6 7 13 14 …`) disagree with the weekday
letters they print (July 2026 has Sat/Sun on 4/5, 11/12 …), so the tinted
columns are labelled M/T. And the Class column takes the last nurse row's band
colour on every row. Both predate this change; fixing either alters shipped
customer artifacts and is Bim's call.
