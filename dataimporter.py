import pandas as pd
import io
import random
import os
from constant import Shift, LocaleShift

# ── Tier definitions ──────────────────────────────────────────────────────────

TIER_LIMITS: dict = {
    'free': {
        'max_nurses':      8,
        'max_req_shifts':  2,
        'max_holidays':    2,
        'enable_meetings': False,
    },
    'ward': {
        'max_nurses':      15,
        'max_req_shifts':  5,
        'max_holidays':    5,
        'enable_meetings': True,
    },
    'ward+': {
        'max_nurses':      25,
        'max_req_shifts':  5,
        'max_holidays':    5,
        'enable_meetings': True,
    },
    'ward pro': {
        'max_nurses':      40,
        'max_req_shifts':  5,
        'max_holidays':    5,
        'enable_meetings': True,
    },
    'department': {
        'max_nurses':      None,
        'max_req_shifts':  5,
        'max_holidays':    5,
        'enable_meetings': True,
    },
}

# ── Phase-0 license registry ──────────────────────────────────────────────────
# Maps license_key -> tier name.
# Issue keys manually on payment confirmation (PromptPay/bank transfer).
# In Phase 1, replace this with an API/database call.

LICENSE_REGISTRY: dict = {
    'FREE-TRIAL': 'free',
    # Add issued keys below as customers subscribe:
    # 'SW-2026-WARD-001': 'ward',
}

_DEFAULT_WEEKENDS: list = [5, 6, 12, 13, 19, 20, 26, 27]  # 0-based


class DataImporter():
    """Parse a ShiftWork CSV file (new sectioned format or legacy single-grid format).

    New format -- three INI-style sections in one file:

        [ward]
        ward_name,Ward 4B
        hospital,Siriraj Hospital
        contact_name,Head Nurse A
        contact_line,@line_id
        month,7
        year,2026
        tier,ward
        license_key,SW-2026-WARD-001

        [settings]
        num_days,31
        weekends,6 7 13 14 20 21 27 28
        allow_evening_to_night,false
        allow_night_to_day,false
        head_nurse_special_shift,true

        [schedule]
        Name,Type,1,2,...,31
        NurseA,senior,,off,...

    Legacy format -- just the nurse grid with no section headers.
    Old files continue to work unchanged (no tier enforcement applied).
    """

    # ── Section parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_sections(filename):
        """Split the file into named sections delimited by [section_name] lines.

        Returns (sections_dict, has_sections).
        If no section headers exist (legacy CSV), has_sections=False and
        everything is placed under 'schedule'.
        """
        sections = {}
        current = None
        has_sections = False

        with open(filename, encoding='utf-8-sig') as f:
            for raw_line in f:
                line = raw_line.rstrip('\n').rstrip('\r')
                stripped = line.strip()
                if stripped.startswith('[') and stripped.endswith(']') and len(stripped) > 2:
                    current = stripped[1:-1].strip().lower()
                    sections.setdefault(current, [])
                    has_sections = True
                elif current is not None:
                    sections[current].append(line)
                else:
                    sections.setdefault('schedule', [])
                    sections['schedule'].append(line)

        return sections, has_sections

    @staticmethod
    def _parse_kv_section(lines):
        """Parse 'key,value' rows into a dict. Skips blank lines and # comments."""
        result = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',', 1)
            if len(parts) == 2:
                result[parts[0].strip().lower().replace(' ', '_')] = parts[1].strip()
        return result

    @staticmethod
    def _parse_weekends(value):
        """Parse space- or comma-separated 1-based day numbers into 0-based ints.

        Example: '6 7 13 14 20 21 27 28' -> [5, 6, 12, 13, 19, 20, 26, 27]
        """
        if not value.strip():
            return list(_DEFAULT_WEEKENDS)
        parts = value.replace(',', ' ').split()
        try:
            return [int(p) - 1 for p in parts if p.strip()]
        except ValueError:
            print('[Settings] Could not parse weekends value; using defaults.')
            return list(_DEFAULT_WEEKENDS)

    @staticmethod
    def _resolve_tier(ward_info):
        """Resolve effective tier from ward_info metadata.

        Priority:
        1. license_key lookup in LICENSE_REGISTRY (authoritative)
        2. Declared 'tier' field (advisory; used when no key or key unrecognised)
        3. Default: 'free'
        """
        key = ward_info.get('license_key', '').strip()
        if key:
            if key in LICENSE_REGISTRY:
                resolved = LICENSE_REGISTRY[key]
                declared = ward_info.get('tier', '').strip().lower()
                if declared and declared != resolved:
                    print(f'[Tier] License key resolves to "{resolved}"; '
                          f'declared tier "{declared}" ignored.')
                return resolved
            else:
                print(f'[Tier] License key "{key}" not recognised. '
                      f'Falling back to declared tier.')

        declared = ward_info.get('tier', '').strip().lower()
        if declared in TIER_LIMITS:
            return declared

        print('[Tier] No valid license key or tier found. Defaulting to "free".')
        return 'free'

    # ── Init ───────────────────────────────────────────────────────────────────

    def __init__(self, filename, maxDayOff, maxReqShift):
        self.filename    = filename
        self.maxDayOff   = maxDayOff
        self.maxReqShift = maxReqShift

        self.reqShifts       = []
        self.reqDayOff       = []
        self.reqVacations    = []
        self.reqMeetings     = []
        self.newNurseIndices = []

        # --- Parse sections ---
        sections, self.has_metadata = self._parse_sections(filename)

        if self.has_metadata:
            self.ward_info = self._parse_kv_section(sections.get('ward', []))
            raw_settings   = self._parse_kv_section(sections.get('settings', []))
            self.tier      = self._resolve_tier(self.ward_info)
        else:
            # Legacy CSV -- no metadata, no tier enforcement
            self.ward_info = {}
            raw_settings   = {}
            self.tier      = None

        # Apply tier limits (new-format files only)
        if self.tier is not None:
            limits = TIER_LIMITS[self.tier]
            self._max_nurses     = limits['max_nurses']
            self.maxDayOff       = min(maxDayOff,   limits['max_holidays'])
            self.maxReqShift     = min(maxReqShift, limits['max_req_shifts'])
            self.enable_meetings = limits['enable_meetings']
            print(f'[Tier] Effective tier: "{self.tier}" -- '
                  f'max nurses: {self._max_nurses or "unlimited"}, '
                  f'meetings: {self.enable_meetings}, '
                  f'max requests/nurse: {self.maxReqShift}')
        else:
            # Legacy format: preserve original behaviour, no caps
            self._max_nurses     = None
            self.enable_meetings = True

        # Build effective settings dict (file values with sensible defaults)
        # enable_meetings is tier-gated and cannot be overridden from the file.
        self.settings = {
            'num_days':                 int(raw_settings.get('num_days', 31)),
            'weekends':                 self._parse_weekends(raw_settings.get('weekends', '')),
            'allow_evening_to_night':   raw_settings.get('allow_evening_to_night', 'false').lower() == 'true',
            'allow_night_to_day':       raw_settings.get('allow_night_to_day',     'false').lower() == 'true',
            'head_nurse_special_shift': raw_settings.get('head_nurse_special_shift', 'true').lower() == 'true',
            'enable_meetings':          self.enable_meetings,
            'min_request_percent':      int(str(raw_settings.get('min_request_percent', 100)).strip() or 100),
            'relax_days_off':           raw_settings.get('relax_days_off', 'false').lower() == 'true',
            'enforce_vacation':         raw_settings.get('enforce_vacation', 'true').lower() == 'true',
            'coverage_weekday_day':     int(str(raw_settings.get('coverage_weekday_day', 5)).strip() or 5),
            'coverage_weekday_evening': int(str(raw_settings.get('coverage_weekday_evening', 3)).strip() or 3),
            'coverage_weekday_night':   int(str(raw_settings.get('coverage_weekday_night', 3)).strip() or 3),
            'coverage_weekend_day':     int(str(raw_settings.get('coverage_weekend_day', 4)).strip() or 4),
            'coverage_weekend_evening': int(str(raw_settings.get('coverage_weekend_evening', 2)).strip() or 2),
            'coverage_weekend_night':   int(str(raw_settings.get('coverage_weekend_night', 2)).strip() or 2),
        }

        # Build schedule DataFrame from [schedule] section (or whole file for legacy)
        schedule_lines = sections.get('schedule', [])
        schedule_text  = '\n'.join(schedule_lines)
        self.df = pd.read_csv(io.StringIO(schedule_text))
        self.df.columns = self.df.columns.astype(str)

        # Validate nurse count against tier cap
        if self._max_nurses is not None:
            actual = len(self.df)
            if actual > self._max_nurses:
                raise ValueError(
                    f'[Tier] This ward has {actual} nurses but tier "{self.tier}" '
                    f'supports at most {self._max_nurses}. '
                    f'Please upgrade your plan.'
                )

    # ── Data helpers ───────────────────────────────────────────────────────────

    def numberOfNurses(self):
        return len(self.df)

    def clean(self, val):
        if pd.isna(val):
            return ""
        return str(val).strip().replace('﻿', '')

    def sample(self, items, max_count):
        if len(items) > max_count:
            return random.sample(items, max_count)
        return items

    # ── Transform ──────────────────────────────────────────────────────────────

    def transform(self):
        """Transform the schedule grid into structured inputs for the solver.

        Populates reqShifts ({day: [Shift, ...]} per nurse), reqDayOff, reqMeetings,
        and newNurseIndices.
        Meeting cells ('mtg') are silently ignored on the Free tier.
        Excess requests beyond configured maximums are randomly sampled down.
        """
        day_cols = [col for col in self.df.columns if str(col).strip().isdigit()]

        _new_flags = {"new", "junior", "น้องใหม่"}
        if "Type" in self.df.columns:
            for idx, row in self.df.iterrows():
                if self.clean(row.get("Type", "")).lower() in _new_flags:
                    self.newNurseIndices.append(idx)

        # Shifts each request cell asks for. A double-shift cell (ช/บ, ด/บ) is ONE
        # request for TWO shifts: it is sampled against the quota as one cell and
        # the solver honours (or, in soft mode, drops) both shifts together.
        _cell_shifts = {
            LocaleShift.DAY.value:           [Shift.DAY],
            LocaleShift.EVENING.value:       [Shift.EVENING],
            LocaleShift.NIGHT.value:         [Shift.NIGHT],
            LocaleShift.DAY_EVENING.value:   [Shift.DAY, Shift.EVENING],
            LocaleShift.NIGHT_EVENING.value: [Shift.NIGHT, Shift.EVENING],
        }

        for index, row in self.df.iterrows():
            days_off, vacations, meeting_days = [], [], []
            shift_cells = []   # [(day_idx, [Shift, ...])], one entry per request cell

            for col in day_cols:
                val = self.clean(row[col])
                if not val:
                    continue
                day_idx   = int(col) - 1
                val_lower = val.lower()

                if val_lower == "off":
                    days_off.append(day_idx)
                elif val_lower == "vac":
                    vacations.append(day_idx)
                elif val_lower == "mtg":
                    if self.enable_meetings:
                        meeting_days.append(day_idx)
                    # Free tier: mtg treated as blank
                elif val in _cell_shifts:
                    shift_cells.append((day_idx, list(_cell_shifts[val])))

            # Excess shift requests are sampled down per CELL (1 cell = 1 request).
            sampled = self.sample(shift_cells, self.maxReqShift)
            self.reqShifts.append({day: shifts for day, shifts in sampled})

            self.reqDayOff.append(self.sample(days_off, self.maxDayOff))
            # Vacations are approved leave: never randomly down-sampled like off-requests.
            self.reqVacations.append(vacations)
            self.reqMeetings.append(meeting_days)


        return self


class SettingLoader():
    """Legacy settings for old-format CSV files or direct CLI invocation.

    For new-format CSV files (with [ward] and [settings] sections), these
    values are superseded by the per-file settings read by DataImporter.
    """

    def __init__(self):
        self.num_days        = 31
        self.weekends        = [5, 6, 12, 13, 19, 20, 26, 27]
        self.allow_shift_e_n = False
        self.allow_shift_n_d = False


class FileLoader():

    def __init__(self, folderPath):
        self.files = []
        for entry in os.scandir(folderPath):
            if entry.is_file():
                self.files.append(entry.path)
        self.files.sort()
        print(self.files)
