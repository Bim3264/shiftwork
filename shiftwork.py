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
            if is_weekend:
                self.days.append({
                    'shifts': [Shift.NIGHT, Shift.DAY, Shift.EVENING, Shift.OFF],
                    'min_shifts': {Shift.NIGHT: 2,
                                   Shift.DAY: 4,
                                   Shift.EVENING: 2,
                                   Shift.OFF: 0}
                })
            else:
                self.days.append({
                    'shifts': [Shift.NIGHT, Shift.DAY, Shift.EVENING, Shift.OFF],
                    'min_shifts': {Shift.NIGHT: 3,
                                   Shift.DAY: 5,
                                   Shift.EVENING: 3,
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
        self._rebuild_days()       # refresh with updated num_days + weekends

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
                self.nurses.append({
                    'requested_holidays': data.reqDayOff[n],   # 0-based days
                    'requested_shifts': data.reqShifts[n]      # {day: shift}
                })

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

        for n in range(self.num_nurses):
            if n < len(self.nurses):
                nurse = self.nurses[n]
                for d in nurse['requested_holidays']:
                    self.model.Add(self.assignments[n][d][Shift.OFF] == 1)
                for d, req_shift in nurse['requested_shifts'].items():
                    if n in senior_indices:
                        is_weekend = d in self.weekends
                        if is_weekend or req_shift in {Shift.EVENING, Shift.NIGHT}:
                            # Senior nurses must be DAY/OFF on weekdays and OFF on weekends.
                            # Drop any request that conflicts with this.
                            print(f'[Info] Nurse {n} {req_shift.value} request on day {d+1} '
                                  f'skipped (conflicts with senior nurse rule).')
                            continue
                    self.model.Add(self.assignments[n][d][req_shift] == 1)

        # Meeting days: nurse is assigned DAY but excluded from minimum coverage count
        for n, days in self.meeting_days.items():
            for d in days:
                self.model.Add(self.assignments[n][d][Shift.DAY] == 1)


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
        off_counts = [shift_counts[n][Shift.OFF] for n in range(self.num_nurses)]
        max_off = self.model.NewIntVar(0, self.num_days, 'max_off')
        min_off = self.model.NewIntVar(0, self.num_days, 'min_off')
        self.model.AddMaxEquality(max_off, off_counts)
        self.model.AddMinEquality(min_off, off_counts)
        holiday_imb = self.model.NewIntVar(0, self.num_days, 'holiday_imb')
        self.model.Add(holiday_imb == max_off - min_off)

        # --- Double-shift count ---
        total_dbl = self.model.NewIntVar(0, self.num_nurses * self.num_days, 'total_dbl')
        self.model.Add(total_dbl == sum(self.double_shift_vars))

        # Combined objective:
        #   Shift imbalance and holiday imbalance are top priority (weight 10)
        #   Double shifts minimised last
        self.model.Minimize(
            10 * overall_imbalance +
            10 * holiday_imb +
            total_dbl
        )


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
          - Nurse has a requested shift → that shift (honour the data)
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
            off_days  = set(nurse.get('requested_holidays', []))
            req_shifts = nurse.get('requested_shifts', {})

            for d in range(self.num_days):
                if n in senior_indices:
                    hint = Shift.OFF if d in self.weekends else Shift.DAY
                elif d in off_days:
                    hint = Shift.OFF
                elif d in req_shifts:
                    hint = req_shifts[d]
                else:
                    hint = _cycle[(n + d) % 4]

                for s in Shift:
                    self.model.AddHint(self.assignments[n][d][s], 1 if s == hint else 0)

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
            self._write_schedule()
        else:
            print("No solution found.")

        return self.status

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
