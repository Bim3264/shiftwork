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
