"""Calendar helpers: derive a month's schedule dates automatically.

Given a month + year, compute the number of days, the weekend day-numbers
(Sat/Sun), and the public holidays (Thailand, via the `holidays` library). Plus
any manually-added holiday day-numbers. Holidays are treated like weekends by the
scheduler, so `combined` is the full set of "weekend/holiday" days to feed into
`weekends`.

Day numbers are 1-based (matching the CSV/UI convention).
"""

from __future__ import annotations

import calendar
import datetime

import holidays as _holidays

HOLIDAY_COUNTRY = "TH"  # Thailand; change if the app ever serves other countries


def month_dates(year: int, month: int, extra_holidays=None) -> dict:
    """Return {num_days, weekends, holidays, extra, combined} for a month.

    - num_days: days in the month.
    - weekends: 1-based day numbers that fall on Sat/Sun.
    - holidays: [{day, name}] public holidays in that month.
    - extra: the manual holiday day numbers (clamped to the month).
    - combined: sorted union of weekends + holiday days + extra (what to store
      as `weekends` for the solver, since holidays act like weekends).
    """
    num_days = calendar.monthrange(year, month)[1]

    weekends = [
        d for d in range(1, num_days + 1)
        if calendar.weekday(year, month, d) >= 5  # 5=Sat, 6=Sun
    ]

    try:
        cal = _holidays.country_holidays(HOLIDAY_COUNTRY, years=year)
    except Exception:
        cal = {}
    holidays_list = []
    for d in range(1, num_days + 1):
        name = cal.get(datetime.date(year, month, d)) if cal else None
        if name:
            holidays_list.append({"day": d, "name": name})
    holiday_days = [h["day"] for h in holidays_list]

    extra = sorted({int(d) for d in (extra_holidays or []) if 1 <= int(d) <= num_days})

    combined = sorted(set(weekends) | set(holiday_days) | set(extra))
    return {
        "num_days": num_days,
        "weekends": weekends,
        "holidays": holidays_list,
        "extra": extra,
        "combined": combined,
    }
