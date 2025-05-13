from enum import Enum

class Shift(Enum):
    DAY = 'day'
    EVENING = 'evening'
    NIGHT = 'night'
    OFF = 'off'


class LocaleShift(Enum):
    OFF = "off"
    DAY = "ช"
    EVENING = "บ"
    NIGHT = "ด"
    DAY_EVENING = "ช/บ"
    NIGHT_EVENING = "ด/บ"