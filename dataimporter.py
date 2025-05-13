import pandas as pd
from constant import Shift, LocaleShift
import random

class DataImporter():

    def __init__(self, filename: str, maxDayOff: int, maxReqShift: int):
        """Init the DataImporter
        
        Keyword arguments:
            filename -- The schedule file for importing
            maxDayOff -- Maximum day off a nurse can have
            maxReqShift -- Maximum requested shift allowed for a nurse (excluding day off)

        Return: None

        """
        
        # Init the self arguemtn
        self.filename = filename 
        self.maxDayOff = maxDayOff
        self.maxReqShift = maxReqShift

        self.reqShifts = []
        self.reqDayOff = []

        # Init the dataframe from the imported CSV file
        self.df = pd.read_csv(self.filename, encoding='utf-8-sig')
        # Make sure all columns name are strings
        self.df.columns = self.df.columns.astype(str)



    def numberOfNurses(self):
        """Number of nureses in the dataframe"""
        return len(self.df)
    

    def clean(self, val):
        """Clean each cell value of all unwanted characters
        
        Keyword arguments:
            val -- the value to be clean
        Return: Cleaned value
        """

        if pd.isna(val):
            return ""

        # return str(val).strip().strip("'").strip('"')
        return str(val).strip().replace('\ufeff', '')


    # Sample if the input is greater than the max
    def sample(self, input, max: int):
        if len(input) > max: 
                return random.sample(input, self.maxReqShift)
        else:
            return input


    def transform(self):
        """Transform data from the dataframe to a specific data type for the algorithm to ingest. 
        If each of the data exceed the maximum set in init, they will be randomly sampled.
        
        Return: 
            nurseDayOff -- dictionary of each nurse's day off e.g. {'NurseA': [1,3,4,21]}
            reqDayShift -- dictionary of each nurse's requested day shift 
            reqEveningShift -- dictionary of each nurse's requested evening shift
            reqNightShift -- dictionary of each nurse's requested night shift
        """
        


        # Create full list of expected nurses
        # TODO: Change to get the actual name from the file
        self.all_nurses = [f"Nurse{chr(c)}" for c in range(ord('A'), ord('A') + self.numberOfNurses())]  # NurseA to NurseL

        # Initialize output dictionary with all nurses
        self.nursesDayOff = {nurse: [] for nurse in self.all_nurses}

        # TODO: Hot code!!! Find a more efficient way to do this pls.
        self.reqDayShift = {nurse: [] for nurse in self.all_nurses}
        self.reqEveningShift = {nurse: [] for nurse in self.all_nurses}
        self.reqNightShift = {nurse: [] for nurse in self.all_nurses}

        # Fill dictionary with actual days off
        for index, row in self.df.iterrows():
            # Transform day off data
            nurse_name = row["Name"]

            # Add each requested shift/day off to their respective dictionary
            # Change to 0-based day
            days_off = [int(day) - 1 for day in row.index[1:] if row[day] == "off"]

            day_shift = [
                int(day) - 1 for day in row.index[1:]
                # if self.clean(row[day]) in {LocaleShift.DAY.value, LocaleShift.DAY_EVENING.value}
                if self.clean(row[day]) in {self.clean(LocaleShift.DAY.value), self.clean(LocaleShift.DAY_EVENING.value)}
            ]

            evening_shift = [
                int(day) - 1 for day in row.index[1:]
                if self.clean(row[day]) in {LocaleShift.EVENING.value, LocaleShift.DAY_EVENING.value}
            ]

            night_shift = [
                int(day) - 1 for day in row.index[1:]
                if self.clean(row[day]) == LocaleShift.NIGHT.value
            ]

            # If number of requested day off or requested shift exceed the maximum -> sample to maximum.
            # if len(days_off) > self.maxDayOff: self.nursesDayOff[nurse_name] = random.sample(days_off, self.maxDayOff)

            if len(day_shift) > self.maxReqShift: 
                self.reqDayShift[nurse_name] = random.sample(day_shift, self.maxReqShift)
            else:
                self.reqDayShift[nurse_name] = day_shift

            self.reqDayShift[nurse_name] = self.sample(day_shift, self.maxReqShift)

            # Assuming day_shift is a list of days, e.g., [3, 1, 8, 18, 23, 7, 22]
            group = []
            for day in day_shift:
                group.append({day: Shift.DAY})
            self.reqShifts.append(group)

            for day in evening_shift:
                self.reqShifts[index].append({day: Shift.EVENING})

            for day in night_shift:
                self.reqShifts[index].append({day: Shift.NIGHT})

            self.reqDayOff.append(self.sample(days_off, self.maxDayOff))


        for n in range(len(self.reqShifts)):
            # print(self.reqShifts[n])
            sampledList = self.sample(self.reqShifts[n], self.maxReqShift)

            merged_dict = {}
            for d in sampledList:
                merged_dict.update(d)

            self.reqShifts[n] = merged_dict

            print(f'Nurse {n} : {self.reqShifts[n]}')

            # if len(evening_shift) > self.maxReqShift: self.reqEveningShift[nurse_name] = random.sample(evening_shift, self.maxReqShift)
            # if len(night_shift) > self.maxReqShift: self.reqNightShift[nurse_name] = random.sample(night_shift, self.maxReqShift)



        return self
        # return all_nurses, nursesDayOff, reqDayShift, reqEveningShift, reqNightShift
    
    

# Testing code

filename = "Test Schedule.csv"

# off, day, evening, night = DataImporter(filename).transform()
dataimporter = DataImporter(filename, 5, 5).transform()

# print(off)
print(dataimporter.reqShifts)
# print(dataimporter.reqShifts)
# print(len(dataimporter.reqShifts))

# print(evening)
# print(night)

# print(LocaleShift.DAY.value)