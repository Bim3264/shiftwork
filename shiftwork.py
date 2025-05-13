from ortools.sat.python import cp_model
from constant import Shift, LocaleShift
from dataimporter import DataImporter
import pandas as pd
import time

class Solver():

    def __init__(self):

        self.startTime = time.time()

        # Initialize the model
        self.model = cp_model.CpModel()

         # Sample Input Data (Customize this section)
        self.num_days = 31
        self.num_nurses = 12
        self.weekends = [5, 6, 12, 13, 19, 20, 26, 27]  # 0-based day indices
        self.shift_transitions = [('Evening', 'Night'), ('Night', 'Day')]

        self.days = []
        self.nurses = []

        self.shift_types = list({s for s in Shift})

        self.assignments = {}

        self.initShiftSettings()
        self.initAssignments()

        print('Init sucessfully')

        self.loadReqShifts("Test Schedule.csv", 5, 5)

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
        # Create assignment variables
        for n in range(self.num_nurses):
            self.assignments[n] = {}
            for d in range(self.num_days):
                self.assignments[n][d] = {}
                # for s_idx, s in enumerate(self.days[d]['shifts']):
                for shift in Shift:
                    self.assignments[n][d][shift] = self.model.NewBoolVar(f'n{n}_d{d}_s{shift.value}')


    def loadReqShifts(self, filename: str, maxDayOff: int, maxReqShift: int):
        data = DataImporter(filename, maxDayOff, maxReqShift).transform()

        # Check data length
        if len(data.reqShifts) == len(data.reqDayOff):
            for n in range(len(data.reqShifts)):
                self.nurses.append({
                    'requested_holidays': data.reqDayOff[n],  # 0-based days
                    'requested_shifts': data.reqShifts[n]  # {day: shift}
                })
        
        print('[Data] Requested shifts and holidays loaded.')

    # TODO: Change to one function per constraint for easier config in production
    def constraints(self):
         # 1. Handle holidays and shift requests
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

        # 2. All nurses must work exactly one shift per non-holiday day
        for n in range(self.num_nurses):
            for d in range(self.num_days):
                # Skip requested holidays (OFF already enforced above)
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
        

        # 3. Shift coverage requirements
        for d in range(self.num_days):
            for shift in Shift:
                required = self.days[d]['min_shifts'][shift]
                nurses_assigned = [self.assignments[n][d][shift] for n in range(self.num_nurses)]

                if shift is not Shift.OFF:
                    self.model.Add(sum(nurses_assigned) == required)
                else:
                    self.model.Add(sum(nurses_assigned) >= required)

        # 4. Prevent illegal shift transitions

        # Create variables to track double shifts for the objective function
        self.double_shift_vars = []

        for d in range(self.num_days):
            for n in range(self.num_nurses):


                # Create a variable for this nurse-day double shift
                double_shift = self.model.NewBoolVar(f'double_shift_n{n}_d{d}')
                day_var = self.assignments[n][d][Shift.DAY]
                evening_var = self.assignments[n][d][Shift.EVENING]
                night_var = self.assignments[n][d][Shift.NIGHT]
                off_var = self.assignments[n][d][Shift.OFF]

                # Set double_shift to 1 if nurse has more than 1 shift this day
                work_sum = self.model.NewIntVar(0, 3, f'work_sum2_n{n}_d{d}')
                self.model.Add(work_sum == day_var + evening_var + night_var)
                self.model.Add(work_sum >= 2).OnlyEnforceIf(double_shift)
                self.model.Add(work_sum < 2).OnlyEnforceIf(double_shift.Not())
                
                # Add to our list for the objective function
                self.double_shift_vars.append(double_shift)

                
                # Limit to 2 shift per day
                self.model.Add((self.assignments[n][d][Shift.DAY] + 
                                self.assignments[n][d][Shift.EVENING] + 
                                self.assignments[n][d][Shift.NIGHT]) <= 2)
                
                # Disallowe N -> D
                self.model.Add((self.assignments[n][d][Shift.DAY] +
                               self.assignments[n][d][Shift.NIGHT]) <= 1 )
                
                # Disallow E -> N
                if d < self.num_days - 1:
                    self.model.Add((self.assignments[n][d][Shift.EVENING] +
                                    self.assignments[n][d + 1][Shift.NIGHT]) <= 1)
                    
                # If off -> no work shift
                self.model.Add((self.assignments[n][d][Shift.DAY] + self.assignments[n][d][Shift.OFF]) <= 1)
                self.model.Add((self.assignments[n][d][Shift.EVENING] + self.assignments[n][d][Shift.OFF]) <= 1)
                self.model.Add((self.assignments[n][d][Shift.NIGHT] + self.assignments[n][d][Shift.OFF]) <= 1)


                self.model.Add((self.assignments[n][d][Shift.DAY] + 
                            self.assignments[n][d][Shift.EVENING] + 
                            self.assignments[n][d][Shift.NIGHT] +
                            self.assignments[n][d][Shift.OFF]) >= 1)
                
                self.model.Minimize((self.assignments[n][d][Shift.DAY] + 
                                self.assignments[n][d][Shift.EVENING] + 
                                self.assignments[n][d][Shift.NIGHT]))


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
                

            


        # 5. Fair shift distribution
        # Create count variables for each nurse and shift type
        self.shift_counts = {}
        for n in range(self.num_nurses):
            self.shift_counts[n] = {}
            for s in self.shift_types:
                self.shift_counts[n][s] = self.model.NewIntVar(0, self.num_days, f'count_{n}_{s}')


    def distribute(self):
        # Link assignment variables to count variables
        for n in range(self.num_nurses):
            for s in self.shift_types:
                count = []
                for d in range(self.num_days):
                    if s in self.days[d]['shifts']:
                        # s_idx = self.days[d]['shifts'].index(s)
                        count.append(self.assignments[n][d][s])
                self.model.Add(self.shift_counts[n][s] == sum(count))

        # Ensure fair distribution
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

        # Now minimize the largest imbalance across all shifts
        overall_imbalance = self.model.NewIntVar(0, self.num_days, 'overall_imbalance')
        self.model.AddMaxEquality(overall_imbalance, imbalance_vars)

         # Create a combined objective with:
        # 1. Minimize overall imbalance (weight: 100)
        # 2. Minimize double shifts (weight: 50 for each one)
        total_double_shifts = self.model.NewIntVar(0, self.num_nurses * self.num_days, 'total_double_shifts')
        self.model.Add(total_double_shifts == sum(self.double_shift_vars))


        # self.model.Minimize(overall_imbalance)
        # self.model.Minimize((2 * overall_imbalance) + total_double_shifts)
        self.model.Minimize(total_double_shifts)



    def main(self):
        self.solver = cp_model.CpSolver()
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

            data.to_excel("output.xlsx")

            print(f"Total Execution time: {(time.time() - self.startTime) * 10**3} ms")
        else:
            print("No solution found.")

        
if __name__ == '__main__':
    solver = Solver()