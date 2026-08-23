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
import tempfile
from dataclasses import dataclass, field
from typing import Any

from constant import LocaleShift
from dataimporter import DataImporter

# Tokens a schedule cell may contain. Blank means "no request". 'off'/'mtg' are
# control tokens; the rest are the Thai shift symbols the solver understands.
_SHIFT_SYMBOLS = {
    LocaleShift.DAY.value,            # ช
    LocaleShift.EVENING.value,        # บ
    LocaleShift.NIGHT.value,          # ด
    LocaleShift.DAY_EVENING.value,    # ช/บ
    LocaleShift.NIGHT_EVENING.value,  # ด/บ
}
_CONTROL_TOKENS = {"off", "mtg"}
VALID_CELL_TOKENS = _SHIFT_SYMBOLS | _CONTROL_TOKENS | {""}

# Settings the constraint selector exposes, with their defaults. weekends are
# stored 1-based (as written in the CSV, e.g. "6 7 13 14"); everything that
# needs 0-based (the Solver constructor) converts at the boundary.
_DEFAULT_SETTINGS: dict[str, Any] = {
    "num_days": 31,
    "weekends": [6, 7, 13, 14, 20, 21, 27, 28],
    "allow_evening_to_night": False,
    "allow_night_to_day": False,
    "head_nurse_special_shift": True,
}

_BOOL_SETTING_KEYS = (
    "allow_evening_to_night",
    "allow_night_to_day",
    "head_nurse_special_shift",
)


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() == "true"


def _parse_weekends_1based(value: str) -> list[int]:
    """Parse a '6 7 13 14' style string into a sorted list of 1-based ints."""
    if not str(value).strip():
        return list(_DEFAULT_SETTINGS["weekends"])
    parts = str(value).replace(",", " ").split()
    return sorted(int(p) for p in parts if p.strip())


@dataclass
class Nurse:
    """One row of the grid: a name, a type, and any requested cells.

    shifts maps a 1-based day number to the raw cell token for that day. Days
    with no request are simply absent from the dict.
    """

    name: str
    type: str = ""
    shifts: dict[int, str] = field(default_factory=dict)


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

    # ── Coercion / parsing helpers ───────────────────────────────────────────

    @staticmethod
    def _coerce_settings(raw: dict[str, Any]) -> dict[str, Any]:
        s = dict(_DEFAULT_SETTINGS)
        if "num_days" in raw and str(raw["num_days"]).strip():
            s["num_days"] = int(raw["num_days"])
        if "weekends" in raw:
            wk = raw["weekends"]
            s["weekends"] = wk if isinstance(wk, list) else _parse_weekends_1based(wk)
        for key in _BOOL_SETTING_KEYS:
            if key in raw:
                s[key] = _to_bool(raw[key], _DEFAULT_SETTINGS[key])
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
            nurses.append(Nurse(name=name, type=ntype, shifts=shifts))
        return nurses

    # ── Emission ─────────────────────────────────────────────────────────────

    def to_solver_csv(self) -> str:
        """Emit a sectioned CSV that DataImporter/Solver parse back to this same
        schedule. This is what the solver adapter feeds to the engine."""
        out = io.StringIO()

        out.write("[ward]\n")
        for key, value in self.ward_meta.items():
            out.write(f"{key},{value}\n")

        out.write("\n[settings]\n")
        out.write(f"num_days,{self.settings['num_days']}\n")
        weekends = " ".join(str(d) for d in self.settings["weekends"])
        out.write(f"weekends,{weekends}\n")
        for key in _BOOL_SETTING_KEYS:
            out.write(f"{key},{'true' if self.settings[key] else 'false'}\n")

        out.write("\n[schedule]\n")
        num_days = self.settings["num_days"]
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["Name", "Type"] + [str(d) for d in range(1, num_days + 1)])
        for nurse in self.nurses:
            cells = [nurse.shifts.get(d, "") for d in range(1, num_days + 1)]
            writer.writerow([nurse.name, nurse.type] + cells)

        return out.getvalue()

    # ── JSON (for DB storage) ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "ward_meta": self.ward_meta,
            "settings": self.settings,
            "nurses": [
                # JSON object keys must be strings; days are re-int'd on load.
                {"name": n.name, "type": n.type,
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
        if not self.nurses:
            raise ValueError("At least one nurse is required.")
        # The senior-nurse rule assigns a head (index 0) and deputy (index 1),
        # so the solver needs at least two nurses when it is enabled; fewer would
        # crash the solver. Surface it here as a clear message instead.
        if self.settings["head_nurse_special_shift"] and len(self.nurses) < 2:
            raise ValueError(
                "The head/deputy senior-nurse rule needs at least 2 nurses. "
                "Add another nurse, or turn off 'head nurse special shift'."
            )
        for nurse in self.nurses:
            for day, token in nurse.shifts.items():
                if not (1 <= day <= num_days):
                    raise ValueError(
                        f"Nurse '{nurse.name}' has a request on day {day}, "
                        f"outside 1..{num_days}."
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
    return token
