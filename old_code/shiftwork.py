import time
import random
import pandas as pd
import dataimporter as di
from constant import Shift, LocaleShift

class ShiftGenerator():

    # The only place you put TODOs, if it's a BUG add the comment to the place where the bug is.
    # TODO: Add the requested shift function -> not implement yet

    def __init__(self, daysInMonth: int, reqScheduleFile: str):

        self.daysInMonth = daysInMonth
        self.shifts = {}

        self.minimalNursePerShift = {
            Shift.DAY: 5,
            Shift.EVENING: 3,
            Shift.NIGHT: 3
        }

        self.initDataImporter(reqScheduleFile, 5, 5)
        self.initShiftsPool(self.allNurses)
        self.initOptions()

        self.fillShiftsPool()

        self.generate()

    def initDataImporter(self, reqFile: str, maxDayOff: int, maxReqShift: int):
        
        data = di.DataImporter(reqFile, maxDayOff, maxReqShift)
        data.transform()
        
        self.allNurses = data.all_nurses
        self.reqDayOff = data.nursesDayOff
        self.reqDayShift = data.reqDayShift
        self.reqEveningShift = data.reqEveningShift
        self.reqNightShift = data.reqNightShift

        print(data.all_nurses)

        return self

        # print(self.allNurses)


    def initShiftsPool(self, nursesList: list):

        self.shiftsPool  = {}

        for nurse in nursesList:
            self.shiftsPool[nurse] = {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0}

        return self
        # print(self.shiftsPool)


    # Init all options to the default value
    def initOptions(self):
        self.REQ_HOLIDAYS_ALWAYS_RESPECTED = True
        self.REQ_HOLIDAYS_MOSTLY_RESPECTED = False
        self.REQ_HOLIDAYS_MOSTLY_THREASHOLD = 0.8
        self.REQ_SHIFTS_ALWAYS_RESPECTED = True
        self.ALLOWED_ALWAYS_DAY_SHIFTS = True
        


    def setMinimalNursePerShift(self, shift: Shift, amount: int):
        self.minimalNursePerShift[shift] = amount
        return self
    
    def fillShiftsPool(self):

        for shift in Shift:

            totalShifts = self.minimalNursePerShift[shift] * self.daysInMonth                # The total shifts of the month
            base, extra = divmod(totalShifts, len(self.shiftsPool))                         # Base shift for each nurse, and extra shifts required for unlucky nurse/s
            assigned = random.sample(list(self.shiftsPool), extra)                          # Randomly select the unlucky nurses

            for nurse in self.shiftsPool:
                self.shiftsPool[nurse][shift] = base + (1 if nurse in assigned else 0)      # Fill the shifts pool
    

        return self
    
    def sampleList(self, input: list, sampleSize: int):
        """Sample the list if the list is bigger than the sample size
        
        Keyword arguments:
            input -- Input list
            sampleSize -- SampleSize

        Return: sampled list
        """
        
        if len(input) > sampleSize:
            return random.sample(input, sampleSize)
        else: return input

    def nurseAvailable(self, shift: Shift, allNursesToday: list, day: int):

        return [
            nurse for nurse in self.shiftsPool
            if nurse not in allNursesToday 
            and day + 1 not in self.nursesDayOff.get(nurse, []) 
            and self.nurseHaveShiftInShiftsPool(nurse, shift)
        ]
    
    def nurseHaveShiftInShiftsPool(self, nurse: str, shift: Shift):
        if self.shiftsPool[nurse][shift] > 0:
            return True
        else: return False

    
    # Previously trySubtractShiftFromPool
    def assignShift(self, nurse: str, shift: Shift):
        if self.nurseHaveShiftInShiftsPool(nurse, shift):
            self.shiftsPool[nurse][shift] = self.shiftsPool[nurse][shift] - 1

            return True
        else: return False


    def generate(self):
        self.generateReqShifts()


    def generateReqShifts(self):

        print(self.reqDayShift["NurseA"])


ShiftGenerator(30, "Test Schedule.csv")