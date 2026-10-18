"""Calendar helpers: derive a month's schedule dates automatically.

Given a month + year, compute the number of days, the weekend day-numbers
(Sat/Sun), and the public holidays (Thailand, via the `holidays` library). Plus
any manually-added holiday day-numbers.

`holiday_days` (public holidays + manual extras) feeds the separate `holidays`
setting, which selects the special-holiday coverage band (constraint.md section
22); `weekends` is Sat/Sun only. `combined` (the full union) is legacy: it is
what auto-fill used to store into `weekends` before holidays had their own band,
and is kept so nothing that reads it breaks.

Day numbers are 1-based (matching the CSV/UI convention).
"""

from __future__ import annotations

import calendar
import datetime

import holidays as _holidays

HOLIDAY_COUNTRY = "TH"  # Thailand; change if the app ever serves other countries


def month_dates(year: int, month: int, extra_holidays=None) -> dict:
    """Return {num_days, weekends, holidays, extra, combined, holiday_days}.

    - num_days: days in the month.
    - weekends: 1-based day numbers that fall on Sat/Sun.
    - saturdays / sundays: the same days split by weekday; they select the
      Saturday / Sunday coverage bands (constraint.md section 22) while
      `weekends` stays the union.
    - holidays: [{day, name}] public holidays in that month.
    - extra: the manual holiday day numbers (clamped to the month).
    - combined: sorted union of weekends + holiday days + extra (legacy: what
      used to be stored as `weekends`; holidays now have their own list).
    - holiday_days: sorted union of public holiday days + extra (what to store
      as `holidays`, the special-holiday coverage band).
    """
    num_days = calendar.monthrange(year, month)[1]

    weekends = [
        d for d in range(1, num_days + 1)
        if calendar.weekday(year, month, d) >= 5  # 5=Sat, 6=Sun
    ]

    saturdays = [d for d in weekends if calendar.weekday(year, month, d) == 5]
    sundays = [d for d in weekends if calendar.weekday(year, month, d) == 6]

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
        "saturdays": saturdays,
        "sundays": sundays,
        "holidays": holidays_list,
        "extra": extra,
        "combined": combined,
        "holiday_days": sorted(set(holiday_days) | set(extra)),
    }
