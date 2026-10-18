"""Generate the six ShiftWork Excel roster templates from one definition.

    python -X utf8 tools/gen_templates.py            # writes Templates/*.xlsx

Why a generator: the .xlsx files are gitignored (so they cannot be reviewed or
tested), while this file is committed. The same ``build_workbook`` feeds the
shipped files and the round-trip tests, so a template that drifts from the
parser (``ScheduleInput.from_xlsx``) fails CI instead of failing a customer.

Layout (Roster sheet)
    rows 1-2     title bar
    row 4        WARD INFO (A:F) / SETTINGS (H:T) card headers
    rows 5-11    WARD INFO labels A:B / values C:F
    rows 5-16    SETTINGS labels H:O / values P:T
    row 24       SHIFT CODES header; rows 25-27 legend
    rows 28-29   grid header (day numbers / weekday letters)
    rows 30+     nurse rows   (A Nurse, B Type, C Class, D:AH days 1-31)

The cell-token dropdown is built from ``constant.py`` (never a hardcoded
string) so adding a token there without touching this file fails the anti-drift
test instead of silently shipping a stale dropdown.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Allow `python tools/gen_templates.py` as well as `import tools.gen_templates`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import openpyxl  # noqa: E402
from openpyxl.formatting.rule import FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402
from openpyxl.worksheet.properties import PageSetupProperties  # noqa: E402

from constant import (  # noqa: E402
    NEGATE_KIND,
    NEGATE_TOKENS,
    OFFWARD_SEP,
    OFFWARD_SHIFTS,
    OFFWARD_TOKENS,
    LocaleShift,
    OffWard,
)
from dataimporter import TIER_LIMITS  # noqa: E402
from webapp.core.xlsx_style import (  # noqa: E402,F401  (one shared definition)
    BAND, BLUE, BOX, CODE_STYLE, EX_MUTED, FLT_FILL, GRID, INK, LABEL_FILL,
    LEGEND_TEXT, LETTER_FILL, LETTER_INK, NEG_FILL, REF_FILL, REST_BODY, SLATE,
    TEAL, TEAL_DARK, TYPE_STYLE, WEEKEND_FILL, WEEKEND_INK, WEEKEND_LETTER, WHITE,
    CENTER as _CENTER, LEFT as _LEFT, LEFT1 as _LEFT1, col_letter,
    solid as _fill, font as _font, put as _put,
)

ROSTER_SHEET = "Roster"
GUIDE_SHEET = "Guide · วิธีใช้"

# ── Template definitions ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TemplateSpec:
    filename: str      # "ShiftWork Template — Ward (blank).xlsx"
    tier: str          # key of TIER_LIMITS: "ward" -> Guide "Plan limits" block
    nurse_rows: int    # 8 free / 15 ward / 25 ward+ / 40 pro / 50 department
    example: bool = False   # Ward (example): filled roster + filled holidays


TEMPLATES: tuple = (
    TemplateSpec("ShiftWork Template — Free (blank).xlsx", "free", 8),
    TemplateSpec("ShiftWork Template — Ward (blank).xlsx", "ward", 15),
    TemplateSpec("ShiftWork Template — Ward (example).xlsx", "ward", 15, example=True),
    TemplateSpec("ShiftWork Template — Ward+ (blank).xlsx", "ward+", 25),
    TemplateSpec("ShiftWork Template — Ward Pro (blank).xlsx", "ward pro", 40),
    TemplateSpec("ShiftWork Template — Department (blank).xlsx", "department", 50),
)

# ── Shift vocabulary (derived from constant.py) ──────────────────────────────

# Worked-shift codes, in dropdown order.
_WORK_CODES = (
    LocaleShift.DAY, LocaleShift.EVENING, LocaleShift.NIGHT,
    LocaleShift.DAY_EVENING, LocaleShift.NIGHT_EVENING,
)
_CONTROL_CODES = (LocaleShift.OFF, LocaleShift.VACATION)
assert set(_WORK_CODES) | set(_CONTROL_CODES) == set(LocaleShift), (
    "constant.LocaleShift gained a member: decide where it goes in the template "
    "dropdown, legend and conditional formatting")


def offward_tokens() -> list:
    """Off-ward cell tokens in display order (kind order, then ช บ ด)."""
    tokens = [f"{kind.value}{OFFWARD_SEP}{sym}"
              for kind in OffWard for sym in OFFWARD_SHIFTS]
    assert set(tokens) == set(OFFWARD_TOKENS)
    return tokens


def negate_tokens() -> list:
    """Negative-request tokens (no:ช no:บ no:ด) in display order."""
    tokens = [f"{NEGATE_KIND}{OFFWARD_SEP}{sym}" for sym in OFFWARD_SHIFTS]
    assert set(tokens) == set(NEGATE_TOKENS)
    return tokens


def cell_tokens() -> list:
    """Every token the day-cell dropdown offers. Anti-drift: tests compare the
    generated validation to LocaleShift + OFFWARD_TOKENS."""
    return ([m.value for m in _WORK_CODES]
            + [m.value for m in _CONTROL_CODES]
            + offward_tokens()
            + negate_tokens())


# ── Palette / style helpers ──────────────────────────────────────────────────

NUM_DAYS = 31
FIRST_DAY_COL = 4                                # D
LAST_DAY_COL = FIRST_DAY_COL + NUM_DAYS - 1      # AH
LAST_COL = col_letter(LAST_DAY_COL)
# Senior weekend-shifts column (constraint.md 17a): just after the last day so the
# day columns do not move. ScheduleInput.from_xlsx finds it by header text.
WK_COL = col_letter(LAST_DAY_COL + 1)             # AI

# Default rest days / weekday letters shown in the blank template.
DEFAULT_REST_DAYS = "6 7 13 14 20 21 27 28"
_REST = {6, 7, 13, 14, 20, 21, 27, 28}
_LETTERS = "WTFSSMT"

# Row map (Roster).
R_CARD_HDR = 4
R_CODES_HDR = 27
R_LEGEND = 28
R_HDR = 31
R_SUB = 32
R_FIRST = 33


# ── Card content ─────────────────────────────────────────────────────────────

_WARD_INFO = (
    ("Ward name / ชื่อวอร์ด", None),
    ("Hospital / โรงพยาบาล", None),
    ("Head nurse / หัวหน้าเวร", None),
    ("LINE contact / ไลน์", None),
    ("Month / เดือน", 7),
    ("Year / ปี", 2026),
    ("Plan / แพ็กเกจ", "<tier>"),
)

# Left-card row appended after _WARD_INFO. The SETTINGS card is full (rows 5-26
# abut the shift-codes header), so the one min-days-off row ("RN / PN/NA")
# lives here; from_xlsx reads either card by label.
_MIN_OFF_ROW = ("Min days off RN / PN/NA / วันหยุดขั้นต่ำต่อเดือน", "0 / 0")

# (label, value). "<holidays>" is blank in the blank templates (= no special
# holidays) and EXAMPLE_HOLIDAYS in the example.
_SETTINGS = (
    ("Days in month / จำนวนวันในเดือน", 31),
    ("Rest days / วันหยุด", DEFAULT_REST_DAYS),
    ("Allow Evening→Night / บ่ายต่อดึก", "No"),
    ("Allow Night→Day / ดึกต่อเช้า", "No"),
    ("Limit consecutive nights / จำกัดดึกติดกัน", "No"),
    ("Max consecutive nights / ดึกติดกันสูงสุด", 3),
    ("No consecutive Day+Evening double / ห้ามช/บติดกัน", "No"),
    ("Exact coverage / จำนวนเวรตรงตามกำหนด", "Yes"),
    ("Head-nurse special shift / เวรพิเศษหัวหน้า", "Yes"),
    ("Weekday need D / E / N", "5 / 3 / 3"),
    ("Weekend need D / E / N", "4 / 2 / 2"),
    ("PN/NA weekday need D / E / N", "3 / 2 / 2"),
    ("PN/NA weekend need D / E / N", "2 / 1 / 1"),
    ("Saturdays / วันเสาร์", "<saturdays>"),
    ("Saturday need D / E / N", "4 / 2 / 2"),
    ("PN/NA saturday need D / E / N", "2 / 1 / 1"),
    ("Sundays / วันอาทิตย์", "<sundays>"),
    ("Sunday need D / E / N", "4 / 2 / 2"),
    ("PN/NA sunday need D / E / N", "2 / 1 / 1"),
    ("Special holidays / วันหยุดพิเศษ", "<holidays>"),
    ("Holiday need D / E / N", "4 / 2 / 2"),
    ("PN/NA holiday need D / E / N", "2 / 1 / 1"),
)

EXAMPLE_HOLIDAYS = "29 30"
# The example's rest days are 6 7 13 14 20 21 27 28 (the blank template's
# pattern); the first of each pair is the Saturday. Blank in the blank templates
# (= those days keep the Weekend need).
EXAMPLE_SATURDAYS = "6 13 20 27"
EXAMPLE_SUNDAYS = "7 14 21 28"

# Ward (example) roster: (name, type, staff_class, {day: token}).
#
# The example is the only workbook that demonstrates the features, so it must
# exercise all of them: both staff groups (7 RN + 5 PN/NA — once ANY active
# nurse is classed, ScheduleInput.validate() requires EVERY one to be), a
# special-holiday list, a holiday band of its own, and off-ward cells.
#
# NurseA is the only nurse typed "senior" (seniority is the Type column, not the
# row position), so she carries no request the §16/§17 senior rules would force
# the solver to drop (Day or OFF on weekdays only) and no off-ward cell, which
# the §21 skip-guard would discard.
# NurseA opts in to weekend shifts (constraint.md 17a): Day on 2 weekends, one of
# Sat/Sun each; off on all the others.
_EXAMPLE_WEEKEND_SHIFTS = {"NurseA": 2}
_EXAMPLE_ROWS = (
    ("NurseA", "senior", "rn", {2: "off", 3: "ช", 30: "off"}),
    ("NurseB", None, "rn", {1: "ช", 7: "off", 9: "ช", 11: "off", 15: "off",
                            19: "ช", 22: "off", 31: "off"}),
    ("NurseC", None, "rn", {5: "vac", 8: "off", 10: "flt:ช", 24: "off", 26: "off"}),
    ("NurseD", None, "rn", {2: "off", 5: "off", 14: "ช/บ", 17: "off"}),
    ("NurseE", None, "rn", {9: "off", 24: "no:บ"}),
    # Day 23, not 22: a Night refer duty on 21 followed by a Day shift the next
    # morning is a Night->Day transition, which allow_night_to_day forbids by
    # default — it made this example workbook INFEASIBLE as shipped.
    ("NurseF", None, "rn", {7: "ช", 18: "off", 21: "ref:ด", 23: "ช", 27: "off",
                            28: "off"}),
    ("NurseG", "new", "rn", {1: "off", 3: "ช", 5: "ช", 6: "ช", 8: "ช",
                             9: "ช", 11: "off", 13: "ช", 25: "off"}),
    ("NurseH", None, "pn", {7: "off", 20: "off", 30: "off"}),
    ("NurseI", None, "pn", {}),
    ("NurseJ", None, "pn", {2: "off", 12: "off", 16: "ref:บ", 24: "off", 31: "off"}),
    ("NurseK", "new", "na", {1: "ด", 8: "ด", 12: "off", 13: "off", 23: "ช",
                             30: "off"}),
    ("NurseL", None, "na", {20: "off", 19: "flt:บ"}),
)

# The example overrides the blank templates' staffing needs: split across two
# groups, 5/3/3 would need 11 RNs of the 7 present and be infeasible on sight.
# Keyed by the English head of the SETTINGS label.
_EXAMPLE_SETTINGS = {
    "Weekday need": "2 / 1 / 1",
    "Weekend need": "2 / 1 / 1",
    "Saturday need": "2 / 2 / 1",
    "Sunday need": "1 / 1 / 1",
    "Holiday need": "1 / 1 / 1",
    "PN/NA weekday need": "1 / 1 / 1",
    "PN/NA weekend need": "1 / 1 / 1",
    "PN/NA saturday need": "1 / 1 / 1",
    "PN/NA sunday need": "1 / 1 / 1",
    "PN/NA holiday need": "1 / 1 / 1",
}


def _plan_label(tier: str) -> str:
    return tier.title()          # 'ward+' -> 'Ward+', 'ward pro' -> 'Ward Pro'


# ── Roster sheet ─────────────────────────────────────────────────────────────


def _build_roster(ws, spec: TemplateSpec):
    last_row = R_FIRST + spec.nurse_rows - 1
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    ws.column_dimensions["A"].width = 20.0
    ws.column_dimensions["B"].width = 9.0
    ws.column_dimensions["C"].width = 9.0
    for ci in range(FIRST_DAY_COL, LAST_DAY_COL + 1):
        ws.column_dimensions[col_letter(ci)].width = 4.6

    # Title bar.
    subtitle = ("Ward 4B  ·  Siriraj Hospital  ·  July 2026  ·  Ward plan (example)"
                if spec.example else
                f"{_plan_label(spec.tier)} plan  ·  fill in ward name, hospital and "
                "month in the cells above")
    _put(ws, "A1", "MONTHLY SHIFT ROSTER  ·  ตารางเวรพยาบาลประจำเดือน",
         font=_font(20, True, WHITE), fill=TEAL, align=_LEFT1)
    _put(ws, "A2", subtitle, font=_font(11, False, WHITE), fill=TEAL_DARK, align=_LEFT1)
    ws.merge_cells(f"A1:{LAST_COL}1")
    ws.merge_cells(f"A2:{LAST_COL}2")
    ws.row_dimensions[1].height = 34.0
    ws.row_dimensions[2].height = 20.0

    # Card headers.
    _put(ws, f"A{R_CARD_HDR}", "WARD INFO · ข้อมูลวอร์ด",
         font=_font(10, True, WHITE), fill=SLATE, align=_LEFT1)
    ws.merge_cells(f"A{R_CARD_HDR}:F{R_CARD_HDR}")
    _put(ws, f"H{R_CARD_HDR}", "SETTINGS · ตั้งค่า",
         font=_font(10, True, WHITE), fill=SLATE, align=_LEFT1)
    ws.merge_cells(f"H{R_CARD_HDR}:T{R_CARD_HDR}")

    example_info = {5: "Ward 4B", 6: "Siriraj Hospital", 7: "Head Nurse A", 8: "@line_id"}

    def card_row(r, lab_ref, lab_end, val_ref, val_end, label, value):
        _put(ws, lab_ref, label, font=_font(10, True, INK), fill=LABEL_FILL,
             align=_LEFT1, border=BOX)
        ws.merge_cells(f"{lab_ref}:{lab_end}{r}")
        _put(ws, val_ref, value, font=_font(10, True, BLUE), fill=WHITE,
             align=_LEFT1, border=BOX)
        ws.merge_cells(f"{val_ref}:{val_end}{r}")

    for i, (label, value) in enumerate(_WARD_INFO):
        r = R_CARD_HDR + 1 + i
        if value == "<tier>":
            value = spec.tier
        elif spec.example and r in example_info:
            value = example_info[r]
        card_row(r, f"A{r}", "B", f"C{r}", "F", label, value)

    r = R_CARD_HDR + 1 + len(_WARD_INFO)
    card_row(r, f"A{r}", "B", f"C{r}", "F", *_MIN_OFF_ROW)

    for i, (label, value) in enumerate(_SETTINGS):
        r = R_CARD_HDR + 1 + i
        if value == "<holidays>":
            value = EXAMPLE_HOLIDAYS if spec.example else None
        elif value == "<saturdays>":
            value = EXAMPLE_SATURDAYS if spec.example else None
        elif value == "<sundays>":
            value = EXAMPLE_SUNDAYS if spec.example else None
        elif spec.example:
            head = label.split(" D / E / N")[0].strip()
            value = _EXAMPLE_SETTINGS.get(head, value)
        card_row(r, f"H{r}", "O", f"P{r}", "T", label, value)

    # Legend.
    _put(ws, f"A{R_CODES_HDR}",
         "SHIFT CODES · รหัสเวร   (type a code in each day cell, or pick from the dropdown)",
         font=_font(10, True, WHITE), fill=SLATE, align=_LEFT1)
    ws.merge_cells(f"A{R_CODES_HDR}:{LAST_COL}{R_CODES_HDR}")

    r = R_LEGEND
    for k, member in enumerate(_WORK_CODES + _CONTROL_CODES):
        code = member.value
        fill, ink = CODE_STYLE[code]
        c0 = 1 + 4 * k                      # A, E, I, M, Q, U, Y
        _put(ws, f"{col_letter(c0)}{r}", code, font=_font(11, True, ink), fill=fill,
             align=_CENTER, border=BOX)
        lab = f"{col_letter(c0 + 1)}{r}"
        _put(ws, lab, LEGEND_TEXT[code], font=_font(9), align=_LEFT)
        ws.merge_cells(f"{lab}:{col_letter(c0 + 3)}{r}")

    tokens = offward_tokens()
    flt, ref_ = tokens[0], tokens[-1]       # flt:ช, ref:ด
    notes = (
        (r + 1, flt, FLT_FILL,
         "Float to another unit: write flt: + ช / บ / ด  ·  ลอยไปช่วยหน่วยอื่น",
         "Class column: RN / PN / NA (RN and PN/NA scheduled separately)"),
        (r + 2, ref_, REF_FILL,
         "Refer / transfer escort: write ref: + ช / บ / ด  ·  ส่งต่อผู้ป่วย",
         "คอลัมน์กลุ่ม: RN / PN / NA (RN กับ PN/NA จัดเวรแยกกัน)"),
    )
    for rr, token, fill, text, note in notes:
        _put(ws, f"A{rr}", token, font=_font(11, True, INK), fill=fill, align=_CENTER,
             border=BOX)
        _put(ws, f"B{rr}", text, font=_font(9), align=_LEFT)
        # The ref row also carries the no: legend (no spare row above the grid).
        ws.merge_cells(f"B{rr}:{'H' if token == ref_ else 'P'}{rr}")
        if token == ref_:
            _put(ws, f"I{rr}", negate_tokens()[1], font=_font(11, True, INK),
                 fill=NEG_FILL, align=_CENTER, border=BOX)
            _put(ws, f"J{rr}", "Not this shift: no: + ช / บ / ด  ·  ไม่เอาเวรนี้",
                 font=_font(9), align=_LEFT)
            ws.merge_cells(f"J{rr}:P{rr}")
        _put(ws, f"Q{rr}", note, font=_font(9), align=_LEFT)
        ws.merge_cells(f"Q{rr}:{LAST_COL}{rr}")
    for rr in (r, r + 1, r + 2):
        ws.row_dimensions[rr].height = 18.0

    # Grid header.
    for ref, text in ((f"A{R_HDR}", "Nurse · พยาบาล"), (f"B{R_HDR}", "Type · ระดับ"),
                      (f"C{R_HDR}", "Class · กลุ่ม")):
        _put(ws, ref, text, font=_font(10, True, WHITE), fill=TEAL, align=_CENTER,
             border=BOX)
        ws.merge_cells(f"{ref}:{ref[0]}{R_SUB}")
    _put(ws, f"{WK_COL}{R_HDR}", "Weekend shifts · เวรเสาร์/อาทิตย์ (senior)",
         font=_font(8, True, WHITE), fill=TEAL, align=_CENTER, border=BOX)
    ws.merge_cells(f"{WK_COL}{R_HDR}:{WK_COL}{R_SUB}")
    ws.column_dimensions[WK_COL].width = 14
    for day in range(1, NUM_DAYS + 1):
        col = col_letter(FIRST_DAY_COL + day - 1)
        rest = day in _REST
        _put(ws, f"{col}{R_HDR}", day,
             font=_font(10, True, WEEKEND_INK if rest else WHITE),
             fill=WEEKEND_FILL if rest else TEAL, align=_CENTER, border=BOX)
        _put(ws, f"{col}{R_SUB}", _LETTERS[(day - 1) % 7],
             font=_font(8, rest, WEEKEND_LETTER if rest else LETTER_INK),
             fill=WEEKEND_FILL if rest else LETTER_FILL, align=_CENTER, border=BOX)
    ws.row_dimensions[R_HDR].height = 20.0
    ws.row_dimensions[R_SUB].height = 14.0

    # Nurse rows. Rest-day columns carry a pale tint on every row; the Class
    # column takes the band colour of the LAST row for all rows (shipped look of
    # the existing templates, reproduced as-is for parity).
    ex = spec.example
    class_fill = WHITE if (spec.nurse_rows - 1) % 2 == 0 else BAND
    for i in range(spec.nurse_rows):
        rr = R_FIRST + i
        bg = WHITE if i % 2 == 0 else BAND
        _put(ws, f"A{rr}", None, font=_font(10, ex), fill=bg, align=_LEFT, border=BOX)
        _put(ws, f"B{rr}", None, font=_font(9, False, EX_MUTED if ex else INK),
             fill=bg, align=_CENTER, border=BOX)
        _put(ws, f"C{rr}", None, font=_font(9, False, EX_MUTED if ex else INK),
             fill=class_fill, align=_CENTER, border=BOX)
        _put(ws, f"{WK_COL}{rr}", None, font=_font(9, False, EX_MUTED if ex else INK),
             fill=bg, align=_CENTER, border=BOX)
        for day in range(1, NUM_DAYS + 1):
            _put(ws, f"{col_letter(FIRST_DAY_COL + day - 1)}{rr}", None,
                 font=_font(9, ex), fill=REST_BODY if day in _REST else bg,
                 align=_CENTER, border=BOX)
        ws.row_dimensions[rr].height = 18.0

    if spec.example:
        for i, (name, kind, klass, cells) in enumerate(
                _EXAMPLE_ROWS[:spec.nurse_rows]):
            rr = R_FIRST + i
            ws[f"A{rr}"].value = name
            if kind:
                fill, ink = TYPE_STYLE[kind]
                _put(ws, f"B{rr}", kind, font=_font(9, True, ink), fill=fill)
            # Class column: the dropdown offers RN / PN / NA (upper-case).
            ws[f"C{rr}"].value = klass.upper()
            if name in _EXAMPLE_WEEKEND_SHIFTS:
                ws[f"{WK_COL}{rr}"].value = _EXAMPLE_WEEKEND_SHIFTS[name]
            for day, token in cells.items():
                ws[f"{col_letter(FIRST_DAY_COL + day - 1)}{rr}"].value = token

    ws.freeze_panes = f"D{R_FIRST}"

    # Data validation.
    grid = f"D{R_FIRST}:{LAST_COL}{last_row}"
    dv_cells = DataValidation(
        type="list", formula1='"' + ",".join(cell_tokens()) + '"', allow_blank=True,
        showErrorMessage=True, errorTitle="Shift code / รหัสเวร",
        error="Use ช บ ด ช/บ ด/บ off vac, or flt:/ref:/no: + ช บ ด")
    dv_cells.add(grid)
    dv_type = DataValidation(type="list", formula1='"senior,new"', allow_blank=True)
    dv_type.add(f"B{R_FIRST}:B{last_row}")
    dv_class = DataValidation(type="list", formula1='"RN,PN,NA"', allow_blank=True,
                              showErrorMessage=True,
                              error="RN, PN or NA (blank = one group)")
    dv_class.add(f"C{R_FIRST}:C{last_row}")
    dv_wk = DataValidation(type="whole", operator="between", formula1="0", formula2="6",
                           allow_blank=True, showErrorMessage=True,
                           error="Whole number of weekends (0 = always off at weekends)")
    dv_wk.add(f"{WK_COL}{R_FIRST}:{WK_COL}{last_row}")
    for dv in (dv_cells, dv_type, dv_class, dv_wk):
        ws.add_data_validation(dv)

    # Conditional formatting: colour the cell from its token.
    tl = f"D{R_FIRST}"
    for code, (fill, _ink) in CODE_STYLE.items():
        ws.conditional_formatting.add(grid, FormulaRule(
            formula=[f'EXACT({tl},"{code}")'],
            fill=PatternFill("solid", fgColor=fill, bgColor=fill)))
    for kind, fill in ((f"{OffWard.FLOAT.value}{OFFWARD_SEP}", FLT_FILL),
                       (f"{OffWard.REFER.value}{OFFWARD_SEP}", REF_FILL),
                       (f"{NEGATE_KIND}{OFFWARD_SEP}", NEG_FILL)):
        ws.conditional_formatting.add(grid, FormulaRule(
            formula=[f'EXACT(LEFT({tl},{len(kind)}),"{kind}")'],
            fill=PatternFill("solid", fgColor=fill, bgColor=fill)))


# ── Guide sheet ──────────────────────────────────────────────────────────────

_GUIDE_STEPS = (
    ("1. Fill the blue cells",
     "In the WARD INFO and SETTINGS cards, edit the white/blue value cells only. "
     "Labels on the left stay as they are.", 30),
    ("2. Enter shifts",
     "In the grid, click any day cell for a nurse and pick a code from the dropdown "
     "(or type it). The cell colors itself automatically.", 30),
    ("3. Rest days",
     "Type 'off' on the days a nurse cannot work. Weekend / rest-day columns are "
     "tinted blue for reference.", 30),
    ("4. Nurse type",
     "Set 'senior' for head-nurse rules, 'new' for a junior nurse, or leave blank "
     "for a normal nurse.", 30),
    ("5. Add nurses",
     "Type into the empty rows at the bottom. Dropdowns and colors already apply "
     "there.", 30),
    ("6. Staff class",
     "Set RN, PN or NA in the Class column. RN and PN/NA are scheduled as two "
     "separate rosters, each with its own staffing need (SETTINGS card). Leave "
     "blank if the ward has only one group. · ใส่กลุ่ม RN / PN / NA — "
     "สองกลุ่มจัดเวรแยกกัน", 44),
    ("6b. Senior weekend shifts",
     "Optional, senior nurses only: in the 'Weekend shifts' column (right of day "
     "31) enter how many weekends she works a Day shift - on each of those she "
     "works Saturday OR Sunday, the solver picks. Blank / 0 = she is off every "
     "weekend (default). Cannot exceed the Saturday/Sunday weekends listed. · "
     "ใส่จำนวนสุดสัปดาห์ที่หัวหน้าเข้าเวรเช้า (เสาร์หรืออาทิตย์)", 44),
    ("7. Special holidays",
     "List public / special holiday dates in 'Special holidays' as day numbers "
     "(e.g. 29 30). Those days are staffed from 'Holiday need' (blank = same as "
     "Weekend need). Leave the cell blank if there are none. · "
     "ใส่วันที่หยุดพิเศษ เช่น 29 30", 44),
    ("8. Saturday / Sunday",
     "To staff Saturdays and Sundays differently, list the Saturday day numbers in "
     "'Saturdays' and the Sunday day numbers in 'Sundays' (e.g. 6 13 20 27), then "
     "set 'Saturday need' / 'Sunday need'. Days not listed there (blank = all of "
     "them) use 'Weekend need'. Holidays take precedence. · "
     "แยกเวรวันเสาร์ / วันอาทิตย์", 56),
)

_GUIDE_CODES_EXTRA = (
    ("flt:ช · flt:บ · flt:ด",
     "Float to another unit on that shift. Fixed; counts as a worked shift for the "
     "nurse but not toward this ward's staffing · ลอยไปช่วยหน่วยอื่น"),
    ("ref:ช · ref:บ · ref:ด",
     "Refer / patient-transfer escort on that shift. Same rules as float · "
     "ส่งต่อผู้ป่วย"),
    ("no:ช · no:บ · no:ด",
     "Please do NOT give me that shift on that day. Day, the other shift, or off "
     "are all fine. A request like any other: counts toward the request limit · "
     "ขอไม่เอาเวรนี้"),
)


def _build_guide(ws, spec: TemplateSpec):
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.column_dimensions["A"].width = 3.0
    ws.column_dimensions["B"].width = 26.0
    ws.column_dimensions["C"].width = 66.0

    _put(ws, "B1", "How to use this template · วิธีกรอกตาราง",
         font=_font(16, True, WHITE), fill=TEAL, align=_LEFT1)
    ws.merge_cells("B1:C1")
    ws.row_dimensions[1].height = 30.0

    top = Alignment(horizontal="left", vertical="top", wrap_text=True)
    r = 3
    for title, body, height in _GUIDE_STEPS:
        _put(ws, f"B{r}", title, font=_font(10, True), align=top)
        _put(ws, f"C{r}", body, font=_font(10), align=top)
        ws.row_dimensions[r].height = float(height)
        r += 1

    def band(row, text):
        _put(ws, f"B{row}", text, font=_font(11, True, WHITE), fill=SLATE)
        _put(ws, f"C{row}", None, fill=SLATE)

    r += 1                                     # blank spacer
    band(r, "Shift codes · รหัสเวร")
    r += 1
    for member in _WORK_CODES + _CONTROL_CODES:
        code = member.value
        fill, ink = CODE_STYLE[code]
        _put(ws, f"B{r}", code, font=_font(9, True, ink), fill=fill, align=_CENTER,
             border=BOX)
        _put(ws, f"C{r}", LEGEND_TEXT[code], font=_font(9), align=_LEFT)
        ws.row_dimensions[r].height = 18.0
        r += 1
    fill, ink = CODE_STYLE["vac"]               # off-ward rows reuse the vac colours
    for code, text in _GUIDE_CODES_EXTRA:
        _put(ws, f"B{r}", code, font=_font(9, True, ink), fill=fill, align=_CENTER,
             border=BOX)
        _put(ws, f"C{r}", text, font=_font(9), align=_LEFT)
        ws.row_dimensions[r].height = 28.0
        r += 1

    r += 1
    _put(ws, f"B{r}", "Tip · เคล็ดลับ", font=_font(9, True))
    _put(ws, f"C{r}", "Blank cell = the scheduler decides that shift. Only fill in "
                      "fixed requests and days off.", font=_font(9))
    r += 2

    band(r, "Plan limits · ขีดจำกัดแพ็กเกจ")
    r += 1
    limits = TIER_LIMITS[spec.tier]
    max_nurses = limits["max_nurses"]
    rows = [
        ("Plan / แพ็กเกจ", _plan_label(spec.tier)),
        ("Max nurses / พยาบาลสูงสุด",
         str(max_nurses) if max_nurses is not None
         else f"Unlimited ({spec.nurse_rows} rows pre-built)"),
        ("Requested shifts per nurse / ขอเวรได้ต่อคน", str(limits["max_req_shifts"])),
        ("Requested days off per nurse / ขอวันหยุดได้ต่อคน", str(limits["max_holidays"])),
    ]
    if spec.tier == "free":
        # TIER_LIMITS carries no cap on the holidays *list* (max_holidays is the
        # per-nurse day-off cap above), so say so rather than invent a number.
        rows.append(("Special holidays / วันหยุดพิเศษ",
                     "Optional · not limited by plan · ไม่จำกัดตามแพ็กเกจ"))
    for label, value in rows:
        _put(ws, f"B{r}", label, font=_font(9))
        _put(ws, f"C{r}", value, font=_font(9, True))
        r += 1


# ── Public API ───────────────────────────────────────────────────────────────


def build_workbook(spec: TemplateSpec) -> openpyxl.Workbook:
    if spec.tier not in TIER_LIMITS:
        raise ValueError(f"unknown tier {spec.tier!r}")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ROSTER_SHEET
    _build_roster(ws, spec)
    _build_guide(wb.create_sheet(GUIDE_SHEET), spec)
    return wb


def main(out_dir: str | None = None) -> None:
    out_dir = out_dir or os.path.join(_ROOT, "Templates")
    os.makedirs(out_dir, exist_ok=True)
    for spec in TEMPLATES:
        path = os.path.join(out_dir, spec.filename)
        build_workbook(spec).save(path)
        print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
