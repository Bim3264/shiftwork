from enum import Enum

class Shift(Enum):
    DAY = 'day'
    EVENING = 'evening'
    NIGHT = 'night'
    OFF = 'off'


class LocaleShift(Enum):
    OFF = "off"
    VACATION = "vac"
    DAY = "ช"
    EVENING = "บ"
    NIGHT = "ด"
    DAY_EVENING = "ช/บ"
    NIGHT_EVENING = "ด/บ"


OFFWARD_SEP = ":"


def weekend_units(saturdays, sundays, first, last):
    """Weekend units for the senior weekend-shift option (constraint.md 17a).

    Day numbers may be 0-based (first=0) or 1-based (first=1); `last` is the last
    day inclusive. Each Saturday pairs with the Sunday right after it; a Saturday
    with no listed Sunday after it, or a Sunday with no listed Saturday before it
    (a month starting on Sunday), is a ONE-day unit. Takes the literal Sat/Sun
    lists -- never the weekends/holidays union.
    """
    sats = {d for d in saturdays if first <= d <= last}
    suns = {d for d in sundays if first <= d <= last}
    units = [[s] + ([s + 1] if s + 1 in suns else []) for s in sorted(sats)]
    paired = {d for u in units for d in u}
    units += [[s] for s in sorted(suns) if s not in paired]
    return sorted(units)


class OffWard(Enum):
    """Off-ward duty kinds. Both are a hard, head-nurse-authored assignment to one
    shift that does NOT count toward this ward's coverage but DOES count as a
    worked shift for the nurse (constraint.md §21). They differ only in label."""
    FLOAT = "flt"   # lent to another unit
    REFER = "ref"   # accompanying a patient transfer


# Off-ward cell suffix (locale symbol) -> solver shift, and its inverse.
OFFWARD_SHIFTS = {
    LocaleShift.DAY.value:     Shift.DAY,
    LocaleShift.EVENING.value: Shift.EVENING,
    LocaleShift.NIGHT.value:   Shift.NIGHT,
}
SHIFT_LOCALE = {v: k for k, v in OFFWARD_SHIFTS.items()}

OFFWARD_KINDS = {k.value for k in OffWard}

# Every valid off-ward cell token, e.g. {'flt:ช', ..., 'ref:ด'}.
OFFWARD_TOKENS = {
    f'{kind}{OFFWARD_SEP}{sym}' for kind in OFFWARD_KINDS for sym in OFFWARD_SHIFTS
}


# Negative request (constraint.md section 23): 'no:บ' = anything EXCEPT Evening that
# day. Same kind:shift shape as the off-ward tokens, but a soft-able request, not a
# duty. Only the three working shifts can be negated ('no:off' is not a token).
NEGATE_KIND = "no"
NEGATE_TOKENS = {f'{NEGATE_KIND}{OFFWARD_SEP}{sym}' for sym in OFFWARD_SHIFTS}


def parse_negation(token):
    """'no:บ' -> Shift.EVENING. None for anything else (including bare 'no' and
    'no:off')."""
    text = str(token).strip()
    kind, sep, symbol = text.partition(OFFWARD_SEP)
    if not sep or kind.strip().lower() != NEGATE_KIND:
        return None
    return OFFWARD_SHIFTS.get(symbol.strip())


def parse_offward(token):
    """'flt:ด' -> ('flt', Shift.NIGHT). Returns None for anything that is not a
    well-formed off-ward token (including a bare 'flt'), so callers can fall
    through to the ordinary shift/control vocabulary."""
    text = str(token).strip()
    if OFFWARD_SEP not in text:
        return None
    kind, _sep, symbol = text.partition(OFFWARD_SEP)
    kind = kind.strip().lower()
    if kind not in OFFWARD_KINDS:
        return None
    shift = OFFWARD_SHIFTS.get(symbol.strip())
    if shift is None:
        return None
    return kind, shift
