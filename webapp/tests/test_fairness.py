import unittest
from webapp.core.fairness import fairness_stats, _parse_cell


class ParseCellTests(unittest.TestCase):
    def test_singles_doubles_off(self):
        self.assertEqual(_parse_cell("ช"), ["day"])
        self.assertEqual(_parse_cell("บ"), ["evening"])
        self.assertEqual(_parse_cell("ด"), ["night"])
        self.assertEqual(_parse_cell("ช/บ"), ["day", "evening"])
        self.assertEqual(_parse_cell("ด/บ"), ["night", "evening"])
        self.assertEqual(_parse_cell("off"), [])
        self.assertEqual(_parse_cell(""), [])


class FairnessStatsTests(unittest.TestCase):
    def setUp(self):
        # 4 days, days 3 & 4 are the weekend. Two nurses.
        self.grid = {
            "days": ["1", "2", "3", "4"],
            "rows": [
                {"name": "A", "cells": ["ช", "ด", "off", "ด/บ"]},  # day1 eve1 night2, wknd on d4
                {"name": "B", "cells": ["off", "off", "ช", "off"]},  # 1 day, on weekend
            ],
        }

    def test_none_when_empty(self):
        self.assertIsNone(fairness_stats({"days": [], "rows": []}))

    def test_double_counts_both_slots(self):
        s = fairness_stats(self.grid, weekend_days=[3, 4])
        a = s["per_nurse"][0]
        self.assertEqual((a["day"], a["evening"], a["night"]), (1, 1, 2))
        self.assertEqual(a["shifts"], 4)          # ด/บ counts as 2 slots
        self.assertEqual(a["working_days"], 3)    # ...but 1 calendar day
        self.assertEqual(a["off_days"], 1)

    def test_weekend_slots(self):
        s = fairness_stats(self.grid, weekend_days=[3, 4])
        a, b = s["per_nurse"]
        self.assertEqual(a["weekend_shifts"], 2)  # ด/บ on day4
        self.assertEqual(a["night_weekend"], 1)
        self.assertEqual(b["weekend_shifts"], 1)  # ช on day3

    def test_spread_and_totals(self):
        s = fairness_stats(self.grid, weekend_days=[3, 4])
        self.assertEqual(s["totals"]["night"], 2)
        self.assertEqual(s["totals"]["shifts"], 5)
        self.assertEqual(s["spread"]["night"]["range"], 2)  # A=2, B=0

    def test_no_weekend_arg_zeroes_weekend_cols(self):
        s = fairness_stats(self.grid)
        self.assertEqual(s["totals"]["weekend_shifts"], 0)


if __name__ == "__main__":
    unittest.main()


class SeniorExclusionTests(unittest.TestCase):
    def _grid(self):
        # 3 nurses over 2 days: nurse0 is a day-only senior with 0 nights.
        return {"days": ["1", "2"], "rows": [
            {"nurse_index": 0, "name": "Head", "cells": ["ช", "ช"]},
            {"nurse_index": 1, "name": "R1",   "cells": ["ด", "ด"]},
            {"nurse_index": 2, "name": "R2",   "cells": ["ด", "off"]},
        ]}

    def test_seniors_tagged_and_excluded_from_spread(self):
        s = fairness_stats(self._grid(), senior_indices=[0])
        self.assertTrue(s["per_nurse"][0]["senior"])
        self.assertFalse(s["per_nurse"][1]["senior"])
        self.assertEqual(s["senior_excluded"], 1)
        # night spread over R1(2) & R2(1) only -> range 1, NOT 2 (would be 0..2 with Head)
        self.assertEqual(s["spread"]["night"]["max"], 2)
        self.assertEqual(s["spread"]["night"]["min"], 1)
        self.assertEqual(s["spread"]["night"]["range"], 1)
        # totals still cover everyone
        self.assertEqual(s["totals"]["day"], 2)

    def test_falls_back_when_all_senior(self):
        s = fairness_stats(self._grid(), senior_indices=[0, 1, 2])
        self.assertEqual(s["senior_excluded"], 0)          # no exclusion applied
        self.assertEqual(s["spread"]["night"]["max"], 2)   # spread over everyone


class VacationStatsTests(unittest.TestCase):
    def _grid(self):
        # 4 days. A: works d1, vacation d2, off d3, works d4.
        #          B: no vacation at all (off d1, works the rest).
        return {"days": ["1", "2", "3", "4"], "rows": [
            {"nurse_index": 0, "name": "A", "cells": ["ช", "vac", "off", "ด"]},
            {"nurse_index": 1, "name": "B", "cells": ["off", "ช", "ช", "ช"]},
        ]}

    def test_vac_counted_separately_and_not_as_off_or_working(self):
        a = fairness_stats(self._grid())["per_nurse"][0]
        self.assertEqual(a["vac_days"], 1)
        self.assertEqual(a["working_days"], 2)   # ช d1, ด d4 — 'vac' is not working
        self.assertEqual(a["off_days"], 1)       # only d3; the vacation day is excluded

    def test_days_partition_exactly(self):
        s = fairness_stats(self._grid())
        for n in s["per_nurse"]:
            self.assertEqual(n["working_days"] + n["off_days"] + n["vac_days"],
                             s["num_days"],
                             "working + off + vac must account for every day")

    def test_totals_and_has_vacations_flag(self):
        s = fairness_stats(self._grid())
        self.assertEqual(s["totals"]["vac_days"], 1)
        self.assertTrue(s["has_vacations"])

    def test_no_vacations_flag_false_and_off_unchanged(self):
        grid = {"days": ["1", "2"], "rows": [
            {"nurse_index": 0, "name": "A", "cells": ["ช", "off"]},
        ]}
        s = fairness_stats(grid)
        self.assertFalse(s["has_vacations"])
        self.assertEqual(s["per_nurse"][0]["vac_days"], 0)
        self.assertEqual(s["per_nurse"][0]["off_days"], 1)  # unchanged when no leave
