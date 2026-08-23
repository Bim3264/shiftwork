from ortools.sat.python import cp_model
from constant import Shift, LocaleShift
from dataimporter import DataImporter, SettingLoader, FileLoader
import pandas as pd
import time
import re

class Solver():

    def __init__(self, filename: str, num_days: int, weekends):

        self.startTime = time.time()

        # Initialize the model
        self.model = cp_model.CpModel()

         # Sample Input Data (Customize this section)
        self.num_days = num_days
        self.num_nurses = None  # set after loading input data
        self.weekends = weekends # 0-based day indices
        # self.shift_transitions = [('Evening', 'Night'), ('Night', 'Day')]

        self.filename = filename

        self.days = []
        self.nurses = []

        self.shift_types = list({s for s in Shift})

        self.assignments = {}
        # Create variables to track double shifts for the objective function
        self.double_shift_vars = []

        self.initShiftSettings()

        self.loadReqShifts(filename, 5, 5)

        self.initAssignments()

        print('Init sucessfully')
        # TODO: Add generating settings from setting file

        self.constraints()
        self.distribute()
        self.main()



    def initShiftSettings(self):

        # Define days with shifts and coverage requirements
        for day in range(self.num_days):
            is_weekend = day in self.weekends
            if is_weekend:
                self.days.append({
                    'shifts': [Shift.NIGHT, Shift.DAY, Shift.EVENING, Shift.OFF],
                    'min_shifts': {Shift.NIGHT:2, 
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

        # INIT Settings with their default values
        self.MAX_SHIFT_PER_DAY = 2
        self.MAX_REQ_SHIFT = 5
        self.MAX_REQ_HOLIDAYS = 5

        self.ALLOW_SHIFT_N_D = False
        self.ALLOW_SHIFT_E_N = False

        self.HEAD_NURSE_INDEX = 0
        self.HEAD_NURSE_SPEACIAL_SHIFT = True



    def initAssignments(self):
        """Initialized the shift variable for each nurses"""
        
        # Create assignment variables for shift s of nurse n in day d
        for n in range(self.num_nurses):
            self.assignments[n] = {}                    
            for d in range(self.num_days):
                self.assignments[n][d] = {}             
                for s in Shift:
                    self.assignments[n][d][s] = self.model.NewBoolVar(f'n{n}_d{d}_s{s.value}')


    def loadReqShifts(self, filename: str, maxDayOff: int, maxReqShift: int):
        """Load the requested shift data from the file.
        
        Keyword arguments:
            filename: Filename
            maxDayOff: Maximum day off request per nurse
            maxReqShift: Maximum request shift per nurse
        """
        
        
        data = DataImporter(filename, maxDayOff, maxReqShift).transform()

        # Set num_nurses from the actual input data
        self.num_nurses = data.numberOfNurses()
        print(f'[Data] Detected {self.num_nurses} nurses.')

        # Check data length
        if len(data.reqShifts) == len(data.reqDayOff):
            for n in range(len(data.reqShifts)):
                self.nurses.append({
                    'requested_holidays': data.reqDayOff[n],    # 0-based days
                    'requested_shifts': data.reqShifts[n]       # {day: shift}
                })
        
        print('[Data] Requested shifts and holidays loaded.')


    def constraints(self):
         # Handle holidays and shift requests
        self.handleHolidaysAndReq() #n

        for n in range(self.num_nurses):
            for d in range(self.num_days):

                # All nurses must work exactly one shift per non-holiday day
                self._assignShifts(n, d)
                # Apply illegal shift transition constraints
                self._constraintIllegalShift(n, d)
                # Ensure a shift is assigned
                self._constraintMustAssignShift(n, d)
                # Limit shifts per day
                self._constraintShiftLimit(n, d)
                # Track double shifts for the objective function
                self._trackDoubleShift(n, d)

        self._constraintMinimumNurse() # n shift d -> can change to n d


        self.trackShiftCount() # n s

        self.optionHeadNurse()
        

    def handleHolidaysAndReq(self):
        for n in range(self.num_nurses):
            if n < len(self.nurses):
                nurse = self.nurses[n]
                # Handle requested holidays
                for d in nurse['requested_holidays']:
                    # for s_idx in range(len(self.days[d]['shifts'])):
                    #     self.model.Add(self.assignments[n][d][s_idx] == 0)
                    self.model.Add(self.assignments[n][d][Shift.OFF] == 1)
            
        #     # Handle shift requests
                for d, req_shift in nurse['requested_shifts'].items():
                    self.model.Add(self.assignments[n][d][req_shift] == 1)


    # Helper functions with underscore prefix to indicate they're "private"
    # and only meant to be called from the constraints method

    def _assignShifts(self, n, d):
        """Enforce shift assignment rules for a specific nurse and day."""
        # Skip requested holidays (OFF already enforced elsewhere)
        if n < len(self.nurses) and d not in self.nurses[n]['requested_holidays']:
            day_var = self.assignments[n][d][Shift.DAY]
            evening_var = self.assignments[n][d][Shift.EVENING]
            night_var = self.assignments[n][d][Shift.NIGHT]
            off_var = self.assignments[n][d][Shift.OFF]

            # Sum work shifts
            work_sum = self.model.NewIntVar(0, 3, f'work_sum_n{n}_d{d}')
            self.model.Add(work_sum == day_var + evening_var + night_var)

            # Create a boolean flag if nurse works any shift
            work_assigned = self.model.NewBoolVar(f'work_assigned_n{n}_d{d}')
            self.model.Add(work_sum >= 1).OnlyEnforceIf(work_assigned)
            self.model.Add(work_sum == 0).OnlyEnforceIf(work_assigned.Not())

            # Enforce exclusivity between OFF and working shifts
            self.model.Add(off_var == 0).OnlyEnforceIf(work_assigned)
            self.model.Add(day_var + evening_var + night_var == 0).OnlyEnforceIf(off_var)

    def _constraintIllegalShift(self, n, d):
        """Apply illegal shift transition constraints for a specific nurse and day."""
        # Disallow N + D on the same day (never a legal same-day combo)
        self.model.Add((self.assignments[n][d][Shift.DAY] +
                    self.assignments[n][d][Shift.NIGHT]) <= 1)

        if d < self.num_days - 1:
            # Disallow E -> N across consecutive days (always illegal)
            if not self.ALLOW_SHIFT_E_N:
                self.model.Add((self.assignments[n][d][Shift.EVENING] +
                                self.assignments[n][d + 1][Shift.NIGHT]) <= 1)

            # Disallow N -> D across consecutive days (always illegal)
            if not self.ALLOW_SHIFT_N_D:
                self.model.Add((self.assignments[n][d][Shift.NIGHT] +
                                self.assignments[n][d + 1][Shift.DAY]) <= 1)

        # If off -> no work shift
        self.model.Add((self.assignments[n][d][Shift.DAY] + self.assignments[n][d][Shift.OFF]) <= 1)
        self.model.Add((self.assignments[n][d][Shift.EVENING] + self.assignments[n][d][Shift.OFF]) <= 1)
        self.model.Add((self.assignments[n][d][Shift.NIGHT] + self.assignments[n][d][Shift.OFF]) <= 1)

    def _constraintMustAssignShift(self, n, d):
        """Ensure at least one shift type is assigned for a specific nurse and day."""
        self.model.Add((self.assignments[n][d][Shift.DAY] + 
                    self.assignments[n][d][Shift.EVENING] + 
                    self.assignments[n][d][Shift.NIGHT] +
                    self.assignments[n][d][Shift.OFF]) >= 1)

    def _constraintMinimumNurse(self):
        """Ensure sufficient nurses are assigned to each required shift.
        This is more efficiently implemented by looping over days and shifts
        rather than nesting in the n-d loop, as the constraint applies 
        collectively to all nurses."""
        
        # Process minimum nurse requirements per shift per day
        for d in range(self.num_days):
            for shift in Shift:
                # Get required number of nurses for this shift and day
                required = self.days[d]['min_shifts'][shift]
                
                # Sum all nurse assignments for this shift and day
                nurses_assigned = [self.assignments[n][d][shift] for n in range(self.num_nurses)]
                
                # Apply appropriate constraint based on shift type
                if shift is not Shift.OFF:
                    self.model.Add(sum(nurses_assigned) >= required)
                # OFF has no minimum requirement

    def _constraintShiftLimit(self, n, d):
        """Limit the number of shifts per day for a specific nurse."""
        # Limit to MAX_SHIFT_PER_DAY (default 2) shifts per day
        self.model.Add((self.assignments[n][d][Shift.DAY] + 
                        self.assignments[n][d][Shift.EVENING] + 
                        self.assignments[n][d][Shift.NIGHT]) <= self.MAX_SHIFT_PER_DAY)

    def _trackDoubleShift(self, n, d):
        """Create variables to track double shifts for the objective function."""
        # Create a variable for this nurse-day double shift
        double_shift = self.model.NewBoolVar(f'double_shift_n{n}_d{d}')
        day_var = self.assignments[n][d][Shift.DAY]
        evening_var = self.assignments[n][d][Shift.EVENING]
        night_var = self.assignments[n][d][Shift.NIGHT]

        # Set double_shift to 1 if nurse has more than 1 shift this day
        work_sum = self.model.NewIntVar(0, 3, f'work_sum2_n{n}_d{d}')
        self.model.Add(work_sum == day_var + evening_var + night_var)
        self.model.Add(work_sum >= 2).OnlyEnforceIf(double_shift)
        self.model.Add(work_sum < 2).OnlyEnforceIf(double_shift.Not())
        
        # Add to our list for the objective function
        self.double_shift_vars.append(double_shift)


    def trackShiftCount(self):
        self.shift_counts = {}
        for n in range(self.num_nurses):
            self.shift_counts[n] = {}
            for s in self.shift_types:
                self.shift_counts[n][s] = self.model.NewIntVar(0, self.num_days, f'count_{n}_{s}')


    def optionHeadNurse(self):
        for d in range(self.num_days):
            # Head Nurse Shift
            is_weekend = d in self.weekends
            if not is_weekend:
                self.model.Add((self.assignments[0][d][Shift.DAY] + self.assignments[0][d][Shift.OFF]) == 1)
                self.model.Add((self.assignments[0][d][Shift.EVENING] +
                               self.assignments[0][d][Shift.NIGHT]) == 0)
            else:
                self.model.Add(self.assignments[0][d][Shift.OFF] == 1)
                self.model.Add((self.assignments[0][d][Shift.DAY] + 
                                self.assignments[0][d][Shift.EVENING] + 
                                self.assignments[0][d][Shift.NIGHT]) == 0)

    def distribute(self):
        # Link assignment variables to shift count variables
        for n in range(self.num_nurses):
            for s in self.shift_types:
                count = []
                for d in range(self.num_days):
                    if s in self.days[d]['shifts']:
                        count.append(self.assignments[n][d][s])
                self.model.Add(self.shift_counts[n][s] == sum(count))

        # --- Shift type imbalance (Day / Evening / Night) ---
        imbalance_vars = []
        for s in [Shift.DAY, Shift.EVENING, Shift.NIGHT]:
            counts = [self.shift_counts[n][s] for n in range(self.num_nurses)]
            max_s = self.model.NewIntVar(0, self.num_days, f'max_{s}')
            min_s = self.model.NewIntVar(0, self.num_days, f'min_{s}')
            self.model.AddMaxEquality(max_s, counts)
            self.model.AddMinEquality(min_s, counts)
            imbalance = self.model.NewIntVar(0, self.num_days, f'imbalance_{s}')
            self.model.Add(imbalance == max_s - min_s)
            imbalance_vars.append(imbalance)

        overall_imbalance = self.model.NewIntVar(0, self.num_days, 'overall_imbalance')
        self.model.AddMaxEquality(overall_imbalance, imbalance_vars)

        # --- Holiday (OFF) imbalance ---
        off_counts = [self.shift_counts[n][Shift.OFF] for n in range(self.num_nurses)]
        max_off = self.model.NewIntVar(0, self.num_days, 'max_off')
        min_off = self.model.NewIntVar(0, self.num_days, 'min_off')
        self.model.AddMaxEquality(max_off, off_counts)
        self.model.AddMinEquality(min_off, off_counts)
        holiday_imbalance = self.model.NewIntVar(0, self.num_days, 'holiday_imbalance')
        self.model.Add(holiday_imbalance == max_off - min_off)

        # --- Double-shift count ---
        total_double_shifts = self.model.NewIntVar(0, self.num_nurses * self.num_days, 'total_double_shifts')
        self.model.Add(total_double_shifts == sum(self.double_shift_vars))

        # Combined objective:
        #   - Shift imbalance has highest weight (fair workload is top priority)
        #   - Holiday imbalance is secondary
        #   - Double shifts are minimized last
        self.model.Minimize(
            10 * overall_imbalance +
            10 * holiday_imbalance +
            total_double_shifts
        )

    def main(self):
        self.solver = cp_model.CpSolver()
        # self.solver.parameters.log_search_progress = True
        # self.solver.log_callback = print
        # self.solver.parameters.log_to_stdout = False

        self.status = self.solver.Solve(self.model)




        # Print solution
        if self.status == cp_model.OPTIMAL or self.status == cp_model.FEASIBLE:
            print(f"Solution found ({(time.time() - self.startTime) * 10**3} ms):")

            output = {
                "Date": [i + 1 for i in range(self.num_days)] 
            }

            # Initialize all nurse entries in the output dictionary
            for n in range(self.num_nurses):
                output[str(n)] = [""] * self.num_days

            for d in range(self.num_days):
                # print(f"\nDay {d+1}:")
                # # for s_idx, s_type in enumerate(self.days[d]['shifts']):
                # for shift in Shift:
                #     assigned = [n for n in range(self.num_nurses) 
                #                 if self.solver.Value(self.assignments[n][d][shift])]                                                                   


                #     print(f"  {shift}: {assigned}")

                # Determine the shift symbol for each nurse on this day
                for n in range(self.num_nurses):
                    is_day = self.solver.Value(self.assignments[n][d][Shift.DAY]) == 1
                    is_evening = self.solver.Value(self.assignments[n][d][Shift.EVENING]) == 1
                    is_night = self.solver.Value(self.assignments[n][d][Shift.NIGHT]) == 1
                    is_off = self.solver.Value(self.assignments[n][d][Shift.OFF]) == 1
                    
                    # Determine the shift symbol based on combination
                    if is_day and is_evening:
                        shift_symbol = LocaleShift.DAY_EVENING.value
                    elif is_night and is_evening:
                        shift_symbol = LocaleShift.NIGHT_EVENING.value
                    elif is_day:
                        shift_symbol = LocaleShift.DAY.value
                    elif is_evening:
                        shift_symbol = LocaleShift.EVENING.value
                    elif is_night:
                        shift_symbol = LocaleShift.NIGHT.value
                    elif is_off:
                        shift_symbol = LocaleShift.OFF.value
                    else:
                        shift_symbol = "ERROR"  # For debugging purposes
                    
                    # Assign the shift symbol to the output dictionary
                    output[str(n)][d] = shift_symbol
                    

            # Verify all arrays have the same length
            # for key, value in output.items():
            #     print(f"{key}: {len(value)} items")


            # for i in output:
            #     print(output[i])
            #     print(len(output[i]))


            data = pd.DataFrame(output)
            data = data.transpose()
            data.columns = data.iloc[0]
            data = data[1:]

            print(f"Generated Dataframe in {(time.time() - self.startTime) * 10**3} ms")

            # print(data)

            
            match = re.search(r'input\\(.*?)(?=\.csv)', self.filename)

            filename = match.group(1)

            # data.to_csv("output.csv", encoding='utf-8-sig')
            data.to_csv(f'output\\{filename} Final.csv', encoding='utf-8-sig')

            print(f"Total Execution time: {(time.time() - self.startTime) * 10**3} ms")
        else:
            print("No solution found.")

        
if __name__ == '__main__':

    # Load settings from a file?
    settings = SettingLoader()
    num_days = settings.num_days
    weekends = settings.weekends

    # Load schedule files from a folder
    fileLoader = FileLoader("input")
    # Run the solver
    # solver = Solver(num_days, weekends)

    for file in fileLoader.files:
        solver = Solver(file, num_days, weekends)

    # Seperate success and failed file into 2 different folder