"""Tests for webapp.core.months (month grouping helpers).

Run from the project root:
  python3 -m unittest webapp.tests.test_months
"""

import os
import sys
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.months import (  # noqa: E402
    EN_MONTHS, MonthRow, STATE_LABELS, STATE_PILL, THAI_MONTHS,
    job_state, month_rows, parse_month_year,
)


class ParseMonthYearTests(unittest.TestCase):

    def test_valid_and_zero_padded(self):
        self.assertEqual(parse_month_year({"month": "9", "year": "2026"}), (2026, 9))
        self.assertEqual(parse_month_year({"month": "09", "year": " 2026 "}), (2026, 9))
        self.assertEqual(parse_month_year({"month": 12, "year": 2026}), (2026, 12))

    def test_invalid_month_or_year_is_none(self):
        self.assertIsNone(parse_month_year({"month": "13", "year": "2026"}))
        self.assertIsNone(parse_month_year({"month": "0", "year": "2026"}))
        self.assertIsNone(parse_month_year({"month": "9", "year": "0"}))
        self.assertIsNone(parse_month_year({"month": "Sep", "year": "2026"}))

    def test_missing_is_none(self):
        self.assertIsNone(parse_month_year({}))
        self.assertIsNone(parse_month_year(None))
        self.assertIsNone(parse_month_year({"month": "9"}))

    def test_month_name_tables(self):
        self.assertEqual(len(THAI_MONTHS), 13)
        self.assertEqual(THAI_MONTHS[9], "กันยายน")
        self.assertEqual(EN_MONTHS[9], "September")



def _inp(id, *, ward_name="4B", month="9", year="2026", nurses=None, created_at=None):
    return SimpleNamespace(
        id=id,
        created_at=created_at or datetime(2026, 1, 1) + timedelta(seconds=id),
        data={
            "ward_meta": {"ward_name": ward_name, "month": month, "year": year, "hospital": "H"},
            "nurses": nurses if nurses is not None else [{"name": "A"}, {"name": "B"}],
        },
    )


def _job(id, *, status="succeeded", solver_status="OPTIMAL", result_grid=None):
    return SimpleNamespace(id=id, status=status, solver_status=solver_status, result_grid=result_grid)


class MonthRowsTests(unittest.TestCase):

    def test_groups_versions_of_same_month(self):
        i1 = _inp(1, month="9", year="2026")
        i2 = _inp(2, month="9", year="2026")
        i3 = _inp(3, month="8", year="2026")
        rows = month_rows([i1, i2, i3], {})
        self.assertEqual(len(rows), 2)
        sep = next(r for r in rows if r.month == 9)
        self.assertEqual(sep.input_id, 2)
        self.assertEqual(sep.versions, 2)
        self.assertEqual(rows[0].month, 9)  # Sep sorted before Aug

    def test_ward_name_case_insensitive_grouping(self):
        i1 = _inp(1, ward_name="Ward 4B")
        i2 = _inp(2, ward_name="ward 4b ")
        rows = month_rows([i1, i2], {})
        self.assertEqual(len(rows), 1)

    def test_undated_inputs_never_merge(self):
        i1 = _inp(1, month="", year="")
        i2 = _inp(2, month="", year="")
        i3 = _inp(3, month="9", year="2026")
        rows = month_rows([i1, i2, i3], {})
        self.assertEqual(len(rows), 3)
        undated = [r for r in rows if r.year is None]
        self.assertEqual(len(undated), 2)
        dated = [r for r in rows if r.year is not None]
        self.assertEqual(len(dated), 1)
        # Undated rows come after dated rows.
        self.assertTrue(all(r.year is not None for r in rows[:len(dated)]))
        for r in undated:
            self.assertIn("No month set", r.label)

    def test_invalid_month_is_undated(self):
        i1 = _inp(1, month="13", year="2026")
        rows = month_rows([i1], {})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].year)

    def test_job_state_mapping(self):
        self.assertEqual(job_state(None), "draft")
        self.assertEqual(job_state(_job(1, status="queued")), "running")
        self.assertEqual(job_state(_job(1, status="running")), "running")
        self.assertEqual(job_state(_job(1, status="failed")), "error")
        self.assertEqual(job_state(_job(1, status="succeeded", solver_status="OPTIMAL")), "done")
        self.assertEqual(job_state(_job(1, status="succeeded", solver_status="FEASIBLE")), "done")
        self.assertEqual(job_state(_job(1, status="succeeded", solver_status="INFEASIBLE")), "infeasible")
        self.assertEqual(job_state(_job(1, status="succeeded", solver_status="UNKNOWN")), "timeout")

    def test_requests_from_report(self):
        i1 = _inp(1)
        job_with_report = _job(1, result_grid={"request_report": {"total": 45, "accepted": 42}})
        rows = month_rows([i1], {1: job_with_report})
        self.assertEqual(rows[0].requests, "42/45")

        job_without_report = _job(2, result_grid=None)
        rows2 = month_rows([_inp(2)], {2: job_without_report})
        self.assertIsNone(rows2[0].requests)

    def test_status_uses_latest_input_only(self):
        older = _inp(1, created_at=datetime(2026, 1, 1))
        newer = _inp(2, created_at=datetime(2026, 1, 2))
        done_job = _job(10, status="succeeded", solver_status="OPTIMAL")
        rows = month_rows([older, newer], {1: done_job})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].input_id, 2)
        self.assertEqual(rows[0].status, "draft")

    def test_nurse_count_counts_active_only(self):
        i1 = _inp(1, nurses=[
            {"name": "A", "active": True},
            {"name": "B", "active": False},
            {"name": "C"},
        ])
        rows = month_rows([i1], {})
        self.assertEqual(rows[0].nurse_count, 2)


if __name__ == "__main__":
    unittest.main()
