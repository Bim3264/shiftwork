from ortools.sat.python import cp_model
from constant import Shift, LocaleShift
from dataimporter import DataImporter, SettingLoader, FileLoader
import pandas as pd
import time
import re
import os

class Solver():

    def __init__(self, filename: str, num_days: int, weekends,
                 allow_shift_e_n: bool = False, allow_shift_n_d: bool = False):

        self.startTime = time.time()

        self.model = cp_model.CpModel()

        self.num_days = num_days
        self.num_nurses = None  # set after loading input data
        self.weekends = weekends  # 0-based day indices

        # Shift transition overrides (caller can override SettingLoader defaults)
        self._init_allow_e_n = allow_shift_e_n
        self._init_allow_n_d = allow_shift_n_d

        self.filename = filename

        self.days = []
        self.nurses = []
        self.ward_info: dict = {}

        self.shift_types = list({s for s in Shift})

        self.assignments = {}
        self.double_shift_vars = []
        self.new_nurse_indices: set = set()   # nurses flagged as "new" in the CSV
        self.meeting_days: dict = {}          # {nurse_index: [0-based day, ...]}

        # Soft-request handling (see handleHolidaysAndReq). Populated only when
        # MIN_REQUEST_PERCENT < 100; each entry reifies one relaxable request.
        self.request_soft: list = []          # [{n, day, shift, kind, sat}]
        self.request_report = None            # filled after a successful solve

        self.initShiftSettings()

        self.loadReqShifts(filename, 5, 5)

        self.initAssignments()

        print('Init successfully')
        # TODO: Add generating settings from setting file

        # Note: constructing a Solver only sets up configuration, loads input,
        # and creates the decision variables. It deliberately does NOT build the
        # constraints or solve — call build() then solve() (or the run()
        # convenience) to do that. Keeping __init__ side-effect-free makes the
        # object testable and inspectable without paying for a full solve.


    def _rebuild_days(self):
        """Rebuild self.days[] based on current self.num_days and self.weekends.

        Call this any time num_days or weekends changes (e.g. after loading
        per-file settings from the [settings] section).
        """
        self.days = []
        for day in range(self.num_days):
            is_weekend = day in self.weekends
            band = self.coverage['weekend'] if is_weekend else self.coverage['weekday']
            self.days.append({
                'shifts': [Shift.NIGHT, Shift.DAY, Shift.EVENING, Shift.OFF],
                'min_shifts': {Shift.NIGHT:   band[Shift.NIGHT],
                               Shift.DAY:     band[Shift.DAY],
                               Shift.EVENING: band[Shift.EVENING],
                               Shift.OFF: 0}
            })

    def initShiftSettings(self):

        # Default settings (may be overridden by per-file [settings] section)
        self.MAX_SHIFT_PER_DAY = 2
        self.MAX_REQ_SHIFT = 5
        self.MAX_REQ_HOLIDAYS = 5

        self.ALLOW_SHIFT_N_D = False
        self.ALLOW_SHIFT_E_N = False

        self.HEAD_NURSE_INDEX = 0
        self.DEPUTY_NURSE_INDEX = 1
        self.HEAD_NURSE_SPEACIAL_SHIFT = True
        self.enable_meetings = True

        # Partial request acceptance. 100 = every request is a hard constraint
        # (all-or-nothing, original behaviour). Below 100, requests become soft:
        # the solver keeps as many as possible and may drop the rest down to this
        # floor, otherwise the whole model is infeasible. RELAX_DAYS_OFF decides
        # whether requested days off are droppable too (shifts always are).
        self.MIN_REQUEST_PERCENT = 100
        self.RELAX_DAYS_OFF = False
        # When True, every requested vacation ('vac') is a hard constraint the
        # solver can never drop (approved leave). When False, vacations become
        # droppable in soft mode like ordinary off-requests. Unlike 'off', which
        # is only requested, an enforced vacation is guaranteed.
        self.ENFORCE_VACATION = True
        # {nurse_index: set(0-based vacation days)} — populated by loadReqShifts.
        self.vacation_days = {}

        # Per-shift coverage minimums (nurses required per shift), by day band.
        # Defaults preserve the original hard-coded values. loadReqShifts()
        # overrides these from the file's [settings] before _rebuild_days().
        self.coverage = {
            'weekday': {Shift.DAY: 5, Shift.EVENING: 3, Shift.NIGHT: 3},
            'weekend': {Shift.DAY: 4, Shift.EVENING: 2, Shift.NIGHT: 2},
        }

        # Apply caller overrides (from GUI or CLI; may be further overridden by file)
        self.ALLOW_SHIFT_E_N = self._init_allow_e_n
        self.ALLOW_SHIFT_N_D = self._init_allow_n_d

        # Build the days array with constructor-provided num_days/weekends.
        # loadReqShifts() will call _rebuild_days() again if the file overrides these.
        self._rebuild_days()


    def initAssignments(self):
        """Initialize the shift variable for each nurse."""
        for n in range(self.num_nurses):
            self.assignments[n] = {}
            for d in range(self.num_days):
                self.assignments[n][d] = {}
                for s in Shift:
                    self.assignments[n][d][s] = self.model.NewBoolVar(f'n{n}_d{d}_s{s.value}')


    def loadReqShifts(self, filename: str, maxDayOff: int, maxReqShift: int):
        """Load requested shift data from the input file.

        For new-format files (with [settings] section), the file's settings
        override constructor-provided values. This makes each ward's file
        self-contained — num_days, weekends, and rule flags come from the file
        itself rather than from the GUI or SettingLoader.

        Priority: file settings > constructor args > code defaults.
        """
        data = DataImporter(filename, maxDayOff, maxReqShift).transform()

        # Apply per-file settings (new-format files only; legacy files get defaults)
        s = data.settings
        self.num_days              = s['num_days']
        self.weekends              = set(s['weekends'])
        self.ALLOW_SHIFT_E_N       = s['allow_evening_to_night']
        self.ALLOW_SHIFT_N_D       = s['allow_night_to_day']
        self.HEAD_NURSE_SPEACIAL_SHIFT = s['head_nurse_special_shift']
        self.enable_meetings       = s['enable_meetings']
        self.MIN_REQUEST_PERCENT   = max(0, min(100, int(s.get('min_request_percent', 100))))
        self.RELAX_DAYS_OFF        = bool(s.get('relax_days_off', False))
        self.ENFORCE_VACATION      = bool(s.get('enforce_vacation', True))
        self.coverage = {
            'weekday': {Shift.DAY:     s.get('coverage_weekday_day', 5),
                        Shift.EVENING: s.get('coverage_weekday_evening', 3),
                        Shift.NIGHT:   s.get('coverage_weekday_night', 3)},
            'weekend': {Shift.DAY:     s.get('coverage_weekend_day', 4),
                        Shift.EVENING: s.get('coverage_weekend_evening', 2),
                        Shift.NIGHT:   s.get('coverage_weekend_night', 2)},
        }
        self._rebuild_days()       # refresh with updated num_days + weekends + coverage

        # Store ward identity for output naming and logging
        self.ward_info: dict = data.ward_info
        if self.ward_info:
            print(f'[Ward] {self.ward_info.get("ward_name", "?")} — '
                  f'{self.ward_info.get("hospital", "?")} — '
                  f'Tier: {data.tier}')

        # Set num_nurses from the actual input data
        self.num_nurses = data.numberOfNurses()
        print(f'[Data] Detected {self.num_nurses} nurses.')

        if len(data.reqShifts) == len(data.reqDayOff):
            for n in range(len(data.reqShifts)):
                vacs = data.reqVacations[n] if n < len(data.reqVacations) else []
                self.nurses.append({
                    'requested_holidays': data.reqDayOff[n],   # 0-based days
                    'requested_shifts': data.reqShifts[n],     # {day: [Shift, ...]}
                    'requested_vacations': vacs                # 0-based days (approved leave)
                })

        # Reverse map used by the fairness objective and the output renderer.
        self.vacation_days = {
            n: set(nurse['requested_vacations'])
            for n, nurse in enumerate(self.nurses)
            if nurse.get('requested_vacations')
        }

        self.new_nurse_indices = set(data.newNurseIndices)
        self.meeting_days = {n: days for n, days in enumerate(data.reqMeetings) if days}

        # B2: precompute reverse map day → set[nurse] once; used in _constraintMinimumNurse()
        self._meeting_nurses_per_day: dict = {}
        for _n, _days in self.meeting_days.items():
            for _d in _days:
                self._meeting_nurses_per_day.setdefault(_d, set()).add(_n)

        if self.new_nurse_indices:
            print(f'[Data] New nurses: {sorted(self.new_nurse_indices)}')
        if self.meeting_days:
            print(f'[Data] Meeting days detected for nurses: {sorted(self.meeting_days.keys())}')

        print('[Data] Requested shifts and holidays loaded.')


    def constraints(self):
        """Assemble every hard constraint.

        This method is intentionally just an ordered list of named rule groups
        rather than one big loop: each helper below owns exactly one rule, so a
        rule can be read, changed, or unit-tested in isolation without untangling
        it from the others. The helpers still share the single n×d iteration
        pattern; splitting them costs a few extra passes over a small grid but
        adds no constraints or variables to the model.
        """
        self.handleHolidaysAndReq()

        self._add_daily_shift_rules()
        self._add_shift_transition_rules()
        self._add_double_shift_tracking()

        self._constraintMinimumNurse()
        self.optionHeadNurse()

    def _add_daily_shift_rules(self):
        """Rules that constrain a single nurse on a single day, independent of
        any other day: exactly-something-assigned, OFF exclusivity, the per-day
        shift cap, the illegal Night+Day pairing, and the new-nurse Evening+Night
        ban.
        """
        for n in range(self.num_nurses):
            for d in range(self.num_days):
                day_v   = self.assignments[n][d][Shift.DAY]
                eve_v   = self.assignments[n][d][Shift.EVENING]
                night_v = self.assignments[n][d][Shift.NIGHT]
                off_v   = self.assignments[n][d][Shift.OFF]

                # At least one shift type must be assigned each day
                self.model.Add(day_v + eve_v + night_v + off_v >= 1)

                # OFF is mutually exclusive with all work shifts
                self.model.AddImplication(off_v, day_v.Not())
                self.model.AddImplication(off_v, eve_v.Not())
                self.model.AddImplication(off_v, night_v.Not())

                # At most MAX_SHIFT_PER_DAY work shifts per day
                self.model.Add(day_v + eve_v + night_v <= self.MAX_SHIFT_PER_DAY)

                # Illegal same-day combo: Night + Day
                self.model.Add(night_v + day_v <= 1)

                # New nurses cannot work Evening + Night as a double shift
                if n in self.new_nurse_indices:
                    self.model.Add(eve_v + night_v <= 1)

    def _add_shift_transition_rules(self):
        """Rules that link a nurse's shift on one day to the next: the Evening→
        Night and Night→Day transitions are illegal unless explicitly allowed by
        the corresponding flag.
        """
        allow_e_n = self.ALLOW_SHIFT_E_N
        allow_n_d = self.ALLOW_SHIFT_N_D

        for n in range(self.num_nurses):
            for d in range(self.num_days - 1):
                eve_v        = self.assignments[n][d][Shift.EVENING]
                night_v      = self.assignments[n][d][Shift.NIGHT]
                next_day_v   = self.assignments[n][d + 1][Shift.DAY]
                next_night_v = self.assignments[n][d + 1][Shift.NIGHT]

                if not allow_e_n:
                    self.model.Add(eve_v + next_night_v <= 1)   # E -> N
                if not allow_n_d:
                    self.model.Add(night_v + next_day_v <= 1)   # N -> D

    def _add_double_shift_tracking(self):
        """Create one indicator per nurse-day that is true when the nurse works
        two shifts that day. These feed the double-shift term in the fairness
        objective; the append order (nurse-major, day-minor) is what distribute()
        relies on, so it is kept identical to the original single-loop version.
        """
        for n in range(self.num_nurses):
            for d in range(self.num_days):
                day_v   = self.assignments[n][d][Shift.DAY]
                eve_v   = self.assignments[n][d][Shift.EVENING]
                night_v = self.assignments[n][d][Shift.NIGHT]

                double = self.model.NewBoolVar(f'dbl_{n}_{d}')
                self.model.Add(day_v + eve_v + night_v >= 2).OnlyEnforceIf(double)
                self.model.Add(day_v + eve_v + night_v <= 1).OnlyEnforceIf(double.Not())
                self.double_shift_vars.append(double)


    def handleHolidaysAndReq(self):
        # Senior nurses (head + deputy) have role constraints that take precedence
        # over shift requests in the CSV. Conflicting requests are silently dropped.
        senior_indices: set = set()
        if self.HEAD_NURSE_SPEACIAL_SHIFT:
            senior_indices = {self.HEAD_NURSE_INDEX, self.DEPUTY_NURSE_INDEX}

        soft = self.MIN_REQUEST_PERCENT < 100
        self.request_soft = []

        def _add_request(n, d, shifts, kind, relaxable):
            """Force assignments[n][d][s]==1 for every s in `shifts` (one request
            cell; a double shift like ช/บ is two shifts). In soft mode a relaxable
            request is reified with ONE 'satisfied' var so the whole cell is kept
            or dropped together (and tracked for the report); otherwise it stays a
            hard constraint."""
            shifts = list(shifts) if isinstance(shifts, (list, tuple, set)) else [shifts]
            if soft and relaxable:
                sat = self.model.NewBoolVar(f'req_{kind}_{n}_{d}')
                for s in shifts:
                    self.model.Add(self.assignments[n][d][s] == 1).OnlyEnforceIf(sat)
                self.request_soft.append(
                    {'n': n, 'day': d, 'shift': shifts[0], 'shifts': shifts,
                     'kind': kind, 'sat': sat}
                )
            else:
                for s in shifts:
                    self.model.Add(self.assignments[n][d][s] == 1)

        for n in range(self.num_nurses):
            if n < len(self.nurses):
                nurse = self.nurses[n]
                for d in nurse['requested_holidays']:
                    _add_request(n, d, Shift.OFF, 'holiday', self.RELAX_DAYS_OFF)
                for d in nurse.get('requested_vacations', []):
                    # Enforced vacations are never relaxable (hard OFF); otherwise
                    # they drop like off-requests in soft mode.
                    _add_request(n, d, Shift.OFF, 'vacation', not self.ENFORCE_VACATION)
                for d, req_shifts in nurse['requested_shifts'].items():
                    req_set = set(req_shifts)
                    label = '+'.join(s.value for s in req_shifts)
                    if n in senior_indices:
                        is_weekend = d in self.weekends
                        if is_weekend or req_set & {Shift.EVENING, Shift.NIGHT}:
                            # Senior nurses must be DAY/OFF on weekdays and OFF on weekends.
                            # Drop any request that conflicts with this.
                            print(f'[Info] Nurse {n} {label} request on day {d+1} '
                                  f'skipped (conflicts with senior nurse rule).')
                            continue
                    if n in self.new_nurse_indices and {Shift.EVENING, Shift.NIGHT} <= req_set:
                        # New nurses may not work Evening+Night the same day (§14).
                        print(f'[Info] Nurse {n} {label} request on day {d+1} '
                              f'skipped (new nurse cannot work Evening + Night).')
                        continue
                    _add_request(n, d, req_shifts, 'shift', True)

        # Meeting days: nurse is assigned DAY but excluded from minimum coverage
        # count. Meetings are commitments and are never dropped.
        for n, days in self.meeting_days.items():
            for d in days:
                self.model.Add(self.assignments[n][d][Shift.DAY] == 1)

        # Floor: at least MIN_REQUEST_PERCENT of the relaxable requests must be
        # satisfied, else the model is (correctly) infeasible.
        if soft and self.request_soft:
            import math
            floor = math.ceil(self.MIN_REQUEST_PERCENT / 100.0 * len(self.request_soft))
            self.model.Add(sum(e['sat'] for e in self.request_soft) >= floor)
            print(f'[Requests] Soft mode: {len(self.request_soft)} relaxable requests, '
                  f'floor {floor} ({self.MIN_REQUEST_PERCENT}%).')


    def _constraintMinimumNurse(self):
        """Ensure at least the minimum required nurses per shift per day.

        For Day shift: nurses attending a meeting that day are excluded from
        the coverage count (they are present but not providing clinical coverage).
        """
        for d in range(self.num_days):
            for shift in [Shift.DAY, Shift.EVENING, Shift.NIGHT]:
                required = self.days[d]['min_shifts'][shift]
                if shift == Shift.DAY:
                    # B2: use precomputed reverse map instead of rebuilding set each iteration
                    meeting_nurses_today = self._meeting_nurses_per_day.get(d, set())
                    nurses_assigned = [
                        self.assignments[n][d][shift]
                        for n in range(self.num_nurses)
                        if n not in meeting_nurses_today
                    ]
                else:
                    nurses_assigned = [self.assignments[n][d][shift] for n in range(self.num_nurses)]
                self.model.Add(sum(nurses_assigned) >= required)


    def optionHeadNurse(self):
        """Apply senior nurse rules to head nurse and deputy head nurse.

        On weekdays:  DAY or OFF only (no Evening or Night).
        On weekends:  OFF only.
        Controlled by HEAD_NURSE_INDEX (0) and DEPUTY_NURSE_INDEX (1).
        Disable entirely by setting HEAD_NURSE_SPEACIAL_SHIFT = False.
        """
        if not self.HEAD_NURSE_SPEACIAL_SHIFT:
            return

        senior_indices = [self.HEAD_NURSE_INDEX, self.DEPUTY_NURSE_INDEX]

        for n in senior_indices:
            for d in range(self.num_days):
                is_weekend = d in self.weekends
                if not is_weekend:
                    # Weekday: Day or OFF only
                    self.model.Add(
                        self.assignments[n][d][Shift.DAY] + self.assignments[n][d][Shift.OFF] == 1
                    )
                    self.model.Add(self.assignments[n][d][Shift.EVENING] == 0)
                    self.model.Add(self.assignments[n][d][Shift.NIGHT] == 0)
                else:
                    # Weekend: always OFF.
                    # B1: AddImplication constraints in constraints() already propagate
                    # OFF==1 → DAY/EVE/NIGHT==0, so only one constraint is needed here.
                    self.model.Add(self.assignments[n][d][Shift.OFF] == 1)


    def distribute(self):
        """Build fairness objective: minimize shift imbalance, holiday imbalance, and double shifts."""

        # Shift count vars: one per (nurse, shift_type)
        shift_counts = {}
        for n in range(self.num_nurses):
            shift_counts[n] = {}
            for s in Shift:
                sc = self.model.NewIntVar(0, self.num_days, f'sc_{n}_{s.value}')
                self.model.Add(sc == sum(self.assignments[n][d][s] for d in range(self.num_days)))
                shift_counts[n][s] = sc

        # --- Shift type imbalance (Day / Evening / Night) ---
        imbalance_vars = []
        for s in [Shift.DAY, Shift.EVENING, Shift.NIGHT]:
            counts = [shift_counts[n][s] for n in range(self.num_nurses)]
            max_s = self.model.NewIntVar(0, self.num_days, f'max_{s.value}')
            min_s = self.model.NewIntVar(0, self.num_days, f'min_{s.value}')
            self.model.AddMaxEquality(max_s, counts)
            self.model.AddMinEquality(min_s, counts)
            imb = self.model.NewIntVar(0, self.num_days, f'imb_{s.value}')
            self.model.Add(imb == max_s - min_s)
            imbalance_vars.append(imb)

        # A3: sum of per-shift imbalances instead of max — gives CP-SAT a purely linear
        # objective term (no auxiliary max variable), tightening LP relaxation bounds.
        # Semantics: minimises total cross-shift imbalance rather than worst-case only.
        overall_imbalance = cp_model.LinearExpr.Sum(imbalance_vars)

        # --- Holiday (OFF) imbalance ---
        # Balance *discretionary* days off only: OFF days a nurse takes as
        # approved vacation are excluded so leave isn't counted against their
        # fair share (and doesn't force extra days off onto everyone else).
        disc_off = []
        for n in range(self.num_nurses):
            vac_days = self.vacation_days.get(n)
            if vac_days:
                vac_off = sum(self.assignments[n][d][Shift.OFF] for d in vac_days)
                do = self.model.NewIntVar(0, self.num_days, f'disc_off_{n}')
                self.model.Add(do == shift_counts[n][Shift.OFF] - vac_off)
                disc_off.append(do)
            else:
                disc_off.append(shift_counts[n][Shift.OFF])
        max_off = self.model.NewIntVar(0, self.num_days, 'max_off')
        min_off = self.model.NewIntVar(0, self.num_days, 'min_off')
        self.model.AddMaxEquality(max_off, disc_off)
        self.model.AddMinEquality(min_off, disc_off)
        holiday_imb = self.model.NewIntVar(0, self.num_days, 'holiday_imb')
        self.model.Add(holiday_imb == max_off - min_off)

        # --- Double-shift count ---
        total_dbl = self.model.NewIntVar(0, self.num_nurses * self.num_days, 'total_dbl')
        self.model.Add(total_dbl == sum(self.double_shift_vars))

        # Combined objective:
        #   Shift imbalance and holiday imbalance are top priority (weight 10)
        #   Double shifts minimised last
        objective = 10 * overall_imbalance + 10 * holiday_imb + total_dbl

        # In soft-request mode, keep as many requests as possible: penalise each
        # dropped request far more than any fairness term, so acceptance is the
        # overriding priority (fairness only breaks ties among equal-acceptance
        # solutions).
        if self.request_soft:
            reject_weight = 100000
            satisfied = cp_model.LinearExpr.Sum([e['sat'] for e in self.request_soft])
            objective = objective + reject_weight * (len(self.request_soft) - satisfied)

        self.model.Minimize(objective)


    def _build_warm_start(self):
        """A2: Greedy hint to cut time-to-first-feasible solution.

        CP-SAT model.AddHint() plants a starting value for every BoolVar.
        The hint does not need to be feasible — CP-SAT repairs constraint
        violations internally — but a plausible assignment dramatically
        reduces the search depth needed to reach the first feasible point.

        Strategy (per nurse, per day):
          - Senior nurse on a weekend  → OFF
          - Senior nurse on a weekday  → DAY
          - Nurse has a requested OFF  → OFF  (honour the data)
          - Nurse has a requested shift → that shift / both shifts of a double
          - All others                 → cycle (DAY, EVENING, NIGHT, OFF)
                                         via (nurse_index + day_index) % 4
        """
        _cycle = [Shift.DAY, Shift.EVENING, Shift.NIGHT, Shift.OFF]
        senior_indices = (
            {self.HEAD_NURSE_INDEX, self.DEPUTY_NURSE_INDEX}
            if self.HEAD_NURSE_SPEACIAL_SHIFT else set()
        )

        for n in range(self.num_nurses):
            nurse = self.nurses[n] if n < len(self.nurses) else {}
            off_days  = set(nurse.get('requested_holidays', [])) | set(nurse.get('requested_vacations', []))
            req_shifts = nurse.get('requested_shifts', {})

            for d in range(self.num_days):
                if n in senior_indices:
                    hints = {Shift.OFF if d in self.weekends else Shift.DAY}
                elif d in off_days:
                    hints = {Shift.OFF}
                elif d in req_shifts:
                    hints = set(req_shifts[d])      # both shifts for a double request
                else:
                    hints = {_cycle[(n + d) % 4]}

                for s in Shift:
                    self.model.AddHint(self.assignments[n][d][s], 1 if s in hints else 0)

    def build(self):
        """Assemble the CP model: hard constraints, fairness objective, and the
        greedy warm-start hint.

        Split out from solve() so the fully-built model can be inspected or
        unit-tested without running a (slow) search. Returns self so callers can
        chain, e.g. Solver(...).build().solve().
        """
        self.constraints()
        self.distribute()
        # A2: seed the model with a greedy hint before solving
        self._build_warm_start()
        return self

    def solve(self, max_time_seconds: float = 120):
        """Configure the solver, search for a schedule, and write it out if one
        is found. Assumes build() has already assembled the model.

        max_time_seconds caps the search; it defaults to 120 (unchanged from the
        original behaviour) and is exposed mainly so tests can bound the run —
        the fairness objective otherwise keeps optimising until the cap.

        Returns the CP-SAT status so callers can distinguish solved from
        unsolved without inspecting stdout.
        """
        self.solver = cp_model.CpSolver()

        # A1: use all physical cores instead of a hardcoded 8
        self.solver.parameters.num_search_workers = os.cpu_count() or 8
        # Strong LP relaxation helps CP-SAT find bounds faster
        self.solver.parameters.linearization_level = 2
        # Give the solver enough time but don't hang forever
        self.solver.parameters.max_time_in_seconds = max_time_seconds
        # Uncomment to see per-iteration solver progress in the log:
        # self.solver.parameters.log_search_progress = True

        self.status = self.solver.Solve(self.model)

        if self.status == cp_model.OPTIMAL or self.status == cp_model.FEASIBLE:
            print(f"Solution found ({(time.time() - self.startTime) * 10**3:.0f} ms):")
            self._build_request_report()
            self._write_schedule()
        else:
            self.request_report = None
            print("No solution found.")

        return self.status

    def _build_request_report(self):
        """After a successful solve, record which soft requests were accepted vs
        dropped (only meaningful in soft mode; None otherwise)."""
        if not self.request_soft:
            self.request_report = None
            return
        accepted, dropped = [], []
        for e in self.request_soft:
            shifts = e.get('shifts') or [e['shift']]
            rec = {'nurse_index': e['n'], 'day': e['day'] + 1,
                   'shift': '+'.join(s.value for s in shifts), 'kind': e['kind']}
            if self.solver.Value(e['sat']) == 1:
                accepted.append(rec)
            else:
                dropped.append(rec)
        total = len(self.request_soft)
        self.request_report = {
            'total': total,
            'accepted': len(accepted),
            'dropped': dropped,
            'percent': round(100.0 * len(accepted) / total, 1),
        }
        print(f"[Requests] Accepted {len(accepted)}/{total} "
              f"({self.request_report['percent']}%); dropped {len(dropped)}.")

    def run(self, max_time_seconds: float = 120):
        """Convenience: build the model then solve it. Returns the status."""
        self.build()
        return self.solve(max_time_seconds=max_time_seconds)

    def _write_schedule(self):
        """Render the solved assignments into a transposed grid and write the
        output CSV.

        Pure output step: it reads the solution off self.solver and touches no
        model/constraint logic, which is why it lives apart from solve(). Assumes
        self.solver holds a feasible solution. Returns the output label used for
        the filename.
        """
        output = {
            "Date": [i + 1 for i in range(self.num_days)]
        }
        for n in range(self.num_nurses):
            output[str(n)] = [""] * self.num_days

        for d in range(self.num_days):
            for n in range(self.num_nurses):
                is_day     = self.solver.Value(self.assignments[n][d][Shift.DAY]) == 1
                is_evening = self.solver.Value(self.assignments[n][d][Shift.EVENING]) == 1
                is_night   = self.solver.Value(self.assignments[n][d][Shift.NIGHT]) == 1
                is_off     = self.solver.Value(self.assignments[n][d][Shift.OFF]) == 1

                if is_day and is_evening:
                    symbol = LocaleShift.DAY_EVENING.value
                elif is_night and is_evening:
                    symbol = LocaleShift.NIGHT_EVENING.value
                elif is_day:
                    symbol = LocaleShift.DAY.value
                elif is_evening:
                    symbol = LocaleShift.EVENING.value
                elif is_night:
                    symbol = LocaleShift.NIGHT.value
                elif is_off:
                    # A granted OFF on a requested-vacation day is shown as 'vac'.
                    if d in self.vacation_days.get(n, ()):
                        symbol = LocaleShift.VACATION.value
                    else:
                        symbol = LocaleShift.OFF.value
                else:
                    symbol = "ERROR"

                output[str(n)][d] = symbol

        data = pd.DataFrame(output)
        data = data.transpose()
        data.columns = data.iloc[0]
        data = data[1:]

        print(f"Generated DataFrame in {(time.time() - self.startTime) * 10**3:.0f} ms")

        # Build output filename from ward identity (new format) or input filename (legacy)
        ward_name = self.ward_info.get('ward_name', '').strip()
        month     = self.ward_info.get('month', '').strip()
        year      = self.ward_info.get('year', '').strip()
        if ward_name:
            label = ward_name.replace(' ', '_').replace('/', '-')
            if month and year:
                label += f'_{year}-{int(month):02d}'
            elif year:
                label += f'_{year}'
        else:
            match = re.search(r'input[/\\](.*?)(?=\.csv)', self.filename)
            label = match.group(1) if match else 'output'
        data.to_csv(f'output/{label} Final.csv', encoding='utf-8-sig')

        print(f"Total execution time: {(time.time() - self.startTime) * 10**3:.0f} ms")
        return label


if __name__ == '__main__':

    settings = SettingLoader()
    num_days = settings.num_days
    weekends = settings.weekends

    fileLoader = FileLoader("input")

    for file in fileLoader.files:
        solver = Solver(file, num_days, weekends,
                        allow_shift_e_n=settings.allow_shift_e_n,
                        allow_shift_n_d=settings.allow_shift_n_d)
        solver.run()

    # TODO: Separate successful and failed output files into different folders
