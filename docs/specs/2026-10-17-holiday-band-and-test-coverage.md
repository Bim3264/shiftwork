# Track C — special-holiday coverage band · and the Track A/B test gap
Status: approved · Size: L · Date: 2026-10-17 · Owner: lead-engineer

Continues `docs/specs/2026-10-17-staff-classes-and-offward.md` (tracks A and B,
shipped in `43b6d93`). Branch `feature/staff-classes-and-offward`, HEAD `8eb5d25`.
That spec's §1–§4 and §7–§9 still apply; this document supersedes its §12 E3 and
replaces its unshipped WP7.

Two parts, equal priority:

- **C.** A third coverage band for special holidays, defaulting to the weekend band.
- **T.** Close the test gap: tracks A and B shipped with **zero** new webapp tests.

---

## 1. Objective & interpretation

**C.** Today `webapp/core/calendar_util.py:52` computes
`combined = weekends ∪ public holidays ∪ extra_holidays` and the month auto-fill
stores `combined` into `settings["weekends"]`. A 1 Jan holiday is therefore
indistinguishable from a Sunday, so it cannot be staffed differently. Give holidays
their own settings list and their own coverage band, **defaulting to the weekend
band's numbers**, so nothing about an existing ward's staffing changes.

**Interpretation that drives the design:** `weekends` has two jobs today — it picks
the coverage band, and it marks a day as non-working for the senior head/deputy rule
(§16/§17), the warm start and the fairness "weekend" column. Track C splits **only
the first job**. Everywhere else continues to see `weekends ∪ holidays`, which is
exactly the set it sees today. That is what makes the change behaviour-preserving,
and it is why the kernel (§4) sets `Solver.weekends` to the union and adds a separate
`Solver.holidays` read by one method.

**Already-saved months — confirmed, Bim's reading is correct.** An existing input has
its holidays *inside* `weekends` with no record of which days they were, and they are
**not recoverable**. Absent a `holidays` key, `_coerce_settings` defaults it to `[]`,
so every day currently in `weekends` keeps the **weekend** band and the month solves
exactly as it does now. The holidays only separate when the user presses auto-fill
again, and because the holiday band inherits the weekend band's numbers (§3.2) that
re-run changes staffing by nothing. **Do not back-fill.** It is technically possible
to re-derive `(public holidays ∪ extra_holidays) ∩ weekends` from `ward_meta.month/year`,
and it is the wrong thing to do: the `holidays` library's Thai calendar changes between
releases, so the reclassification would depend on an installed package version rather
than on what the user saw when they pressed auto-fill, it silently moves days between
bands at read time, and undated inputs cannot be derived at all. Absent key → empty
list. One rule, no version dependence, no silent reclassification.

**No migration, no DB column** (Alembic is deferred): `holidays` is one more key in
the existing JSON `settings` blob, and a missing key has a defined default — the same
mechanism `extra_holidays` and the 12 group-coverage keys already use.

**T.** Tracks A and B were verified with throwaway scratch scripts that are not in the
repo, so none of that behaviour is protected. Bim has verified the behaviour is good
*now*, end-to-end through the real HTTP routes; these tests are to keep it that way.
Lift the fixtures from the scratch scripts named in §5 rather than reinventing them.

**Challenge:** 18 coverage inputs is past comfortable, as noted when 12 shipped. It is
survivable here only because the existing table is already a band × group grid
(`edit_input.html:122-154`), so the third band is **one more row**, not a new block —
3 rows × 6 columns, and single-class wards still see 3 × 3. If a fourth band is ever
proposed, the answer is a per-day override table, not a fourth row.

---

## 2. Current state

Quoted so no executor has to open these files.

- `webapp/core/calendar_util.py:30-68` · `month_dates(year, month, extra_holidays)`
  returns `{num_days, weekends, holidays:[{day,name}], extra, combined}`;
  `:52` `combined = sorted(set(weekends) | set(holiday_days) | set(extra))`.
- `webapp/web/app.py:370-373` · auto-fill:
  ```python
  md = month_dates(year, month, schedule_input.settings.get("extra_holidays", []))
  schedule_input.settings["num_days"] = md["num_days"]
  schedule_input.settings["weekends"] = md["combined"]
  ```
- `webapp/core/duplicate.py:40-43` · `next_month_input` does the same with
  `md["combined"]` and resets `extra_holidays` to `[]`.
- `webapp/core/schedule_input.py:74-106` · `_DEFAULT_SETTINGS`: `weekends`,
  `extra_holidays: []`, six legacy `coverage_{weekday,weekend}_{day,evening,night}`
  (5/3/3, 4/2/2), then a loop adding the 12 group keys.
- `webapp/core/schedule_input.py:116-131` · `_LEGACY_COVERAGE_KEYS` (6 names),
  `_GROUP_COVERAGE_PREFIX = {"rn": "coverage_rn_", "pn_na": "coverage_pnna_"}`,
  `_GROUP_COVERAGE_KEYS` (the 12), `_COVERAGE_KEYS = legacy + group`.
- `webapp/core/schedule_input.py:150-156` · `_parse_day_list(value)` — space/comma
  list, **empty → `[]`** (unlike `_parse_weekends_1based`, which returns the default).
- `webapp/core/schedule_input.py:385-418` · `_coerce_settings`: `weekends`,
  `extra_holidays`, bools, percent, then `for key in _COVERAGE_KEYS` clamp
  `max(0, int(...))`, then the group-inherits-legacy loop:
  ```python
  for grp in ("rn", "pnna"):
      for band in ("weekday", "weekend"):
          for shift in ("day", "evening", "night"):
              gkey = f"coverage_{grp}_{band}_{shift}"
              lkey = f"coverage_{band}_{shift}"
              if gkey not in raw and lkey in raw:
                  s[gkey] = s[lkey]
  ```
- `webapp/core/schedule_input.py:494-553` · `to_solver_csv(*, group=None)` writes
  `num_days`, `weekends`, `extra_holidays`, `_BOOL_SETTING_KEYS`,
  `min_request_percent`, then `for key in _LEGACY_COVERAGE_KEYS` (value remapped
  from the group prefix when `group` is given). Exact current ungrouped output for
  2 nurses / 3 days / `weekends=[2]`:
  ```
  [ward]\nward_name,W\ntier,ward\n\n[settings]\nnum_days,3\nweekends,2\n
  extra_holidays,\nallow_evening_to_night,false\nallow_night_to_day,false\n
  head_nurse_special_shift,true\nrelax_days_off,false\nenforce_vacation,true\n
  min_request_percent,100\ncoverage_weekday_day,5\ncoverage_weekday_evening,3\n
  coverage_weekday_night,3\ncoverage_weekend_day,4\ncoverage_weekend_evening,2\n
  coverage_weekend_night,2\n\n[schedule]\nName,Type,1,2,3\nA,,,,\nB,,,,\n
  ```
- `dataimporter.py:130-142` · `_parse_weekends(value)` — 1-based→0-based,
  **empty → `_DEFAULT_WEEKENDS`** (cannot express "no weekends").
- `dataimporter.py:218-235` · the settings dict:
  `'weekends': self._parse_weekends(raw_settings.get('weekends',''))`, then
  `'coverage_weekday_day': int(str(raw_settings.get('coverage_weekday_day',5)).strip() or 5)`
  and five siblings.
- `shiftwork.py:11-21` · `__init__(filename, num_days, weekends, allow_shift_e_n,
  allow_shift_n_d)`; `self.weekends = weekends  # 0-based day indices`.
- `shiftwork.py:76-93` · `_rebuild_days()`:
  ```python
  is_weekend = day in self.weekends
  band = self.coverage['weekend'] if is_weekend else self.coverage['weekday']
  ```
- `shiftwork.py:125-132` · `initShiftSettings` defaults
  `self.coverage = {'weekday': {DAY:5,EVENING:3,NIGHT:3}, 'weekend': {DAY:4,EVENING:2,NIGHT:2}}`.
- `shiftwork.py:167-183` · `loadReqShifts`: `self.weekends = set(s['weekends'])`,
  then `self.coverage = {'weekday': {...s.get('coverage_weekday_day',5)...},
  'weekend': {...}}`, then `self._rebuild_days()`.
- **Every other use of `self.weekends`** (verified by grep, these must keep seeing a
  holiday as a weekend): `:375-377` senior request skip-guard, `:410` off-ward
  skip-guard, `:486` `optionHeadNurse`, `:606` `_build_warm_start`. Note
  `holiday_imb` at `:548` is **days-off** imbalance, unrelated to the calendar.
- `shiftwork.py:443-467` · `_constraintMinimumNurse` reads
  `required = self.days[d]['min_shifts'][shift]` and, in diagnosis pass 2, appends
  `(lit, {'day': d + 1, 'shift': shift.value, 'required': required})`. **The band is
  already baked into `self.days` by `_rebuild_days`, so the diagnosis coverage pass
  needs no new gating** — only a band label so the message can name it.
- `webapp/core/diagnosis.py:48-53` · `· day {c['day']}: needs {c['required']} {c['shift']}`.
- `webapp/core/presolve.py:134` · `weekends = set(s["weekends"])  # 1-based`;
  `:176` `wk = d in weekends`; `:233` `senior_meeting_weekend`;
  `:290-296` `_band(s, wk: bool, group=None)` → `b = "weekend" if wk else "weekday"`;
  `:325,331,359-360,387` the other `ctx["weekends"]` reads.
- `webapp/core/presolve.py:106` · `rep = PresolveReport(checks_total=1 + 3*len(groups or [None]))`.
- `webapp/core/fairness.py:59-66,89-91` · `weekend_days` → `wk = {int(d) for d in
  (weekend_days or [])}` → the `weekend_shifts` / `night_weekend` columns.
- `webapp/web/app.py:106-135` · `_settings_from_form(form, current=None)`:
  `"weekends": form.get("weekends","")`, `"extra_holidays": form.get("extra_holidays","")`,
  then for each `_COVERAGE_KEYS` → `form.get(key)`, else `current[key]`, else omit.
- `webapp/web/app.py:447-478` · `_fairness_for_job` → `weekends = settings.get("weekends")`.
- `webapp/web/app.py:539-558` · `coverage_groups` (`[{label, prefix}]`) and
  `coverage_defaults = {"weekday": {...5,3,3}, "weekend": {...4,2,2}}`;
  `:558-560` `weekends_str`, `extra_holidays_str`.
- `webapp/web/templates/edit_input.html:122-154` · one `<table class="opts">`;
  `{% set bands = [("weekday","จ–ศ Weekday"), ("weekend","ส–อา Weekend")] %}`,
  `{% set shifts = [("day","ช",...),("evening","บ",...),("night","ด",...)] %}`,
  field name `{{ g.prefix ~ band ~ "_" ~ key }}`, value
  `settings.get(fname, coverage_defaults[band][key])`.
- `webapp/web/templates/edit_input.html:111-115` · the auto-fill button, labelled
  "Auto-fill days & weekends from month".

**Test state (verified):** `webapp/tests/` has no test mentioning `staff_class`,
`solve_groups`, `offward`, `groups`, `enforce_max_nurses` or any off-ward token.
`test_shiftwork.py` has none either. Of the previous spec's 16 acceptance tests,
**none exist**.

---

## 3. Behaviour spec

### 3.1 The `holidays` setting

- New key `holidays`: a **1-based** list of day numbers, stored beside `weekends`,
  parsed with `_parse_day_list` so **empty means empty** (not "default").
- Default `[]`. A saved input without the key gets `[]` → every day in its `weekends`
  keeps the weekend band → identical solve. **This is the migration.**
- Not clamped to `1..num_days`, exactly as `weekends` is not. Out-of-range days are
  inert: `_rebuild_days` only iterates `range(num_days)`.
- A day may be in both lists (a public holiday that falls on a Saturday).
  **Holiday wins the band.** Precedence: `holiday` > `weekend` > `weekday`.
- `extra_holidays` keeps its present meaning: the user's manual holiday days, an
  *input* to auto-fill. `holidays` is the *resolved* list auto-fill writes.

### 3.2 The holiday coverage band

- Three new legacy/solver-facing keys: `coverage_holiday_{day,evening,night}`.
  Six new group keys: `coverage_{rn,pnna}_holiday_{day,evening,night}`.
  Totals: legacy 6 → **9**, group 12 → **18**, all ints clamped `>= 0`.
- Defaults equal the weekend defaults: 4 / 2 / 2.
- **Inheritance (the whole point of the track).** In `_coerce_settings`, resolved in
  this exact order, first hit wins:
  - `coverage_holiday_{S}` ← itself if in `raw`; else `coverage_weekend_{S}` if in
    `raw`; else default (4/2/2).
  - `coverage_{G}_holiday_{S}` ← itself if in `raw`; else `coverage_{G}_weekend_{S}`
    if in `raw`; else `coverage_holiday_{S}` if in `raw`; else `coverage_weekend_{S}`
    if in `raw`; else default.
  - The existing weekday/weekend group inheritance is unchanged.
  Rationale for the order: a ward that set *its group's* weekend numbers must get
  those on holidays, not the legacy ward-wide ones.
- Same inheritance at the solver boundary, once, in `dataimporter.py`:
  `coverage_holiday_{S}` defaults to **the value parsed for `coverage_weekend_{S}`
  from that same file**, not to a constant. So a `to_solver_csv` output that predates
  this change still produces equal weekend and holiday bands.

### 3.3 What a holiday does NOT change

A day in `holidays` behaves like a weekend for everything except the coverage band:

| Consumer | Sees |
|---|---|
| `Solver._rebuild_days` coverage band | `holidays` first, then `weekends` |
| §16/§17 senior head/deputy rule (`shiftwork.py:375,486`) | `weekends ∪ holidays` |
| off-ward senior skip-guard (`shiftwork.py:410`) | `weekends ∪ holidays` |
| `_build_warm_start` (`shiftwork.py:606`) | `weekends ∪ holidays` |
| `presolve` senior rules / `senior_meeting_weekend` / `_explain_day` | `weekends ∪ holidays` |
| `presolve._band` | `holidays` first, then `weekends` |
| `fairness_stats` weekend columns | `weekends ∪ holidays` |

### 3.4 Auto-fill and duplicate

- `POST /inputs/{id}/autofill` now sets
  `weekends = md["weekends"]` (Sat/Sun only) and
  `holidays = md["holiday_days"]` (public ∪ `extra_holidays`), plus `num_days`.
- **Edge — re-running auto-fill on an existing ward must change nothing about
  staffing.** Days move out of `weekends` into `holidays`, and because the holiday
  band inherited the weekend band the minimums per day are identical. The acceptance
  test for this (test C7) asserts the *solved grid* is unchanged, not just the
  settings.
- `month_dates` gains `holiday_days` = `sorted(public ∪ extra)`. `combined` is kept
  (still the union) so nothing that reads it breaks.
- `duplicate.next_month_input` sets `holidays = md["holiday_days"]` for the new month
  alongside `weekends = md["weekends"]`; `extra_holidays` still resets to `[]`. An
  undated input copies both lists unchanged.
- **Edge — manual edits to the `holidays` box survive a save but not a re-auto-fill**,
  identically to `weekends` today. Stated in the UI hint.

### 3.5 UI

- One extra row in the existing coverage table: `("holiday", "วันหยุดพิเศษ Holiday")`.
  3 rows × (3 or 6) columns. No new card, no second block.
- A `holidays` text input beside the existing `weekends` and `extra_holidays` inputs,
  same style, hint: "Special holidays — staffed by the Holiday row below. Auto-fill
  sets this from Thai public holidays + your extra dates."
- Auto-fill button label becomes "Auto-fill days, weekends & holidays from month".
- `coverage_defaults` gains `"holiday": {"day": 4, "evening": 2, "night": 2}`.

### 3.6 Diagnosis (`constraint.md` §20)

No new gating pass: the band is already resolved into `self.days[d]['min_shifts']`
before `_constraintMinimumNurse` runs, so pass 2 already gates holiday-band
minimums. Add `'band': 'weekday'|'weekend'|'holiday'` to each gated coverage item so
an infeasible holiday floor is **nameable**, and render it:
`· day 13 (holiday): needs 4 ช`. For `weekday` the parenthetical is omitted, so
existing diagnosis text is unchanged.

---

## 4. Design

### Files (exhaustive)

| File | Part | Change |
|---|---|---|
| `webapp/core/schedule_input.py` | C | `holidays` setting, 9+18 coverage keys, inheritance chain, `to_solver_csv` emission |
| `dataimporter.py` | C | parse `holidays` (0-based, empty→empty) + 3 `coverage_holiday_*` keys inheriting the file's weekend values |
| `shiftwork.py` | C | `self.holidays`, union `self.weekends`, `'holiday'` band, diagnosis `band` label |
| `constraint.md` | C | new §22 + amend §9 (coverage) and §20 (diagnosis item shape) |
| `webapp/core/calendar_util.py` | C | `holiday_days` key |
| `webapp/core/duplicate.py` | C | carry `holidays` for the new month |
| `webapp/core/diagnosis.py` | C | render the band |
| `webapp/core/presolve.py` | C | holiday band + union weekends |
| `webapp/core/fairness.py` | C | nothing (caller passes the union) |
| `webapp/web/app.py` | C | auto-fill, `_settings_from_form`, `_render_edit` bands/defaults, `_fairness_for_job` union |
| `webapp/web/templates/edit_input.html` | C | holiday band row, `holidays` input, button label |
| `webapp/tests/test_schedule_input.py`, `test_tiers.py` | T | §6 group A |
| `webapp/tests/test_solver_runner.py` | T | §6 group B |
| `webapp/tests/test_fairness.py`, `test_web.py` | T | §6 group C |
| `test_shiftwork.py` | T | §6 group D |
| `webapp/tests/test_presolve.py`, `test_calendar_util.py`, `test_duplicate.py` | T | §6 group E |
| `CODEBASE.md` | C+T | map, settings table, failure modes (lead, at close) |

### New / changed signatures (exact)

```python
# webapp/core/schedule_input.py  — module level
_LEGACY_COVERAGE_KEYS = (
    "coverage_weekday_day", "coverage_weekday_evening", "coverage_weekday_night",
    "coverage_weekend_day", "coverage_weekend_evening", "coverage_weekend_night",
    "coverage_holiday_day", "coverage_holiday_evening", "coverage_holiday_night",
)
_COVERAGE_BANDS = ("weekday", "weekend", "holiday")   # NEW, used by the key loops
# _GROUP_COVERAGE_KEYS now loops _COVERAGE_BANDS -> 18 names
# _DEFAULT_SETTINGS gains: "holidays": [], the 3 legacy holiday keys (4/2/2),
#   and the 6 group holiday keys (4/2/2)

# webapp/core/calendar_util.py
def month_dates(year, month, extra_holidays=None) -> dict: ...
    # returns {num_days, weekends, holidays, extra, combined, holiday_days}
    # holiday_days = sorted(set(public holiday days) | set(extra))

# shiftwork.py — Solver
#   self.holidays: set[int]        NEW, 0-based, coverage band ONLY
#   self.weekends: set[int]        now the UNION of weekends and holidays
#   self.coverage gains a 'holiday' key

# webapp/core/presolve.py
def _band(s, band: str, group=None) -> dict[str, int]: ...   # band name, not a bool
def _day_band(ctx, d: int) -> str: ...  # "holiday" | "weekend" | "weekday"
```

`run_schedule`, `fairness_stats`, `presolve.check`, `tiers.*` signatures are
**unchanged**.

### Rejected alternative

**Keep one `weekends` list and add a parallel `holiday_days` list consulted only by
the band lookup, leaving `weekends` as the union in storage too.** That is close to
what the kernel does *inside the solver*, but doing it in `settings` as well would
mean the edit page's "weekends" box keeps showing holidays mixed into the Sat/Sun
list, which is the very thing the customer is complaining about — they cannot see or
edit which days are holidays. The storage split is the deliverable; the union is an
implementation detail of the rules that do not care.

**Also rejected: back-filling old months** by recomputing Thai public holidays. §1.

### Kernel (C) — written by the lead. Use verbatim, adapting only names.

**KC1 · `dataimporter.py`** — a 0-based holiday parser next to `_parse_weekends`
(`dataimporter.py:130`). Note the deliberate difference: **empty → `[]`**.

```python
    @staticmethod
    def _parse_holidays(value):
        """Parse space- or comma-separated 1-based day numbers into 0-based ints.

        Unlike _parse_weekends, an empty value means an EMPTY list, not a default:
        a ward with no special holidays is the normal case, and a saved month that
        predates the holiday band has no `holidays` line at all — it must keep
        every one of its days in the weekend band (constraint.md §22).
        """
        if not str(value).strip():
            return []
        parts = str(value).replace(',', ' ').split()
        try:
            return [int(p) - 1 for p in parts if p.strip()]
        except ValueError:
            print('[Settings] Could not parse holidays value; using none.')
            return []
```

In the settings dict (`dataimporter.py:218-235`), add `holidays` beside `weekends`
and the three holiday coverage keys **after** the weekend ones, each defaulting to
the weekend value already parsed from this same file:

```python
        _cov_weekend_day     = int(str(raw_settings.get('coverage_weekend_day', 4)).strip() or 4)
        _cov_weekend_evening = int(str(raw_settings.get('coverage_weekend_evening', 2)).strip() or 2)
        _cov_weekend_night   = int(str(raw_settings.get('coverage_weekend_night', 2)).strip() or 2)
```
then inside the returned dict:
```python
            'holidays':                 self._parse_holidays(raw_settings.get('holidays', '')),
            'coverage_weekend_day':     _cov_weekend_day,
            'coverage_weekend_evening': _cov_weekend_evening,
            'coverage_weekend_night':   _cov_weekend_night,
            # The holiday band defaults to THIS FILE's weekend band, so a solver CSV
            # written before the band existed produces identical staffing (§22).
            'coverage_holiday_day':     int(str(raw_settings.get('coverage_holiday_day',     _cov_weekend_day)).strip()     or _cov_weekend_day),
            'coverage_holiday_evening': int(str(raw_settings.get('coverage_holiday_evening', _cov_weekend_evening)).strip() or _cov_weekend_evening),
            'coverage_holiday_night':   int(str(raw_settings.get('coverage_holiday_night',   _cov_weekend_night)).strip()   or _cov_weekend_night),
```
The legacy `SettingLoader` path (`dataimporter.py:347`) also needs
`self.holidays = []`.

**KC2 · `shiftwork.py` · `__init__`** (right after `self.weekends = weekends`):

```python
        # Special-holiday days (0-based), constraint.md §22. These pick the
        # 'holiday' coverage band and NOTHING else: self.weekends is kept as the
        # UNION of weekends and holidays, so every rule that treats a day as
        # non-working (§16/§17 senior head/deputy, the off-ward skip-guard, the
        # warm start) sees a holiday exactly as it did when auto-fill merged
        # holidays into `weekends`. Only _rebuild_days() tells the two apart.
        self.holidays = set()
```

**KC3 · `shiftwork.py` · `initShiftSettings`** — the coverage default dict becomes:

```python
        self.coverage = {
            'weekday': {Shift.DAY: 5, Shift.EVENING: 3, Shift.NIGHT: 3},
            'weekend': {Shift.DAY: 4, Shift.EVENING: 2, Shift.NIGHT: 2},
            # Defaults equal the weekend band: a holiday is staffed as a weekend
            # until the ward says otherwise (§22).
            'holiday': {Shift.DAY: 4, Shift.EVENING: 2, Shift.NIGHT: 2},
        }
```

**KC4 · `shiftwork.py` · `loadReqShifts`** — replace the `self.weekends = ...` line
and extend the coverage dict:

```python
        self.holidays              = set(s.get('holidays', ()))
        # UNION, deliberately: see the note in __init__.
        self.weekends              = set(s['weekends']) | self.holidays
```
```python
            'holiday': {Shift.DAY:     s.get('coverage_holiday_day',
                                             s.get('coverage_weekend_day', 4)),
                        Shift.EVENING: s.get('coverage_holiday_evening',
                                             s.get('coverage_weekend_evening', 2)),
                        Shift.NIGHT:   s.get('coverage_holiday_night',
                                             s.get('coverage_weekend_night', 2))},
```

**KC5 · `shiftwork.py` · `_rebuild_days`** — replace the two band lines with:

```python
            # Band precedence: holiday > weekend > weekday. self.weekends is the
            # union, so a holiday that falls on a Sunday is in both and the
            # holiday band must win.
            if day in self.holidays:
                band = self.coverage['holiday']
            elif day in self.weekends:
                band = self.coverage['weekend']
            else:
                band = self.coverage['weekday']
```
Also add `self.holidays` to the docstring's "call this any time … changes" list.

**KC6 · `shiftwork.py` · `_constraintMinimumNurse`** — the gated-item dict gains the
band, so the diagnosis can name a holiday floor (§3.6). Replace the `self._diag_gated.append(...)`
call with:

```python
                    band = ('holiday' if d in self.holidays
                            else 'weekend' if d in self.weekends else 'weekday')
                    self._diag_gated.append(
                        (lit, {'day': d + 1, 'shift': shift.value,
                               'required': required, 'band': band})
                    )
```

**KC7 · `webapp/core/diagnosis.py:50-51`** — name the band:

```python
        for c in named.get("coverage", []):
            band = c.get("band")
            where = f"day {c['day']}" + (f" ({band})" if band and band != "weekday" else "")
            lines.append(f"  · {where}: needs {c['required']} {c['shift']}")
```

**KC8 · `webapp/core/presolve.py`** — the band becomes a name, and `weekends`
becomes the union. In `_check_group` (`presolve.py:134`):

```python
    # The union, exactly as shiftwork.Solver keeps it: a holiday is non-working
    # for the senior rule. Only the coverage band tells the two apart (§22).
    holidays = set(s.get("holidays") or ())              # 1-based
    weekends = set(s["weekends"]) | holidays             # 1-based
```
Put `holidays=holidays` into `ctx` (`presolve.py:260`) and replace `_band`:

```python
def _day_band(ctx, d: int) -> str:
    """Coverage band for a 1-based day: holiday > weekend > weekday (§22)."""
    if d in ctx.get("holidays", ()):
        return "holiday"
    return "weekend" if d in ctx["weekends"] else "weekday"


def _band(s, band: str, group=None) -> dict[str, int]:
    """Coverage minimums for a band name ('weekday'/'weekend'/'holiday').
    `group` ('rn'/'pn_na') selects that staff group's keys; None uses legacy."""
    pre = _GROUP_PREFIX[group] if group is not None else "coverage_"
    return {D: s[f"{pre}{band}_day"], E: s[f"{pre}{band}_evening"],
            N: s[f"{pre}{band}_night"]}
```
Then at `:331` and `:360` use `_band(s, _day_band(ctx, d), ctx.get("group"))`.
Leave `:325` (`d in ctx["weekends"]` for the senior-off count), `:359` and `:387`
(`"weekend/holiday"` label) reading the union — they are already correct.

---

## 5. Work packages

Files are disjoint **within each wave**. Implementation packages must not touch test
files; test packages must not touch non-test files.

**Hard rule for every executor — this is why two reports contradicted each other last
run:** run **only your own test module(s)**, never
`python -m unittest discover`. The lead runs the full suites once, at the end, on a
quiet tree. Your report must quote the exact command you ran.

| WP | Owner | Model | Files | Wave | Depends on |
|----|-------|-------|-------|------|-----------|
| C1 | web-specialist | sonnet | `webapp/core/schedule_input.py` | 1 | — |
| C2 | data-import-specialist | sonnet | `dataimporter.py` | 1 | — |
| C3 | lead | — | `shiftwork.py`, `constraint.md` | 1 | — |
| TB | test-specialist | sonnet | `webapp/tests/test_solver_runner.py` | 1 | — |
| C4 | web-specialist | sonnet | `webapp/web/app.py`, `edit_input.html`, `calendar_util.py`, `duplicate.py`, `diagnosis.py` | 2 | C1 |
| C5 | web-specialist | sonnet | `webapp/core/presolve.py` | 2 | C1, C2 |
| TD | test-specialist | sonnet | `test_shiftwork.py` | 2 | C3 |
| TA | test-specialist | sonnet | `webapp/tests/test_schedule_input.py`, `test_tiers.py` | 3 | C1 |
| TC | test-specialist | sonnet | `webapp/tests/test_fairness.py`, `test_web.py` | 3 | C4 |
| TE | test-specialist | sonnet | `webapp/tests/test_presolve.py`, `test_calendar_util.py`, `test_duplicate.py` | 3 | C4, C5 |

### C1 — `webapp/core/schedule_input.py`

1. `_DEFAULT_SETTINGS`: add `"holidays": []` directly after `"weekends"`, with a
   comment that empty means empty and that an absent key is what keeps already-saved
   months on the weekend band. Add `coverage_holiday_{day,evening,night}` = 4/2/2
   after the weekend trio.
2. Add `_COVERAGE_BANDS = ("weekday", "weekend", "holiday")`. Rewrite the group-key
   loop at `:103-106` and `_GROUP_COVERAGE_KEYS` at `:123-128` to iterate it, so the
   group defaults are weekday 5/3/3 and weekend **and holiday** 4/2/2 → 18 keys.
3. `_LEGACY_COVERAGE_KEYS`: add the three holiday names (order as in §4).
4. `_coerce_settings`: parse `holidays` with `_parse_day_list` (`if "holidays" in raw`),
   and implement the §3.2 inheritance chain. Keep the existing weekday/weekend group
   inheritance behaviour byte-for-byte; add the holiday chain beside it. Pattern to
   copy: `webapp/core/schedule_input.py:410-417`.
5. `to_solver_csv`: emit `holidays,<space-joined>` immediately after the `weekends`
   line. The nine `_LEGACY_COVERAGE_KEYS` are emitted by the existing loop with no
   change (it already iterates the tuple and remaps by prefix) — **verify that, do
   not rewrite it**.
6. `to_dict`/`from_dict` need no change (`settings` is passed whole through
   `_coerce_settings`) — **verify and say so in your report**.

**Do not** touch `validate()`, the `Nurse` dataclass, or any other file.

### C2 — `dataimporter.py`

Apply KC1 verbatim. Nothing else. Do not change `_parse_weekends` or its
empty-string-means-default behaviour.

### C3 — `shiftwork.py`, `constraint.md` (lead)

KC2–KC6, plus `constraint.md` §22 and the §9 / §20 amendments.

### TB — `webapp/tests/test_solver_runner.py`

Tests A6, A7, A8, B13 of §6. **Lift the fixtures verbatim** from
`C:\Users\ACER NITRO I5 9th\AppData\Local\Temp\claude\D--Claude-ShiftWork\30469c81-4400-4dad-b239-9bd0329ba801\scratchpad\wp4_check.py`
(its `ward()` helper and the T6/T7/T8 bodies are already exactly these tests,
including the `mock.patch.object(solver_runner, "_run_one", fake)` budget probe and
the ungrouped single-call assertion). Convert the `assert`/`print` style to
`unittest` methods in the existing file's style. Keep `pandas` out if the existing
file does not already import it — assert on `csv_text` by splitting lines instead.
Run: `python -m unittest webapp.tests.test_solver_runner`.

### C4 — `app.py`, `edit_input.html`, `calendar_util.py`, `duplicate.py`, `diagnosis.py`

1. `calendar_util.month_dates`: add `"holiday_days": sorted(set(holiday_days) | set(extra))`
   to the returned dict; keep `combined` and every other key. Update the module
   docstring: `combined` is now legacy, `holiday_days` feeds the holiday band.
2. `app.py` auto-fill (`:370-373`): set `settings["weekends"] = md["weekends"]` and
   `settings["holidays"] = md["holiday_days"]`. Update the route docstring.
3. `app.py._settings_from_form`: add `holidays` using the **coverage pattern**, not
   the `weekends` pattern — `form.get("holidays")` when present, else
   `current["holidays"]` when `current` is given and has it, else omit the key. An
   absent field must never silently clear a ward's holidays.
4. `app.py._render_edit`: `coverage_defaults` gains
   `"holiday": {"day": 4, "evening": 2, "night": 2}`; add
   `"holidays_str": " ".join(str(d) for d in schedule_input.settings.get("holidays", []))`.
5. `app.py._fairness_for_job`: `weekends = sorted(set(settings.get("weekends") or ())
   | set(settings.get("holidays") or ()))` — the union, so the fairness weekend
   column keeps counting holidays as it does today.
6. `duplicate.next_month_input`: in the dated branch set
   `settings["holidays"] = md["holiday_days"]` beside `settings["weekends"] = md["weekends"]`
   (**change `md["combined"]` to `md["weekends"]` here too**). Undated branch
   unchanged. Update the module docstring.
7. `diagnosis.render_text`: KC7.
8. `edit_input.html`: add the `("holiday", "วันหยุดพิเศษ Holiday")` band to `bands`
   (`:118`); add a `holidays` text input beside the existing `weekends` /
   `extra_holidays` inputs using `holidays_str`, with the §3.5 hint; change the
   auto-fill button label (`:112-113`) and its hint to mention holidays.

**Do not** touch `fairness.py`, `presolve.py`, `schedule_input.py` or any test.

### C5 — `webapp/core/presolve.py`

Apply KC8. Then audit **every** remaining `ctx["weekends"]` / `weekends` read in the
file against the §3.3 table and leave the union-reading ones alone. `checks_total`
(`:106`) is unchanged — no new check is added. Known failure mode 9: this file
mirrors the solver's hard rules, and after KC5 the solver's band precedence is
holiday > weekend > weekday; `_day_band` must agree exactly.

### TD — `test_shiftwork.py`

Tests B9, B10, B12 and C1–C4 of §6. **Lift the fixtures** from
`…\scratchpad\smoke_offward.py` — its `ward_csv(num_days, rows, cov, senior)` builder
and `run(...)` temp-dir/chdir/stdout-capture harness are exactly what these tests
need; extend `ward_csv` with `holidays=""` and a `cov_holiday=None` argument
(defaulting to `cov`, so existing calls are unaffected).
Run: `python -m unittest test_shiftwork`.

### TA — `webapp/tests/test_schedule_input.py`, `test_tiers.py`

Tests A1–A5, A7(tiers half), B14 and C5–C6 of §6. The `ward()` helper and the tier
assertions in `…\scratchpad\wp4_check.py` (T7 block) and `…\scratchpad\verify_wp1.py`
have ready fixtures. Run: `python -m unittest webapp.tests.test_schedule_input
webapp.tests.test_tiers`.

### TC — `webapp/tests/test_fairness.py`, `test_web.py`

Tests B11, A/B UI round-trip and C8 of §6. **Lift from** `…\scratchpad\wp5_check.py`,
which is already a `unittest.TestCase` subclassing the real
`webapp.tests.test_web.WebTestBase` and using its `_make_job` / `_make_input`
helpers — the four test bodies there map onto the §6 tests almost unchanged.
**Note:** its coverage-input counts (`n_cov == 6` ungrouped, `== 12` grouped) become
**9 and 18** after C4. Run:
`python -m unittest webapp.tests.test_fairness webapp.tests.test_web`.

### TE — `webapp/tests/test_presolve.py`, `test_calendar_util.py`, `test_duplicate.py`

Tests B15, B16, A-grouped-presolve and C7, C9 of §6. **Lift from**
`…\scratchpad\prove_wp6.py`, which already contains every one of these as a
`si()`-helper fixture plus real-solver cross-checks. Run:
`python -m unittest webapp.tests.test_presolve webapp.tests.test_calendar_util
webapp.tests.test_duplicate`.

---

## 6. Acceptance tests

Each must **fail if its rule is removed**. State in your report which ones you
mutation-checked and how.

### Track A (regression cover for what shipped in `43b6d93`)

- **A1 `test_ungrouped_solver_csv_is_exact`** (`test_schedule_input`): no nurse has a
  `staff_class` → `to_solver_csv()` equals a literal containing
  `weekends,2`, `holidays,`, `coverage_weekday_day,5`, `coverage_holiday_day,4`, and
  header `Name,Type,1,2,3` with **no** `Class` column and **no** `coverage_rn_*` /
  `coverage_pnna_*` line. *Fails if* group keys leak into the ungrouped emission or a
  `Class` column appears for an unclassed ward.
- **A2 `test_group_csv_contains_only_that_group`**: 4 RN + 3 PN + 1 NA →
  `to_solver_csv(group="rn")` has 4 schedule rows, `group="pn_na"` has 4.
- **A3 `test_group_csv_uses_group_coverage`**: `coverage_pnna_weekday_day=2` →
  `to_solver_csv(group="pn_na")` contains `coverage_weekday_day,2` **and**
  `head_nurse_special_shift,false`. *Fails if* the remap or the senior override drops.
- **A4 `test_legacy_coverage_is_inherited`**:
  `_coerce_settings({"coverage_weekday_night": 1})` → `coverage_rn_weekday_night ==
  coverage_pnna_weekday_night == 1`.
- **A5 `test_mixed_staff_class_is_rejected`**: one RN + one blank active nurse →
  `validate()` raises, message names the blank nurse.
- **A6 `test_two_groups_produce_two_group_grids`** (`test_solver_runner`): 6 RN + 6
  PN, 7 days, per-group coverage 2/1/1 → `solved`; `grid["groups"]` has 2 entries
  with disjoint `nurse_indices` whose union is `range(12)`; `len(grid["rows"]) == 12`;
  every row has a non-empty `group`; `[r["nurse_index"] for r in rows] == list(range(12))`;
  `csv_text`'s header is `,1,…,7` and it has 12 data rows indexed 0..11.
  *Fails if* the merge or the group-local→global index remap is wrong.
- **A7 `test_combined_tier_cap_blocks_split`** (`test_tiers` + `test_solver_runner`):
  free tier (cap 8), 5 RN + 5 PN active → `tiers.enforce_max_nurses` raises **and**
  `run_schedule` raises; 4 RN + 3 PN + 2 NA (= 9) also raises, proving NA counts;
  `tier_view["by_group"] == {"rn": 5, "pn_na": 5}` with `nurse_count == 10`.
  *Fails if* the cap is left to `DataImporter`'s per-file check — each group of 5
  would pass. This is the guard on the combined-ceiling decision.
- **A8 `test_budget_is_split_across_groups`** (`test_solver_runner`): patch
  `solver_runner._run_one` to record its arguments; two groups,
  `max_time_seconds=100, diagnose_seconds=20, relax_seconds=10` → calls are
  `("rn",50,10,5)` then `("pn_na",50,10,5)`; an ungrouped input → exactly one call
  `(None,100,20,10)`. *Fails if* the split is removed, which would breach
  `WORKER_JOB_TIMEOUT_SECONDS`.

### Track B (regression cover for off-ward duties)

- **B9 `test_offward_shift_is_forced_and_exclusive`** (`test_shiftwork`): `ref:ด` on
  day 3 → that nurse's day-3 NIGHT var is 1 and DAY, EVENING **and OFF** are 0.
- **B10 `test_offward_excluded_from_coverage`** (`test_shiftwork`): 2 nurses, 1 day,
  coverage 1/1/1 solves; the same ward with nurse A's cell set to `flt:ช` is
  **INFEASIBLE**. *Fails if* the nurse stays in the coverage pool — it would solve.
  **This is the test the whole feature rests on.**
- **B11 `test_offward_counts_as_a_worked_shift`** (`test_fairness`): row
  `["flt:ด","off"]` → `night == 1`, `shifts == 1`, `offward_days == 1`,
  `off_days == 1` (the `off` cell only), and `totals["offward_days"] == 1` with
  `has_offward` true. *Fails if* `_parse_cell` does not know the token — today an
  unknown cell reads as a day off.
- **B12 `test_offward_senior_conflict_is_skipped`** (`test_shiftwork`): `ref:ด` on
  nurse 0 with `head_nurse_special_shift` on → still solves; the cell is not
  `ref:ด`; stdout contains `skipped (conflicts with senior nurse rule)`;
  `_offward_applied` has no entry for nurse 0 and `_offward_excluded` is empty.
  *Fails if* the skip-guard is removed — known failure mode 1.
- **B13 `test_offward_render_round_trips`** (`test_solver_runner`): a solved grid
  containing an off-ward cell → every cell is in `VALID_CELL_TOKENS`.
- **B14 `test_bare_flt_rejected`** (`test_schedule_input`): `shifts={1:"flt"}` →
  `validate()` raises, message names `flt:ช`. Also `FLT:ด` normalises to `flt:ด`.
- **B15 `test_presolve_no_false_blockers_with_offward`** (`test_presolve`): a feasible
  ward with `flt:บ` and `ref:ช` → `check()` returns no errors, cross-checked against
  a real `run_schedule` that solves. Guards failure mode 9.
- **B16 `test_presolve_offward_transition_error`** (`test_presolve`): `flt:ด` day 4 +
  ward `ช` day 5 with `allow_night_to_day=false` → exactly one `request_transition`
  error; with `allow_night_to_day=true` → no errors.
- **B17 `test_presolve_senior_offward_is_a_warning`** (`test_presolve`): `ref:ช` on
  nurse 0 on a weekend and `flt:ด` on nurse 1 → two `senior_offward` **warnings**,
  **zero** errors, and the real solver still solves. *Fails if* it is promoted to an
  error (presolve errors must be sound).
- **B18 `test_presolve_grouped_messages_and_bands`** (`test_presolve`): 4 RN + 3 PN
  with `coverage_pnna_weekday_day=4` → an error whose message starts `"PN/NA: "` and
  none starting `"RN: "`; a `senior_offward` warning starting `"RN: "`;
  `checks_total == 7`; and a grouped free-tier ward over the combined cap raises
  `tier_nurses` exactly **once**.

### Track C (the holiday band)

- **C1 `test_holiday_band_defaults_to_weekend`** (`test_shiftwork`): a ward whose
  `[settings]` has `holidays,3` and **no** `coverage_holiday_*` keys, with
  `coverage_weekend_day,2` → `Solver.coverage['holiday'][Shift.DAY] == 2` and
  `solver.days[2]['min_shifts'][Shift.DAY] == 2`. *Fails if* the inheritance default
  is a hard-coded 4 — an existing ward's holiday would change staffing.
- **C2 `test_holiday_band_is_applied`** (`test_shiftwork`): identical wards except
  `coverage_holiday_day`; sized so the holiday day is feasible at the weekend number
  and **INFEASIBLE** one higher → status flips. *Fails if* `_rebuild_days` ignores
  `self.holidays`.
- **C3 `test_holiday_wins_over_weekend_band`** (`test_shiftwork`): day 6 is in **both**
  `weekends` and `holidays`, with different weekend and holiday numbers →
  `days[5]['min_shifts']` carries the **holiday** numbers.
- **C4 `test_holiday_is_non_working_for_senior`** (`test_shiftwork`): a Monday in
  `holidays` but **not** in `weekends`, `head_nurse_special_shift` on → nurse 0's OFF
  var for that day is 1. *Fails if* `self.weekends` stops being the union — the
  senior rule would start scheduling head nurses on public holidays, a silent
  behaviour change for every existing ward.
- **C5 `test_holidays_setting_round_trips`** (`test_schedule_input`): `holidays` set
  on a ward survives `to_solver_csv` → `DataImporter` as 0-based days, and
  `_coerce_settings({})` leaves it `[]` while `_coerce_settings({"holidays": ""})`
  also gives `[]`. *Fails if* the empty-means-default behaviour of `weekends` is
  copied onto `holidays`.
- **C6 `test_holiday_coverage_inheritance_chain`** (`test_schedule_input`): four
  cases, asserting the §3.2 order — (a) `{"coverage_weekend_night": 1}` →
  `coverage_holiday_night == coverage_rn_holiday_night ==
  coverage_pnna_holiday_night == 1`; (b) `{"coverage_rn_weekend_night": 1}` →
  `coverage_rn_holiday_night == 1` and `coverage_pnna_holiday_night == 2` (default);
  (c) an explicit `coverage_rn_holiday_night` beats both; (d) `{}` → all holiday keys
  are 4/2/2. *Fails if* any link of the chain is dropped or reordered.
- **C7 `test_autofill_twice_does_not_change_the_schedule`** (`test_web`): **the
  behaviour-preservation test.** Make a dated input, auto-fill once (pre-change
  semantics are simulated by setting `weekends = md["combined"]`, `holidays` absent),
  record the solved grid; then auto-fill again through the real route and solve
  again → the two grids' per-day coverage requirement is identical, and the second
  input's `weekends` no longer contains the public-holiday day while `holidays` does.
  A cheaper equivalent is acceptable and preferred if a solve is too slow: assert
  that `presolve`'s resolved band numbers per day are identical before and after.
  *Fails if* the holiday band does not inherit the weekend band.
- **C8 `test_edit_page_renders_three_bands`** (`test_web`): the edit page has
  **9** `name="coverage_` inputs for an unclassed ward and **18** once every nurse is
  classed; it has a `name="holidays"` input; saving a form that omits the PN/NA
  coverage fields does not clobber their stored values (the §4 C4.3 no-clobber rule),
  and saving a form that omits `holidays` entirely leaves the stored list intact.
- **C9 `test_month_dates_holiday_days`** + **`test_duplicate_carries_holidays`**
  (`test_calendar_util`, `test_duplicate`): `month_dates` returns `holiday_days` =
  public ∪ extra and still returns `combined` = the full union;
  `next_month_input` on a dated ward sets `weekends` to Sat/Sun only and `holidays`
  to the new month's `holiday_days`, with `extra_holidays == []`.

**Suites that must stay green** (lead runs these, from the repo root):

```
python -m unittest discover -s webapp/tests -p "test_*.py"
python -m unittest test_shiftwork
```

The Windows run reports ~49 `PermissionError`s in `tearDown` (temp sqlite files held
open). **Pre-existing harness artifact, not a failure.** Do not chase them; a real
failure surfaces as some other exception type.

---

## 7. Risks

Known failure modes touched (names from `CODEBASE.md` → "Known failure modes"):

- **1. Constraint vs request conflict → silent infeasibility.** Track C adds **no new
  request-restricting rule**, so it needs no new skip-guard — the holiday band only
  changes an integer on the right-hand side of an existing coverage constraint, and a
  too-high holiday floor surfaces as a `coverage` diagnosis (named by band, KC6/KC7)
  and as presolve's existing `coverage_day` error. Guarded by C2 and B10.
- **2. Senior rules cover BOTH head (0) and deputy (1).** Untouched, *provided*
  `self.weekends` stays the union. C4 is the test that catches a regression here.
- **3. `mtg` days** — untouched; the general `excluded` set is not changed.
- **4. `vac` is protected** — untouched.
- **5. Tier resolution** — untouched.
- **6. Input contract.** `holidays` and three `coverage_holiday_*` keys are **additive
  optional** `[settings]` lines; a CSV or xlsx without them parses exactly as today.
  No column changes, no cell-token changes. CSV and Excel must still round-trip
  identically through `to_solver_csv()` (A1, C5).
- **7. Half-wired settings.** `holidays` must pass every layer: solver (KC2–KC5) →
  `dataimporter` (KC1) → `schedule_input` (C1) → form (C4.3) → template (C4.8) →
  display (`holidays_str`) → tests (C5, C8). The 9 legacy coverage keys likewise.
  The 18 group keys stop at `to_solver_csv`, by the same design as the existing 12.
- **8. No redundant model structure.** Track C adds **zero** variables and zero
  constraints — it changes a right-hand-side integer.
- **9. Pre-solve mirror drift.** C5 lands in the same pass as C1/C2/C3 and must
  implement the identical holiday > weekend > weekday precedence. Guarded by B15 and
  `test_presolve`'s existing real-solver cross-checks.
- **11. Staff-group split happens at `to_solver_csv`.** The 6 new group holiday keys
  ride the existing remap; nothing about the index mapping changes.

Additional risks specific to this change:

- **R4. `self.weekends` now means "weekend or holiday".** The name is a lie and the
  comment in KC2 is the only thing that says so. Accepted deliberately over renaming:
  a rename touches four correct call sites in the solver's most delicate rules for no
  behaviour gain. If anyone later adds a rule that needs *calendar* weekends only,
  they must use `self.weekends - self.holidays`. Recorded in `constraint.md` §22.
- **R5. Re-running auto-fill is only behaviour-preserving while the bands are equal.**
  That is the design, and once a ward edits its holiday numbers the change is
  intentional and visible. C7 guards the equal case.
- **R6. 18 coverage inputs.** Accepted for now (§1). The single-class ward still sees
  9 fields, of which 3 are new.
- **R7 (pre-existing, out of scope).** `webapp/core/relaxation.py` still does not
  exist while `solver_runner.py:195` imports it behind an `except Exception`, so
  relaxation suggestions never appear. Reported again, not fixed here.

**What the lead verifies at review:** every diff's file list matches §4; `self.weekends`
is the union at every one of the four §3.3 call sites; C2 genuinely flips status when
the band lookup is reverted; `_parse_holidays` does **not** fall back to the default
weekends; `_day_band` agrees with `_rebuild_days`; no test weakened; every lifted
fixture asserts rather than prints; and each specialist's report quotes the module-only
test command it ran.

## 8. Do NOT

- Edit files outside your WP's list. Propose the diff in your report instead.
- Run `python -m unittest discover`. Run only your own module(s). (Last run, two
  specialists reported contradictory results for the same test because they ran the
  whole suite while others were mid-edit.)
- Add a DB column, an Alembic migration, or any migration step. Bim has deferred
  Alembic; `holidays` lives in the existing JSON `settings`.
- Back-fill `holidays` for already-saved inputs, or derive them from the `holidays`
  library at read time (§1).
- Change `_parse_weekends`'s empty-string-means-default behaviour.
- Add dependencies, or change a signature not listed in §4.
- Rewrite or weaken a test to make it pass. If a shipped behaviour looks wrong, STOP
  and report it — do not "fix" it from a test package.
- Change solver output semantics for a ward with no `holidays`, tier/licensing
  resolution, or the CSV/Excel input contract beyond the additive optional settings
  lines in §7.6.
- Touch `webapp/core/relaxation.py`, or chase the Windows `tearDown` `PermissionError`s.
- Commit, push, or open a PR. The lead commits.

## 9. STOP and report instead of guessing if

- A named file/symbol is missing or its signature differs from §2/§4.
- The spec contradicts the code, `constraint.md`, or itself.
- An acceptance test cannot pass without breaking §8.
- You need to change a file another WP owns.
- A test you lifted from a scratch script fails on current code: that is a real
  finding, not a fixture problem. Report it with the exact assertion.

## 10. Report format

Files changed (paths only) · the exact test command(s) you ran + pass/fail counts ·
which acceptance tests you **mutation-checked** and what you reverted to check ·
deviations from spec + why · open questions. No diffs, no file contents.

## 11. Done / deviations log

- [ ] Acceptance tests + both suites green (run by the lead on a quiet tree)
- [ ] `constraint.md` §22 + §9/§20 amendments, `CODEBASE.md` updated
- [ ] Status → done

Deviations:
