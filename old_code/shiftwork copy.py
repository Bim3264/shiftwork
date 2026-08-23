import time
import random
import pandas as pd
import dataimporter as di
from constant import Shift, LocaleShift

startTime = time.time()

dayInMonth = 30

# Minimum Shift Requirement
minimalNursePerShift = {
    Shift.DAY: 5,
    Shift.EVENING: 3,
    Shift.NIGHT: 3
}

shiftsPool = {
    "NurseA": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseB": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseC": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseD": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseE": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseF": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseG": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseH": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseI": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseJ": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseK": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0},
    "NurseL": {Shift.DAY: 0, Shift.EVENING: 0, Shift.NIGHT: 0}
}

# Day off of Nurses in Day of the Month (start at 1 !!!)
nursesDayOff, dayShift, eveningShift, nightShift = di.DataImporter("Test Schedule.csv", 5, 5).transform()

# Calendar day - 1
# requestedShifts = [
#     {'NurseA': Shift.DAY, 'NurseC': Shift.EVENING},
#     {'NurseA': Shift.EVENING, "NurseE": Shift.NIGHT}]

requestedShifts = [{} for _ in range(dayInMonth)]

shifts = {}


def addRequestedShifts(calendarDay: int, nurse: str, shift: Shift):
    if 1 <= calendarDay <= dayInMonth:
        requestedShifts[calendarDay - 1][nurse] = shift


# addRequestedShifts(1, "NurseA", Shift.DAY)
# addRequestedShifts(1, "NurseC", Shift.NIGHT)


# TODO: Handle speacial shifts e.g. Only Day shift
# TODO: Add weekends
def assignShifts(shift: Shift, minimalShift: int):

    # shiftPerNurse = int(minimalShift / len(shiftsPool))

    # # Check if minimalShift is divisible by number of nurse
    # if minimalShift % len(shiftsPool) == 0:
    #     for nurse in shiftsPool:
    #         shiftsPool[nurse][shift] = shiftPerNurse
    # else: 
    #     missingShifts = minimalShift - (shiftPerNurse * len(shiftsPool))

    #     unluckyNurses = random.sample(list(shiftsPool), missingShifts)

    #     for nurse in shiftsPool:
    #         if nurse in unluckyNurses:
    #             shiftsPool[nurse][shift] = shiftPerNurse + 1
    #         else:
    #             shiftsPool[nurse][shift] = shiftPerNurse

    for shift in Shift:
        totalShifts = minimalNursePerShift[shift] * dayInMonth
        base, extra = divmod(totalShifts, len(shiftsPool))
        assigned = random.sample(list(shiftsPool), extra)
        for nurse in shiftsPool:
            shiftsPool[nurse][shift] = base + (1 if nurse in assigned else 0)
        

def sampleIfAvailable(dataToSample: list, sampleSize: int):

    if len(dataToSample) > sampleSize:
        return random.sample(dataToSample, sampleSize)
    else: return dataToSample


def assignMissingShiftFirstPass(shiftRandomPool: list, missingShifts: int, shift: Shift, day: int):
    
    # Sample nurse from the pool
    sampledNurse = sampleIfAvailable(shiftRandomPool, missingShifts)

    # Get the current nurses in shift in the day
    updatedShift = shifts[str(day)][shift.value]
    # Add the newly sampled nurses
    updatedShift.extend(sampledNurse)

    # Remove shift from shift pool
    for nurse in sampledNurse:
        # shiftsPool[nurse][shift] = shiftsPool[nurse][shift] - 1
        trySubtractShiftFromPool(nurse, shift, 1)

    # Update the shift dictionary with the updated shift
    shifts[str(day)].update({shift.value: updatedShift})

    return sampledNurse
    

def nurseAvailable(shift: Shift, allNursesToday: list, day: int):

    return [
        nurse for nurse in shiftsPool
        if nurse not in allNursesToday 
        and day + 1 not in nursesDayOff.get(nurse, []) 
        and nurseHaveShiftInShiftsPool(nurse, shift)
    ]


def nurseHaveShiftInShiftsPool(nurse: str, shift: Shift):
    if shiftsPool[nurse][shift] > 0:
        return True
    else: return False


def trySubtractShiftFromPool(nurse: str, shift: Shift, amount: int):
    if nurseHaveShiftInShiftsPool(nurse, shift):
        shiftsPool[nurse][shift] = shiftsPool[nurse][shift] - amount

        return True
    else:
        return False


# Obsolete
def generateShiftsPool():
    
    # Loop through each shift type and assign shift to each nurse
    for shift in (Shift):
        minimalShift = minimalNursePerShift[shift] * dayInMonth
        assignShifts(shift, minimalShift)

# TODO: Sample randomly
def firstPass():

    sampleChance = 0.8
    maximalRequestedShifts = 5

    nurseShifts = {}

    for i in range(dayInMonth):
        for nurse in requestedShifts[i]:
            pass


def secondPass():
    # First pass: Assign all conditions
    for i in range(dayInMonth):

        # Variable for each shift in day i
        dayShift = []
        eveningShift = []
        nightShift = []

        # List of nurse available for that shift
        dayShiftAvailable = []
        eveningShiftAvailable = []
        nightShiftAvailable = []

        # Check if a nurse still have that type of shift available
        for nurse in shiftsPool:
            # TODO: Add nurse's prefered shifts
            # BUG: ช/บ/ด allowed!!
            # BUG: The requested shift is add to the sample list instead of assinging to that day -> add a new loop assigning requested shift first with minimum assign percent
            # Make sure it's not the nurse's holiday
            if i + 1 not in nursesDayOff[nurse]:
                
                if nurse in requestedShifts[i]:
                    if requestedShifts[i][nurse] == Shift.DAY:
                        if nurseHaveShiftInShiftsPool(nurse, Shift.DAY):
                            dayShiftAvailable.append(nurse)
                    
                    if requestedShifts[i][nurse] == Shift.EVENING:
                        if nurseHaveShiftInShiftsPool(nurse, Shift.EVENING):
                            eveningShiftAvailable.append(nurse)

                    if requestedShifts[i][nurse] == Shift.NIGHT:
                        if nurseHaveShiftInShiftsPool(nurse, Shift.NIGHT):
                            nightShiftAvailable.append(nurse)
                else:
                    if nurseHaveShiftInShiftsPool(nurse, Shift.DAY):
                        dayShiftAvailable.append(nurse)

                    if nurseHaveShiftInShiftsPool(nurse, Shift.EVENING):
                        eveningShiftAvailable.append(nurse)

                    if nurseHaveShiftInShiftsPool(nurse, Shift.NIGHT):
                        nightShiftAvailable.append(nurse)

        # Check for evening->night
        if i > 0:
            for nurse in shifts[str(i - 1)]["evening"]:
                if nurse in nightShiftAvailable:
                    nightShiftAvailable.remove(nurse)

        # TODO: Change to intersect
        for nurse in nightShiftAvailable:
            if nurse in dayShiftAvailable:
                dayShiftAvailable.remove(nurse)

        # Randomly sample nurse from the available lists
        nightShift = sampleIfAvailable(nightShiftAvailable, minimalNursePerShift[Shift.NIGHT])
        dayShift = sampleIfAvailable(dayShiftAvailable, minimalNursePerShift[Shift.DAY])
        eveningShift = sampleIfAvailable(eveningShiftAvailable, minimalNursePerShift[Shift.EVENING])

        # Make sure the nurse only got assign 1 shift at first.
        for nurse in nightShift:
            if nurse in dayShift:
                dayShift.remove(nurse)
            
            if nurse in eveningShift:
                eveningShift.remove(nurse)

        for nurse in dayShift:
            if nurse in eveningShift:
                eveningShift.remove(nurse)

        for nurse in eveningShift:
            if nurse in nightShift:
                eveningShift.remove(nurse)


        for nurse in dayShift:
            trySubtractShiftFromPool(nurse, Shift.DAY, 1)

        for nurse in eveningShift:
            trySubtractShiftFromPool(nurse, Shift.EVENING, 1)

        for nurse in nightShift:
            trySubtractShiftFromPool(nurse, Shift.NIGHT, 1)

        # TODO: Change to update shift
        shifts[str(i)] = {"night": nightShift, "day": dayShift, "evening": eveningShift} 


def thirdPass():
    # Pass 2: Add shift, only add nurse that don't have shift on that day. (Prefer no double shift)
    for i in range(dayInMonth):

        shiftsToday = shifts[str(i)]

        nightShift = shiftsToday["night"]
        dayShift = shiftsToday["day"]
        eveningShift = shiftsToday["evening"]

        allNursesToday = set(nightShift) | set(dayShift) | set(eveningShift)

        for shift, shiftToday, minNurses in zip(
            # ["night", "day", "evening"],
            [Shift.NIGHT, Shift.DAY, Shift.EVENING],
            [nightShift, dayShift, eveningShift],
            [minimalNursePerShift[Shift.NIGHT], minimalNursePerShift[Shift.DAY], minimalNursePerShift[Shift.EVENING]]):

            if len(shiftToday) < minNurses:
                
                availableNurses = nurseAvailable(shift, allNursesToday, i)

                missingShifts = assignMissingShiftFirstPass(availableNurses, minNurses - len(shiftToday), shift, i)

                allNursesToday.update(missingShifts)


def fourthPass():
    # Pass 3: Add double shifts
    for i in range(dayInMonth):

        nightShift = shifts[str(i)]["night"]
        dayShift = shifts[str(i)]["day"]
        eveningShift = shifts[str(i)]["evening"]

        allNursesToday = []

        nightShiftRandomPool = []
        dayShiftRandomPool = []
        eveningShiftRandomPool = []

        for nurse in shiftsPool:
            if  i + 1 in nursesDayOff[nurse]:
                pass
            else:
                if nurseHaveShiftInShiftsPool(nurse, Shift.NIGHT):
                    if i > 0:
                        if nurse not in shifts[str(i - 1)]["evening"] and nurse not in dayShift and nurse not in nightShift:
                            nightShiftRandomPool.append(nurse)

                if nurseHaveShiftInShiftsPool(nurse, Shift.DAY):
                    if nurse not in nightShift and nurse not in dayShift:
                        dayShiftRandomPool.append(nurse)

                if nurseHaveShiftInShiftsPool(nurse, Shift.EVENING):
                    if nurse in nightShift or nurse in dayShift:
                        if nurse not in eveningShift:
                            eveningShiftRandomPool.append(nurse)

        # print(f"[Pass 3] Random Pool: Night {nightShiftRandomPool}, Day {dayShiftRandomPool}, Evening {eveningShiftRandomPool}")

        if len(nightShift) < minimalNursePerShift[Shift.NIGHT]:
            sampleNurse = sampleIfAvailable(nightShiftRandomPool, minimalNursePerShift[Shift.NIGHT] - len(nightShift))
            allNursesToday.extend(sampleNurse)

            for nurse in sampleNurse:
                trySubtractShiftFromPool(nurse, shift, 1)

            updateShift = shifts[str(i)]["night"]
            updateShift.extend(sampleNurse)

            shifts[str(i)].update({'night': updateShift})

        if len(dayShift) < minimalNursePerShift[Shift.DAY]:
            for nurse in shiftsPool:
                if nurse in allNursesToday:
                    pass
                else:
                    if nurseHaveShiftInShiftsPool(nurse, Shift.DAY):
                        dayShiftRandomPool.append(nurse)

            sampleNurse = sampleIfAvailable(dayShiftRandomPool, minimalNursePerShift[Shift.DAY] - len(dayShift))
            allNursesToday.extend(sampleNurse)

            for nurse in sampleNurse:
                trySubtractShiftFromPool(nurse, Shift.DAY, 1)

            updateShift = shifts[str(i)]["day"]
            updateShift.extend(sampleNurse)

            shifts[str(i)].update({'day': updateShift})

        if len(eveningShift) < minimalNursePerShift[Shift.EVENING]:
            for nurse in shiftsPool:
                if nurse in allNursesToday:
                    pass
                elif nurse not in allNursesToday:
                    if "evening" in shiftsPool[nurse]:
                        eveningShiftRandomPool.append(nurse)

            sampleNurse = sampleIfAvailable(eveningShiftRandomPool, minimalNursePerShift[Shift.EVENING] - len(eveningShift))
            allNursesToday.extend(sampleNurse)

            for nurse in sampleNurse:
                trySubtractShiftFromPool(nurse, Shift.EVENING, 1)

            updateShift = shifts[str(i)]["evening"]
            updateShift.extend(sampleNurse)

            shifts[str(i)].update({'evening': updateShift})


def generateShiftPass(passNumber: int):
    if passNumber == 1:
        firstPass()
    elif passNumber == 2:
        secondPass()
    elif passNumber == 3:
        thirdPass()
    elif passNumber == 4:
        fourthPass()


def generateShifts():

    # secondPass()
    # thirdPass()
    # fourthPass()

    generateShiftPass(2)
    generateShiftPass(3)
    generateShiftPass(4)

    # Pass 4: Add unlucky shifts
    # Pass 5: Add last resort (unequal shift)

def countShifts(dataframe: pd.DataFrame):

    maxmialDeviationFromMean = 2
    withinStandard = True

    nightList = dataframe[Shift.NIGHT.value].to_list()
    dayList = dataframe[Shift.DAY.value].to_list()
    eveningList = dataframe[Shift.EVENING.value].to_list()

    nursesNightShift = []
    nursesDayShift = []
    nursesEveningShift = []

    name = []
    nightShiftCount = []
    dayShiftCount = []
    eveningShiftCount = []

    df = None

    for i in range(len(dayList)):
        nursesNightShift.extend(nightList[i])
        nursesDayShift.extend(dayList[i])
        nursesEveningShift.extend(eveningList[i])

    for nurse in shiftsPool:
        name.append(nurse)

        countNight = nursesNightShift.count(nurse)
        countDay = nursesDayShift.count(nurse)
        countEvening = nursesEveningShift.count(nurse)

        # print(f"{nurse} count night: {countNight}")

        nightShiftCount.append(countNight)
        dayShiftCount.append(countDay)  
        eveningShiftCount.append(countEvening)


        # print(f"{nurse} have {count} day shifts.")
    if withinStandard == True:
        for value in nightShiftCount:
            mean = sum(nightShiftCount) / len(nightShiftCount)
            if (abs(value - mean) > maxmialDeviationFromMean):
                withinStandard = False
                break

        for value in dayShiftCount:
            mean = sum(dayShiftCount) / len(nightShiftCount)
            if (abs(value - mean) > maxmialDeviationFromMean):
                withinStandard = False
                break

        for value in eveningShiftCount:
            mean = sum(eveningShiftCount) / len(nightShiftCount)
            if (abs(value - mean) > maxmialDeviationFromMean):
                withinStandard = False
                break

    # for i in range(dayInMonth):
    #     print(shifts[str(i)])
    # print(shifts)
        
    if withinStandard:
        dict = {"Name": name,"Night": nightShiftCount, "Day": dayShiftCount, "Evening": eveningShiftCount}
        df = pd.DataFrame(dict)
    
        # print(df)

    return withinStandard, df


def formatShifts():

    data = {
        "Date": [i + 1 for i in range(dayInMonth)] 
    }

    for nurse in shiftsPool:
        for i in range(dayInMonth):
            if nurse in shifts[str(i)][Shift.DAY.value]:
                if nurse not in shifts[str(i)][Shift.EVENING.value]:
                    if nurse not in data:
                        data[nurse] = [LocaleShift.DAY.value]
                    else: data[nurse].append(LocaleShift.DAY.value)
                else:
                    if nurse not in data:
                        data[nurse] = [LocaleShift.DAY_EVENING.value]
                    else: data[nurse].append(LocaleShift.DAY_EVENING.value)
                    
            elif nurse in shifts[str(i)][Shift.EVENING.value]:
                if nurse not in shifts[str(i)][Shift.DAY.value]:
                    if nurse not in data:
                        data[nurse] = [LocaleShift.EVENING.value]
                    else: data[nurse].append(LocaleShift.EVENING.value)
            elif nurse in shifts[str(i)][Shift.NIGHT.value]:
                if nurse not in data:
                    data[nurse] = [LocaleShift.NIGHT.value]
                else: data[nurse].append(LocaleShift.NIGHT.value)
            else:
                if nurse not in data:
                    data[nurse] = ["off"]
                else: data[nurse].append("off")

    data = pd.DataFrame(data)
    data = data.transpose()
    data.columns = data.iloc[0]
    data = data[1:]
    # data.set_index("Date", inplace=True)

    # for item in data:
    #     print(data[item])

    print(data)
                
    return(data)


# generateShiftsPool()
# generateShifts()

fullyAssigned = False
withinStandard = False

while (fullyAssigned == False or withinStandard == False):
    generateShiftsPool()
    generateShifts()

    for i in range(dayInMonth):
        if len(shifts[str(i)]["night"]) != minimalNursePerShift[Shift.NIGHT] or len(shifts[str(i)]["day"]) != minimalNursePerShift[Shift.DAY] or len(shifts[str(i)]["evening"]) != minimalNursePerShift[Shift.EVENING]:
            fullyAssigned = False
        else:
            fullyAssigned = True

    if fullyAssigned:
        df = pd.DataFrame(shifts)
        df = df.transpose()

        withinStandard, sumDf = countShifts(df)

        # print(withinStandard)

        if withinStandard:
            endTime = time.time()

            print(df)
            print(sumDf)

            shiftOutput = formatShifts()
            shiftOutput.to_excel("output.xlsx")

            # print(shifts["1"])
            # print(withinStandard)

            print(f"Execution time: {(endTime - startTime) * 10**3} ms")

            for nurse in shiftsPool:
                for shift in (Shift):
                    if nurseHaveShiftInShiftsPool(nurse, shift):
                        print(f"{nurse} : {shiftsPool[nurse]}")

            
        
        

