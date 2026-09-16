"""Tier resolution and a view model for showing tier capabilities in the UI.

The subscription tiers and their limits live in `dataimporter` (TIER_LIMITS,
LICENSE_REGISTRY) — the same source the solver's importer enforces. This module
resolves the effective tier for a ward and turns its limits into a small
structure the templates render (limits + which constraint options are available).

Resolution mirrors DataImporter's precedence: a recognised license key wins,
then a `tier` declared in the file, otherwise the free tier. We read the shared
data (not DataImporter's private method) so there are no log side effects here.
"""

from __future__ import annotations

from typing import Any

from dataimporter import LICENSE_REGISTRY, TIER_LIMITS


def resolve_tier(ward_meta: dict | None) -> tuple[str, str]:
    """Return (tier_name, source) for a ward's metadata.

    source is one of: "license key", "declared in file", "default".
    """
    meta = ward_meta or {}
    key = str(meta.get("license_key", "")).strip()
    if key and key in LICENSE_REGISTRY:
        return LICENSE_REGISTRY[key], "license key"
    declared = str(meta.get("tier", "")).strip().lower()
    if declared in TIER_LIMITS:
        return declared, "declared in file"
    return "free", "default"


def tier_view(schedule_input) -> dict[str, Any]:
    """Build the tier panel shown on the input page.

    Reports the resolved tier, its numeric limits, how the current roster
    compares to the nurse cap, and each constraint option flagged available or
    unavailable for this tier.
    """
    name, source = resolve_tier(schedule_input.ward_meta)
    limits = TIER_LIMITS.get(name, TIER_LIMITS["free"])
    # Only active nurses are solved, so the cap applies to them.
    nurse_count = len(schedule_input.active_nurses())
    max_nurses = limits["max_nurses"]  # None == unlimited
    meetings = limits["enable_meetings"]

    # Which constraint options this tier unlocks. Only meetings is tier-gated in
    # the current model; the rule toggles are available on every tier. Listing
    # them all (with a reason) makes the tier's scope explicit to the user.
    options = [
        {
            "label": "Meeting days (mtg cells)",
            "available": meetings,
            "detail": "Mark nurses 'mtg' on a day"
            if meetings
            else "Not included on this tier — upgrade to use meeting days",
        },
        {"label": "Evening → night transition toggle", "available": True,
         "detail": "All tiers"},
        {"label": "Night → day transition toggle", "available": True,
         "detail": "All tiers"},
        {"label": "Head-nurse special-shift rule", "available": True,
         "detail": "All tiers"},
        {"label": "Custom weekends & schedule length", "available": True,
         "detail": "All tiers"},
    ]

    return {
        "name": name,
        "source": source,
        "max_nurses": max_nurses,               # None => unlimited
        "nurse_count": nurse_count,
        "over_max": max_nurses is not None and nurse_count > max_nurses,
        "max_req_shifts": limits["max_req_shifts"],
        "max_holidays": limits["max_holidays"],
        "meetings_enabled": meetings,
        "options": options,
    }
