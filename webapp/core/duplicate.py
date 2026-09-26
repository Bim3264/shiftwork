"""Duplicate a schedule input into next month's ("Next month from this").

Pure transformation: build a new ScheduleInput from an existing one, with all
requests cleared but the roster (names, types, active flags, order) and all
settings/coverage preserved. When the source is dated (per
``months.parse_month_year``), the month is advanced (Dec -> Jan rolls the
year) and ``num_days``/``weekends`` are recomputed for the new month via
``calendar_util.month_dates``, with ``extra_holidays`` reset to []. When
undated, ward_meta and settings are copied unchanged so the user can set the
month and auto-fill themselves.

Does not touch the database or mutate its input — the route in
webapp/web/app.py handles persistence.
"""

from __future__ import annotations

import copy

from webapp.core.calendar_util import month_dates
from webapp.core.months import parse_month_year
from webapp.core.schedule_input import Nurse, ScheduleInput


def next_month_input(si: ScheduleInput) -> ScheduleInput:
    """Return a new ScheduleInput for "next month from this". Does not mutate si."""
    ward_meta = dict(si.ward_meta)
    settings = copy.deepcopy(si.settings)

    parsed = parse_month_year(si.ward_meta)
    if parsed is not None:
        year, month = parsed
        month += 1
        if month > 12:
            month = 1
            year += 1
        ward_meta["month"] = str(month)
        ward_meta["year"] = str(year)

        md = month_dates(year, month, [])
        settings["num_days"] = md["num_days"]
        settings["weekends"] = md["combined"]
        settings["extra_holidays"] = []

    nurses = [
        Nurse(name=n.name, type=n.type, shifts={}, active=n.active)
        for n in si.nurses
    ]

    return ScheduleInput(ward_meta=ward_meta, settings=settings, nurses=nurses)
