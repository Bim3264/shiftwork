"""Month helpers for the month-centric dashboard and "next month" duplication.

Shared contract (landed first, see docs/specs/2026-09-26-parallel-build.md):
``THAI_MONTHS``, ``EN_MONTHS`` and ``parse_month_year``. The dashboard feature
extends this module; duplication imports ``parse_month_year`` only.
"""

from __future__ import annotations

THAI_MONTHS: list[str] = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]
EN_MONTHS: list[str] = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def parse_month_year(ward_meta: dict | None) -> tuple[int, int] | None:
    """Return (year, month) from ward_meta, or None if missing/invalid.

    Values are stored as strings ("9", "09", "2026"). Month must be 1..12 and
    year > 0; anything else (blank, non-numeric, out of range) is undated.
    """
    meta = ward_meta or {}
    try:
        month = int(str(meta.get("month", "")).strip())
        year = int(str(meta.get("year", "")).strip())
    except (TypeError, ValueError):
        return None
    if not (1 <= month <= 12) or year <= 0:
        return None
    return year, month
