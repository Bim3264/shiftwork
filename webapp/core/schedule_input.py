"""ScheduleInput — the one canonical representation of a ward's solve input.

Both entry points (CSV upload and the in-browser grid) normalize into this
shape, and everything downstream — storage (JSON), the grid renderer, and the
solver adapter — reads it. Keeping a single representation is what stops the
upload path and the grid path from drifting apart.

The representation deliberately stores the *raw* schedule grid (the shift
symbols exactly as a user typed them), NOT the solver's post-processed request
structures. DataImporter samples/merges requests down to tier limits when it
builds the model; that transformation belongs to solving, not to the input we
store and let users re-edit.

Parsing of the sectioned CSV format reuses DataImporter's own section/key-value
parsers so the two never disagree about the file format.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any

from constant import (
    OFFWARD_SEP,
    OFFWARD_TOKENS,
    NEGATE_KIND,
    NEGATE_TOKENS,
    SHIFT_LOCALE,
    LocaleShift,
    parse_negation,
    parse_offward,
)
from dataimporter import DataImporter

# Staff classes (RN vs PN/NA). The roster splits into groups that are solved as
# independent runs; the split happens at the to_solver_csv() boundary so the
# solver itself never learns about staff classes.
GROUP_RN = "rn"
GROUP_PN_NA = "pn_na"
GROUP_LABELS = {GROUP_RN: "RN", GROUP_PN_NA: "PN/NA"}
STAFF_CLASSES = ("rn", "pn", "na")
_GROUP_ORDER = (GROUP_RN, GROUP_PN_NA)


def group_of(staff_class: str) -> str:
    """Solve group for a staff class: rn -> 'rn', pn/na -> 'pn_na', else ''."""
    sc = str(staff_class or "").strip().lower()
    if sc == "rn":
        return GROUP_RN
    if sc in ("pn", "na"):
        return GROUP_PN_NA
    return ""

# Tokens a schedule cell may contain. Blank means "no request". 'off'/'mtg' are
# control tokens; the rest are the Thai shift symbols the solver understands.
_SHIFT_SYMBOLS = {
    LocaleShift.DAY.value,            # ช
    LocaleShift.EVENING.value,        # บ
    LocaleShift.NIGHT.value,          # ด
    LocaleShift.DAY_EVENING.value,    # ช/บ
    LocaleShift.NIGHT_EVENING.value,  # ด/บ
}
_CONTROL_TOKENS = {"off", "mtg", "vac"}
# Off-ward duties (float / refer) are written 'flt:ช' / 'ref:ด' etc.
VALID_CELL_TOKENS = _SHIFT_SYMBOLS | _CONTROL_TOKENS | OFFWARD_TOKENS | NEGATE_TOKENS | {""}
# The bare kind words ('flt', 'ref') are NOT valid on their own: a shift is required.
_OFFWARD_KINDS = {t.partition(OFFWARD_SEP)[0] for t in OFFWARD_TOKENS} | {NEGATE_KIND}

# Settings the constraint selector exposes, with their defaults. weekends are
# stored 1-based (as written in the CSV, e.g. "6 7 13 14"); everything that
# needs 0-based (the Solver constructor) converts at the boundary.
_DEFAULT_SETTINGS: dict[str, Any] = {
    "num_days": 31,
    "weekends": [6, 7, 13, 14, 20, 21, 27, 28],
    # Special-holiday day-numbers (1-based), staffed by the 'holiday' coverage
    # band. Empty means EMPTY (parsed with _parse_day_list, not like weekends).
    # An absent key in a saved input defaults to [] - that is the whole migration:
    # every day already in `weekends` keeps the weekend band (constraint.md 22).
    "holidays": [],
    # Saturday / Sunday day-numbers (1-based) selecting the 'saturday' /
    # 'sunday' coverage bands. `weekends` stays the union (non-working days);
    # these two only pick the band. Absent -> [] so an existing weekend day
    # keeps the weekend band (constraint.md 22).
    "saturdays": [],
    "sundays": [],
    "allow_evening_to_night": False,
    "allow_night_to_day": False,
    "head_nurse_special_shift": True,
    # Fatigue rules (constraint.md 24). Both OFF by default: a new hard rule
    # that defaulted on would change every existing ward's schedule.
    "limit_consecutive_nights": False,
    "max_consecutive_nights": 3,
    "no_consecutive_day_eve": False,
    # Partial request acceptance: 100 = every request required (default);
    # below 100 the solver may drop requests to reach feasibility, keeping as
    # many as possible. relax_days_off allows dropping requested days off too.
    "min_request_percent": 100,
    # Per-staff-group thresholds (RN / PN/NA are solved independently). Absent
    # from a saved input -> inherited from min_request_percent (_coerce_settings).
    "min_request_percent_rn": 100,
    "min_request_percent_pnna": 100,
    # On by default: an ordinary requested day off should be the EASY thing to
    # give up when a month is tight. A saved input keeps whatever it stored, so
    # only new wards get this.
    "relax_days_off": True,
    # When True (default) every requested vacation ('vac' cell) is guaranteed —
    # a hard constraint the solver can't drop. When False, vacations become
    # droppable, but still only if relax_days_off is on: approved leave must
    # never be easier to drop than an ordinary off-request. Order of hardness:
    # shifts < off < vacation.
    "enforce_vacation": True,
    # Manually-added holiday day-numbers (1-based). Merged into `weekends` by the
    # month auto-fill; holidays behave like weekends for the solver.
    "extra_holidays": [],
    # Per-shift coverage minimums (nurses required per shift), by day band.
    # Defaults preserve the original hard-coded values.
    "coverage_weekday_day": 5,
    "coverage_weekday_evening": 3,
    "coverage_weekday_night": 3,
    "coverage_weekend_day": 4,
    "coverage_weekend_evening": 2,
    "coverage_weekend_night": 2,
    # The holiday band defaults to the weekend numbers (inherited, see
    # _coerce_settings) so a holiday is staffed as a weekend until the ward says
    # otherwise.
    "coverage_holiday_day": 4,
    "coverage_holiday_evening": 2,
    "coverage_holiday_night": 2,
    # Saturday / Sunday bands inherit the weekend numbers (see _coerce_settings).
    "coverage_saturday_day": 4,
    "coverage_saturday_evening": 2,
    "coverage_saturday_night": 2,
    "coverage_sunday_day": 4,
    "coverage_sunday_evening": 2,
    "coverage_sunday_night": 2,
}
# Day bands, in precedence-neutral listing order (holiday > sunday > saturday >
# weekend/weekday is applied by the solver, not here).
_COVERAGE_BANDS = ("weekday", "weekend", "saturday", "sunday", "holiday")
# Per-staff-group coverage (RN vs PN/NA). Defaults equal the legacy values; a
# saved input that only has the legacy keys inherits them (see _coerce_settings).
for _grp in ("rn", "pnna"):
    for _band, _defaults in (
        ("weekday", (5, 3, 3)), ("weekend", (4, 2, 2)),
        ("saturday", (4, 2, 2)), ("sunday", (4, 2, 2)), ("holiday", (4, 2, 2)),
    ):
        for _shift, _val in zip(("day", "evening", "night"), _defaults):
            _DEFAULT_SETTINGS[f"coverage_{_grp}_{_band}_{_shift}"] = _val

_BOOL_SETTING_KEYS = (
    "allow_evening_to_night",
    "allow_night_to_day",
    "head_nurse_special_shift",
    "relax_days_off",
    "enforce_vacation",
    "limit_consecutive_nights",
    "no_consecutive_day_eve",
)

# The fifteen legacy coverage keys: the only ones the solver/DataImporter know.
_LEGACY_COVERAGE_KEYS = (
    "coverage_weekday_day", "coverage_weekday_evening", "coverage_weekday_night",
    "coverage_weekend_day", "coverage_weekend_evening", "coverage_weekend_night",
    "coverage_holiday_day", "coverage_holiday_evening", "coverage_holiday_night",
    "coverage_saturday_day", "coverage_saturday_evening", "coverage_saturday_night",
    "coverage_sunday_day", "coverage_sunday_evening", "coverage_sunday_night",
)
# Prefix of a staff group's coverage keys (suffix = legacy key minus "coverage_").
_GROUP_COVERAGE_PREFIX = {GROUP_RN: "coverage_rn_", GROUP_PN_NA: "coverage_pnna_"}
_GROUP_COVERAGE_KEYS = tuple(
    f"coverage_{grp}_{band}_{shift}"
    for grp in ("rn", "pnna")
    for band in _COVERAGE_BANDS
    for shift in ("day", "evening", "night")
)

# Per-group acceptance-floor keys (legacy key: "min_request_percent").
_GROUP_MIN_REQUEST_KEY = {GROUP_RN: "min_request_percent_rn",
                          GROUP_PN_NA: "min_request_percent_pnna"}
_MIN_REQUEST_KEYS = ("min_request_percent",) + tuple(_GROUP_MIN_REQUEST_KEY.values())

# Integer settings clamped to >= 0 (coverage minimums): legacy + per-group.
_COVERAGE_KEYS = _LEGACY_COVERAGE_KEYS + _GROUP_COVERAGE_KEYS


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() == "true"


# Bilingual card labels are written "English / ไทย". The Thai half is dropped —
# but ONLY when the part after a "/" actually contains Thai characters. A plain
# split("/")[0] also truncated "PN/NA weekday need" to "pn", which matched no
# branch and silently discarded both per-group coverage rows that the shipped
# templates already carry.
_THAI_RE = re.compile("[\u0E00-\u0E7F]")

# Accepted spellings of the PN/NA staff-group prefix on a SETTINGS card label.
# "pn/na" is what the shipped templates use; the others are accepted so a future
# rename is non-breaking in either direction.
_PNNA_PREFIXES = ("pn/na", "pn-na", "pn na")


def _label_key(raw) -> str:
    """Normalize a bilingual WARD INFO / SETTINGS card label to its English key."""
    parts = _cell_str(raw).split("/")
    kept = [parts[0]]
    for part in parts[1:]:
        if _THAI_RE.search(part):
            break
        kept.append(part)
    return "/".join(kept).strip().lower()


def _parse_weekends_1based(value: str) -> list[int]:
    """Parse a '6 7 13 14' style string into a sorted list of 1-based ints."""
    if not str(value).strip():
        return list(_DEFAULT_SETTINGS["weekends"])
    parts = str(value).replace(",", " ").split()
    return sorted(int(p) for p in parts if p.strip())


def _parse_day_list(value) -> list[int]:
    """Parse a space/comma list of day numbers; empty → [] (unlike weekends)."""
    if isinstance(value, list):
        return sorted(int(x) for x in value)
    if not str(value).strip():
        return []
    return sorted(int(p) for p in str(value).replace(",", " ").split() if p.strip())


@dataclass
class Nurse:
    """One row of the grid: a name, a type, and any requested cells.

    shifts maps a 1-based day number to the raw cell token for that day. Days
    with no request are simply absent from the dict.

    active=False means the nurse is disabled: kept in the roster (name, type and
    requests preserved so they can be re-enabled) but excluded from the generated
    schedule. This is the "remove without deleting" behaviour.

    staff_class is an axis orthogonal to `type` (seniority): "rn", "pn", "na" or
    "" (unclassed). Stored lower-case; an unknown value becomes "".
    """

    name: str
    type: str = ""
    shifts: dict[int, str] = field(default_factory=dict)
    active: bool = True
    staff_class: str = ""

    def __post_init__(self) -> None:
        sc = str(self.staff_class or "").strip().lower()
        self.staff_class = sc if sc in STAFF_CLASSES else ""


@dataclass
class ScheduleInput:
    ward_meta: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_SETTINGS))
    nurses: list[Nurse] = field(default_factory=list)

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, text: str) -> "ScheduleInput":
        """Parse a sectioned (or legacy) ShiftWork CSV into a ScheduleInput.

        Reuses DataImporter's section and key/value parsers so the file format
        is interpreted identically to the solver's own import path.
        """
        # _parse_sections reads a filename, so stage the text in a temp file.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        try:
            sections, _has_sections = DataImporter._parse_sections(tmp_path)
        finally:
            os.unlink(tmp_path)

        ward_meta = DataImporter._parse_kv_section(sections.get("ward", []))
        raw_settings = DataImporter._parse_kv_section(sections.get("settings", []))
        settings = cls._coerce_settings(raw_settings)

        schedule_lines = sections.get("schedule", [])
        nurses = cls._parse_schedule_rows(schedule_lines, settings["num_days"])

        return cls(ward_meta=ward_meta, settings=settings, nurses=nurses)

    @classmethod
    def from_grid(
        cls,
        ward_meta: dict[str, str],
        settings: dict[str, Any],
        nurses: list[Nurse],
    ) -> "ScheduleInput":
        """Build from grid-editor form data. Coerces settings the same way the
        CSV path does so both routes produce an identical object."""
        return cls(
            ward_meta=dict(ward_meta),
            settings=cls._coerce_settings(settings),
            nurses=list(nurses),
        )

    @classmethod
    def from_xlsx(cls, data) -> "ScheduleInput":
        """Parse a filled ShiftWork Excel template (its Roster sheet) straight
        into a ScheduleInput — no CSV round-trip (option 2).

        The WARD INFO / SETTINGS cards are read by their label text (the English
        half of the bilingual "English / ไทย" labels), so inserting rows in the
        cards doesn't break parsing; the day grid is located by its row of day
        numbers. Produces the same object shape as from_csv / from_grid, so the
        rest of the pipeline can't tell which entry point was used.
        """
        try:
            import openpyxl  # lazy import: only the Excel path needs it
        except ImportError as exc:  # pragma: no cover
            raise ValueError(
                "Excel upload isn't available on this server (openpyxl missing)."
            ) from exc

        src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
        try:
            wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
        except Exception as exc:
            raise ValueError(
                f"That doesn't look like a readable .xlsx file ({exc})."
            ) from exc
        try:
            ws = wb["Roster"] if "Roster" in wb.sheetnames else wb[wb.sheetnames[0]]
            rows = list(ws.iter_rows(values_only=True))
        finally:
            wb.close()

        def gv(r, c):  # 1-based coordinate access into the materialized grid
            if 1 <= r <= len(rows):
                row = rows[r - 1]
                if 1 <= c <= len(row):
                    return row[c - 1]
            return None

        # --- WARD INFO + SETTINGS cards (label-driven) ---
        ward_meta: dict[str, str] = {}
        settings: dict[str, Any] = {}
        for r in range(1, min(len(rows), 40) + 1):
            # Left card: label col A, value col C.  Right card: label col H, value col P.
            for lc, vc in ((1, 3), (8, 16)):
                label = _label_key(gv(r, lc))
                if not label:
                    continue
                val = gv(r, vc)
                if label == "ward name":
                    ward_meta["ward_name"] = _cell_str(val)
                elif label == "hospital":
                    ward_meta["hospital"] = _cell_str(val)
                elif label == "head nurse":
                    ward_meta["head_nurse"] = _cell_str(val)
                elif label == "line contact":
                    ward_meta["line"] = _cell_str(val)
                elif label == "month":
                    ward_meta["month"] = _cell_str(val)
                elif label == "year":
                    ward_meta["year"] = _cell_str(val)
                elif label == "plan":
                    ward_meta["tier"] = _cell_str(val)
                elif label == "days in month":
                    if _cell_str(val):
                        settings["num_days"] = int(float(val))
                elif label == "rest days":
                    settings["weekends"] = _parse_weekends_1based(_cell_str(val))
                elif label.startswith("allow") and "evening" in label:
                    if val is not None:
                        settings["allow_evening_to_night"] = _yesno(val)
                elif label.startswith("allow") and "day" in label:
                    if val is not None:
                        settings["allow_night_to_day"] = _yesno(val)
                elif label.startswith("limit consecutive nights"):
                    if val is not None:
                        settings["limit_consecutive_nights"] = _yesno(val)
                elif label.startswith("max consecutive nights"):
                    if _cell_str(val):
                        settings["max_consecutive_nights"] = max(1, int(float(val)))
                elif label.startswith("no consecutive day"):
                    if val is not None:
                        settings["no_consecutive_day_eve"] = _yesno(val)
                elif "special shift" in label:
                    if val is not None:
                        settings["head_nurse_special_shift"] = _yesno(val)
                elif label.startswith("special holidays") or label == "holidays":
                    # Empty means EMPTY (constraint.md 22 / known failure mode
                    # 11), never the weekends DEFAULT — hence _parse_day_list.
                    settings["holidays"] = _parse_day_list(_cell_str(val))
                elif label.startswith(_PNNA_PREFIXES) and "need" in label:
                    # Per-staff-group coverage. Only the pnna_* keys are written:
                    # leaving rn_* absent is what lets _coerce_settings inherit
                    # the general "Weekday/Weekend need" rows for RN, which is
                    # exactly how the SETTINGS card is laid out.
                    band = ("holiday" if "holiday need" in label
                            else "saturday" if "saturday need" in label
                            else "sunday" if "sunday need" in label
                            else "weekend" if "weekend need" in label
                            else "weekday" if "weekday need" in label else None)
                    if band is not None:
                        d, e, n = _parse_triple(val)
                        settings.update({
                            f"coverage_pnna_{band}_day": d,
                            f"coverage_pnna_{band}_evening": e,
                            f"coverage_pnna_{band}_night": n,
                        })
                elif label in ("saturdays", "sundays"):
                    # Band-selecting day lists (constraint.md 22). Empty means
                    # EMPTY: those days keep the weekend band.
                    settings[label] = _parse_day_list(_cell_str(val))
                elif label.startswith(("saturday need", "sunday need")):
                    band = label.split()[0]
                    d, e, n = _parse_triple(val)
                    settings.update({f"coverage_{band}_day": d,
                                     f"coverage_{band}_evening": e,
                                     f"coverage_{band}_night": n})
                elif label.startswith("holiday need"):
                    d, e, n = _parse_triple(val)
                    settings.update(coverage_holiday_day=d,
                                    coverage_holiday_evening=e,
                                    coverage_holiday_night=n)
                elif label.startswith("weekday need"):
                    d, e, n = _parse_triple(val)
                    settings.update(coverage_weekday_day=d,
                                    coverage_weekday_evening=e,
                                    coverage_weekday_night=n)
                elif label.startswith("weekend need"):
                    d, e, n = _parse_triple(val)
                    settings.update(coverage_weekend_day=d,
                                    coverage_weekend_evening=e,
                                    coverage_weekend_night=n)

        # --- Day grid: find the day-number header row, then read nurse rows ---
        header_r = None
        day_cols: dict[int, int] = {}
        for r in range(1, len(rows) + 1):
            found: dict[int, int] = {}
            row = rows[r - 1]
            for c in range(1, len(row) + 1):
                v = row[c - 1]
                if isinstance(v, bool):
                    continue
                if isinstance(v, int):
                    found[v] = c
                elif isinstance(v, str) and v.strip().isdigit():
                    found[int(v)] = c
            if found.get(1) and found.get(2) and found.get(3):
                header_r, day_cols = r, found
                break
        if header_r is None:
            raise ValueError(
                "Couldn't find the day-number header row in the Excel grid — "
                "is this a ShiftWork roster template?"
            )

        # Optional staff-class column: a header cell starting with "class" in
        # columns 1-6 of the day-number row or the row above it.
        class_col = None
        for hr in (header_r, header_r - 1):
            for c in range(1, 7):
                if _cell_str(gv(hr, c)).lower().startswith("class"):
                    class_col = c
                    break
            if class_col is not None:
                break

        nurses: list[Nurse] = []
        for r in range(header_r + 1, len(rows) + 1):
            name = _cell_str(gv(r, 1))
            if not name:
                continue  # blank rows and the weekday-letter row carry no name
            ntype = _cell_str(gv(r, 2)).lower()
            if ntype not in ("senior", "new"):
                ntype = ""
            shifts: dict[int, str] = {}
            for day, col in day_cols.items():
                raw = gv(r, col)
                token = _normalize_token(raw) if raw is not None else ""
                if token:
                    shifts[day] = token
            staff_class = ""
            if class_col is not None:
                staff_class = _cell_str(gv(r, class_col)).split("/")[0].strip().lower()
            nurses.append(
                Nurse(name=name, type=ntype, shifts=shifts, staff_class=staff_class)
            )

        if "num_days" not in settings and day_cols:
            settings["num_days"] = max(day_cols)

        return cls.from_grid(ward_meta=ward_meta, settings=settings, nurses=nurses)

    # ── Coercion / parsing helpers ───────────────────────────────────────────

    @staticmethod
    def _coerce_settings(raw: dict[str, Any]) -> dict[str, Any]:
        s = dict(_DEFAULT_SETTINGS)
        if "num_days" in raw and str(raw["num_days"]).strip():
            s["num_days"] = int(raw["num_days"])
        if "weekends" in raw:
            wk = raw["weekends"]
            s["weekends"] = wk if isinstance(wk, list) else _parse_weekends_1based(wk)
        if "holidays" in raw:
            # _parse_day_list, NOT _parse_weekends_1based: empty means empty.
            s["holidays"] = _parse_day_list(raw["holidays"])
        for dkey in ("saturdays", "sundays"):
            if dkey in raw:
                s[dkey] = _parse_day_list(raw[dkey])
        if "extra_holidays" in raw:
            s["extra_holidays"] = _parse_day_list(raw["extra_holidays"])
        for key in _BOOL_SETTING_KEYS:
            if key in raw:
                s[key] = _to_bool(raw[key], _DEFAULT_SETTINGS[key])
        for key in _MIN_REQUEST_KEYS:
            if key in raw and str(raw[key]).strip() != "":
                try:
                    s[key] = max(0, min(100, int(raw[key])))
                except (TypeError, ValueError):
                    pass
        # Inheritance: an input that predates per-group thresholds carries only
        # the legacy key; each absent group key takes its value.
        if "min_request_percent" in raw:
            for gkey in _GROUP_MIN_REQUEST_KEY.values():
                if gkey not in raw:
                    s[gkey] = s["min_request_percent"]
        if "max_consecutive_nights" in raw and str(raw["max_consecutive_nights"]).strip() != "":
            try:
                s["max_consecutive_nights"] = max(1, int(raw["max_consecutive_nights"]))
            except (TypeError, ValueError):
                pass
        for key in _COVERAGE_KEYS:
            if key in raw and str(raw[key]).strip() != "":
                try:
                    s[key] = max(0, int(raw[key]))
                except (TypeError, ValueError):
                    pass
        # Inheritance: a saved input that predates staff groups carries only the
        # legacy coverage keys. When a group key is absent from `raw` but its
        # legacy counterpart is present, the group key takes the legacy value, so
        # every already-saved input keeps its exact numbers.
        for grp in ("rn", "pnna"):
            for band in ("weekday", "weekend"):
                for shift in ("day", "evening", "night"):
                    gkey = f"coverage_{grp}_{band}_{shift}"
                    lkey = f"coverage_{band}_{shift}"
                    if gkey not in raw and lkey in raw:
                        s[gkey] = s[lkey]
        # Holiday, Saturday and Sunday bands inherit the WEEKEND band
        # (constraint.md 22). Resolved in this exact order, first hit wins;
        # sources are tested against `raw`.
        for band in ("holiday", "saturday", "sunday"):
            for shift in ("day", "evening", "night"):
                hkey = f"coverage_{band}_{shift}"
                wkey = f"coverage_weekend_{shift}"
                # legacy: itself -> legacy weekend -> default
                if hkey not in raw and wkey in raw:
                    s[hkey] = s[wkey]
                # group: itself -> group weekend -> legacy band -> legacy
                # weekend -> default
                for grp in ("rn", "pnna"):
                    gh = f"coverage_{grp}_{band}_{shift}"
                    gw = f"coverage_{grp}_weekend_{shift}"
                    if gh in raw:
                        continue
                    if gw in raw:
                        s[gh] = s[gw]
                    elif hkey in raw:
                        s[gh] = s[hkey]
                    elif wkey in raw:
                        s[gh] = s[wkey]
        return s

    @staticmethod
    def _parse_schedule_rows(lines: list[str], num_days: int) -> list[Nurse]:
        text = "\n".join(lines).strip()
        if not text:
            return []
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return []

        header = [h.strip() for h in rows[0]]
        has_type = len(header) > 1 and header[1].lower() == "type"
        # Optional staff-class column, found by header name (non-digit, so it
        # never collides with day_cols).
        class_idx = next(
            (idx for idx, h in enumerate(header) if h.lower() == "class"), None
        )
        # Day columns are the header cells that are digits; map header text ->
        # column index so extra/re-ordered columns are handled safely.
        day_cols = {
            int(h): idx for idx, h in enumerate(header) if h.isdigit()
        }

        nurses: list[Nurse] = []
        for row in rows[1:]:
            name = row[0].strip() if len(row) > 0 else ""
            if not name:
                continue
            ntype = row[1].strip() if has_type and len(row) > 1 else ""
            shifts: dict[int, str] = {}
            for day, col in day_cols.items():
                if col < len(row):
                    token = _normalize_token(row[col])
                    if token:
                        shifts[day] = token
            staff_class = (
                row[class_idx].strip()
                if class_idx is not None and class_idx < len(row) else ""
            )
            nurses.append(
                Nurse(name=name, type=ntype, shifts=shifts, staff_class=staff_class)
            )
        return nurses

    # ── Roster ───────────────────────────────────────────────────────────────

    def active_nurses(self, group: str | None = None) -> list[Nurse]:
        """The nurses that go to the solver (disabled ones are excluded but kept
        in self.nurses). to_solver_csv() and the result-grid mapping both use
        this exact ordering. With `group` ('rn' / 'pn_na') only that staff
        group's active nurses are returned, in roster order."""
        active = [n for n in self.nurses if n.active]
        if group is None:
            return active
        return [n for n in active if group_of(n.staff_class) == group]

    def group_indices(self, group: str) -> list[int]:
        """Positions, within active_nurses(), of the nurses in `group` - used to
        remap a group-local solver row back to its global active index."""
        return [
            i for i, n in enumerate(self.active_nurses())
            if group_of(n.staff_class) == group
        ]

    def min_request_percent_for(self, group: str | None = None) -> int:
        """The acceptance floor for a solve: the group's own value, or the legacy
        key for the ungrouped (group=None) roster."""
        key = _GROUP_MIN_REQUEST_KEY[group] if group is not None else "min_request_percent"
        return self.settings[key]

    def solve_groups(self) -> list[str]:
        """The staff groups to solve independently. [] when no active nurse has a
        staff class (legacy single solve); otherwise the groups present, in the
        fixed order ['rn', 'pn_na']."""
        present = {group_of(n.staff_class) for n in self.active_nurses()}
        return [g for g in _GROUP_ORDER if g in present]

    # ── Emission ─────────────────────────────────────────────────────────────

    def to_solver_csv(self, *, group: str | None = None) -> str:
        """Emit a sectioned CSV that DataImporter/Solver parse back to this same
        schedule. This is what the solver adapter feeds to the engine.

        With group=None the output is the legacy single-roster CSV. With
        group='rn' / 'pn_na' only that group's active nurses are emitted, the
        nine solver-facing coverage keys carry that group's coverage numbers. The
        senior rule follows each nurse's Type column in every group. The solver
        never learns that staff classes exist.
        """
        if group is not None and group not in _GROUP_COVERAGE_PREFIX:
            raise ValueError(f"Unknown staff group '{group}'.")
        out = io.StringIO()

        out.write("[ward]\n")
        for key, value in self.ward_meta.items():
            out.write(f"{key},{value}\n")

        out.write("\n[settings]\n")
        out.write(f"num_days,{self.settings['num_days']}\n")
        weekends = " ".join(str(d) for d in self.settings["weekends"])
        out.write(f"weekends,{weekends}\n")
        holidays = " ".join(str(d) for d in self.settings.get("holidays", []))
        out.write(f"holidays,{holidays}\n")
        extra = " ".join(str(d) for d in self.settings.get("extra_holidays", []))
        out.write(f"extra_holidays,{extra}\n")
        for dkey in ("saturdays", "sundays"):
            days = " ".join(str(d) for d in self.settings.get(dkey, []))
            out.write(f"{dkey},{days}\n")
        for key in _BOOL_SETTING_KEYS:
            value = self.settings[key]
            out.write(f"{key},{'true' if value else 'false'}\n")
        # Solver-facing key stays the legacy name; a group run gets its own value.
        out.write(f"min_request_percent,{self.min_request_percent_for(group)}\n")
        out.write(f"max_consecutive_nights,{self.settings['max_consecutive_nights']}\n")
        for key in _LEGACY_COVERAGE_KEYS:
            if group is None:
                value = self.settings[key]
            else:
                # coverage_weekday_day -> coverage_{rn|pnna}_weekday_day
                value = self.settings[
                    _GROUP_COVERAGE_PREFIX[group] + key[len("coverage_"):]
                ]
            out.write(f"{key},{value}\n")

        out.write("\n[schedule]\n")
        num_days = self.settings["num_days"]
        writer = csv.writer(out, lineterminator="\n")
        # A Class column is emitted ONLY when some active nurse is classed, so an
        # unclassed ward's CSV stays byte-identical to the pre-staff-class format.
        # DataImporter ignores it (its day_cols filter keeps digit headers only),
        # but _parse_schedule_rows reads it back by name — which is what keeps the
        # round-trip whole for a classed ward (known failure mode 6).
        emit_class = any(n.staff_class for n in self.active_nurses())
        head = ["Name", "Type"] + (["Class"] if emit_class else [])
        writer.writerow(head + [str(d) for d in range(1, num_days + 1)])
        # Only active nurses go to the solver; disabled ones stay in self.nurses
        # but are omitted here so they don't appear in the schedule.
        for nurse in self.active_nurses(group):
            cells = [nurse.shifts.get(d, "") for d in range(1, num_days + 1)]
            lead = [nurse.name, nurse.type] + ([nurse.staff_class] if emit_class else [])
            writer.writerow(lead + cells)

        return out.getvalue()

    # ── JSON (for DB storage) ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "ward_meta": self.ward_meta,
            "settings": self.settings,
            "nurses": [
                # JSON object keys must be strings; days are re-int'd on load.
                {"name": n.name, "type": n.type, "active": n.active,
                 "staff_class": n.staff_class,
                 "shifts": {str(k): v for k, v in n.shifts.items()}}
                for n in self.nurses
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleInput":
        nurses = [
            Nurse(
                name=n["name"],
                type=n.get("type", ""),
                shifts={int(k): v for k, v in n.get("shifts", {}).items()},
                active=n.get("active", True),  # default True for pre-existing data
                staff_class=n.get("staff_class", ""),  # "" for pre-existing rows
            )
            for n in data.get("nurses", [])
        ]
        return cls(
            ward_meta=dict(data.get("ward_meta", {})),
            settings=cls._coerce_settings(data.get("settings", {})),
            nurses=nurses,
        )

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Raise ValueError with a user-friendly message if the input is unusable.

        Kept intentionally lightweight — tier limits (max nurses etc.) are
        enforced by DataImporter during the solve, not duplicated here.
        """
        num_days = self.settings["num_days"]
        if num_days < 1:
            raise ValueError("num_days must be at least 1.")
        # Only active nurses are solved, so the roster checks apply to them.
        active = self.active_nurses()
        if not active:
            raise ValueError("At least one active nurse is required.")
        # Staff classes: once any active nurse has one, every active nurse must,
        # otherwise the unclassed nurse would appear in neither group's solve and
        # silently vanish from the schedule. Inactive nurses are exempt.
        if any(n.staff_class for n in active):
            for nurse in active:
                if not nurse.staff_class:
                    raise ValueError(
                        f"Nurse '{nurse.name}' has no staff class. Set RN or "
                        f"PN/NA for every nurse, or clear them all."
                    )
        for nurse in self.nurses:
            for day, token in nurse.shifts.items():
                if not (1 <= day <= num_days):
                    raise ValueError(
                        f"Nurse '{nurse.name}' has a request on day {day}, "
                        f"outside 1..{num_days}."
                    )
                bare = token.strip().lower()
                if bare in _OFFWARD_KINDS:
                    raise ValueError(
                        f"Nurse '{nurse.name}' day {day}: '{token}' needs a shift "
                        f"— use {bare}:ช, {bare}:บ or {bare}:ด."
                    )
                if token not in VALID_CELL_TOKENS:
                    raise ValueError(
                        f"Nurse '{nurse.name}' day {day}: '{token}' is not a "
                        f"valid shift token."
                    )


def _normalize_token(raw: str) -> str:
    """Normalize a raw cell string to a canonical token.

    Control words are lower-cased ('OFF' -> 'off'); shift symbols are kept as-is.
    """
    token = str(raw).strip().replace("﻿", "")
    if token.lower() in _CONTROL_TOKENS:
        return token.lower()
    # Off-ward cells ('FLT:ด' -> 'flt:ด'); a bare 'FLT' is lower-cased but
    # stays invalid (validate() reports it - we never guess a shift).
    offward = parse_offward(token)
    if offward is not None:
        kind, shift = offward
        return f"{kind}{OFFWARD_SEP}{SHIFT_LOCALE[shift]}"
    # Negative request ('NO:บ' -> 'no:บ'); bare 'no' / 'no:off' stay invalid.
    negated = parse_negation(token)
    if negated is not None:
        return f"{NEGATE_KIND}{OFFWARD_SEP}{SHIFT_LOCALE[negated]}"
    if token.lower() in _OFFWARD_KINDS:
        return token.lower()
    return token


def _cell_str(value) -> str:
    """A trimmed string for an Excel cell value; '' for blanks; ints stay clean
    (openpyxl gives whole numbers as floats, so 31.0 -> '31', not '31.0')."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _yesno(value) -> bool:
    """Interpret a Yes/No (or true/1/✓) settings cell as a boolean."""
    return _cell_str(value).lower() in ("yes", "y", "true", "1", "✓")


def _parse_triple(value) -> tuple[int, int, int]:
    """Parse a 'D / E / N' coverage cell (e.g. '5 / 3 / 3') into three ints."""
    nums = [int(p) for p in re.findall(r"\d+", _cell_str(value))]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]
