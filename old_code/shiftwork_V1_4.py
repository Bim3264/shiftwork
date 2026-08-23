# TODO: Add prefered shifts
# TODO: Add weekend shifts
import enum
import random

class Shift(enum.Enum):
    NIGHT = "night"
    DAY = "day"
    EVENING = "evening"

minimumNursePerShift = {Shift.DAY: 5, Shift.EVENING: 3, Shift.NIGHT: 3}

daysInMonth = 30
shifts = {}
shiftsPool = {}

# Days in computer days not calendar day (calendar day - 1)
nurses = {"NurseA": {"holidays": [0,5,15], "requested_shift": {1: "day", 16: "night"}},
          "NurseB": {"holidays": [], "requested_shift": []},
          "NurseC": {"holidays": [], "requested_shift": []},
          "NurseE": {"holidays": [], "requested_shift": []},
          "NurseD": {"holidays": [], "requested_shift": []},
          "NurseF": {"holidays": [], "requested_shift": []},
          "NurseG": {"holidays": [], "requested_shift": []},
          "NurseH": {"holidays": [], "requested_shift": []},
          "NurseI": {"holidays": [], "requested_shift": []},
          "NurseJ": {"holidays": [], "requested_shift": []},
          "NurseK": {"holidays": [], "requested_shift": []},
          "NurseL": {"holidays": [], "requested_shift": []}}


def initShift():
    for i in range(daysInMonth):
        shifts[i] = {Shift.NIGHT: [], Shift.DAY: [], Shift.EVENING: []}


def assignNormalShifts(shift: Shift, minimalShift):

    shiftPerNurse = int(minimalShift / len(nurses))

    print(f"Shifts Per Nurse {shift}: " + str(shiftPerNurse))

    # for i in range(shiftPerNurse):
    #     for nurse in nurses:
    #         shiftsPool[nurse][shift] = shiftsPool[nurse][shift] + 1

    for nurse in nurses:

        nurseShift = shiftsPool[nurse][shift]
        nurseShift.update(shiftPerNurse)


def assignUnluckyShifts(shift: Shift, minimalShift):
    shiftPerNurse = int(minimalShift / len(nurses))

    assignNormalShifts(shift, minimalShift)

    missingShifts = minimalShift - (shiftPerNurse * len(nurses))
    # print("Missing Shifts: " + str(missingShifts))

    unluckyNurses = random.sample(list(nurses), missingShifts)

    for nurse in unluckyNurses:
        shiftsPool[nurse][shift] = shiftsPool[nurse][shift] + missingShifts


def generateShiftsPool():

    for shift in (Shift):
        minimalShift = minimumNursePerShift[shift] * daysInMonth

        if minimalShift % len(nurses) == 0:
            assignNormalShifts(shift, minimalShift)
        else:
            assignUnluckyShifts(shift, minimalShift)


initShift()
generateShiftsPool()
print(len(nurses))
print(shiftsPool)
