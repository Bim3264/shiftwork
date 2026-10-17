"""Tests for the web layer (routes, auth gate, tenancy, status fragment).

These drive the FastAPI app with `TestClient`. Two dependencies are overridden
so no real Google network round-trip and no Redis are needed:

  * ``require_user`` -> a fake logged-in user (except the one test that verifies
    the unauthenticated redirect, which leaves it in place),
  * ``get_db``       -> sessions from a temp-file SQLite database,
  * ``enqueue_solve`` (module attribute on ``webapp.web.app``) -> a recorder, so
    submitting a solve creates the job row without touching RQ/Redis.

The status-fragment states (succeeded-with-grid, infeasible, failed) are seeded
directly as job rows in those terminal states — no real ~8s solves in web tests.

Run from the project root:
  python3 -m unittest webapp.tests.test_web
"""

import os
import re
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from webapp import config  # noqa: E402
from webapp.core.schedule_input import ScheduleInput  # noqa: E402
from webapp.db.models import (  # noqa: E402
    JobStatus, ScheduleInputRow, SolveJob, User, Ward, WardMember,
)
from webapp.db.session import make_session_factory  # noqa: E402
from webapp.web import app as app_module  # noqa: E402
from webapp.web import auth as auth_module  # noqa: E402
from webapp.web.auth import get_db, require_user  # noqa: E402


_SAMPLE_CSV = """[ward]
ward_name,Ward 4B
hospital,Siriraj Hospital

[settings]
num_days,7
weekends,6 7
allow_evening_to_night,false
allow_night_to_day,true
head_nurse_special_shift,true

[schedule]
Name,Type,1,2,3,4,5,6,7
NurseA,senior,,off,,,,,
NurseB,senior,ช,,,,off,,
NurseC,,,,ด,,,,
"""

# _SAMPLE_CSV with default coverage 5/3/3 weekday is certainly infeasible with
# only 3 nurses (presolve now blocks it). Feasible variant for tests that need
# solve_input to actually proceed: all six coverage keys down to 1 and
# allow_night_to_day true (per spec). head_nurse_special_shift is also turned
# off here: with only 3 nurses, 2 of them senior (NurseA/NurseB), the senior
# weekend-off rule would leave a single nurse to cover 3 required weekend
# shifts alone, which is genuinely infeasible (verified against the real
# solver) regardless of coverage or transition settings.
_FEASIBLE_CSV = _SAMPLE_CSV.replace(
    "head_nurse_special_shift,true\n",
    "head_nurse_special_shift,false\n"
    "coverage_weekday_day,1\n"
    "coverage_weekday_evening,1\n"
    "coverage_weekday_night,1\n"
    "coverage_weekend_day,1\n"
    "coverage_weekend_evening,1\n"
    "coverage_weekend_night,1\n",
)


class WebTestBase(unittest.TestCase):
    """Shared fixture: temp DB, a user in a shared ward, dependency overrides."""

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.Session = make_session_factory(f"sqlite:///{self._db_path}")

        # Seed a user + shared ward + membership.
        with self.Session() as db:
            user = User(google_sub="sub-1", email="tester@example.com", name="Tester")
            db.add(user)
            db.flush()
            ward = Ward(name="Shared Ward", created_by=user.id)
            db.add(ward)
            db.flush()
            db.add(WardMember(ward_id=ward.id, user_id=user.id, role="member"))
            db.commit()
            self.user_id = user.id
            self.ward_id = ward.id
        # A detached copy require_user can return (attrs stay loaded).
        with self.Session() as db:
            self.user = db.get(User, self.user_id)

        # enqueue_solve recorder (no Redis).
        self.enqueued = []
        self._orig_enqueue = app_module.enqueue_solve
        app_module.enqueue_solve = lambda job_id: self.enqueued.append(job_id)

        # Dependency overrides.
        def _override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app_module.app.dependency_overrides[get_db] = _override_db
        app_module.app.dependency_overrides[require_user] = lambda: self.user
        self.client = TestClient(app_module.app, follow_redirects=False)

    def tearDown(self):
        app_module.app.dependency_overrides.clear()
        app_module.enqueue_solve = self._orig_enqueue
        os.unlink(self._db_path)

    # helpers -----------------------------------------------------------------
    def _make_input(self, ward_id=None, csv_text=_SAMPLE_CSV, source="csv_upload"):
        si = ScheduleInput.from_csv(csv_text)
        with self.Session() as db:
            row = ScheduleInputRow(
                ward_id=ward_id or self.ward_id,
                created_by=self.user_id,
                source=source,
                data=si.to_dict(),
                original_csv=csv_text,
            )
            db.add(row)
            db.commit()
            return row.id

    def _make_job(self, *, status, solver_status="", result_grid=None,
                  result_csv="", error="", log="", ward_id=None, input_id=None):
        with self.Session() as db:
            if input_id is None:
                row = ScheduleInputRow(
                    ward_id=ward_id or self.ward_id, created_by=self.user_id,
                    source="grid", data=ScheduleInput.from_csv(_SAMPLE_CSV).to_dict(),
                )
                db.add(row)
                db.flush()
                input_id = row.id
            job = SolveJob(
                input_id=input_id, ward_id=ward_id or self.ward_id,
                submitted_by=self.user_id, status=status,
                max_time_seconds=config.SOLVER_MAX_TIME_SECONDS,
                solver_status=solver_status, result_grid=result_grid,
                result_csv=result_csv, error=error, log=log,
            )
            db.add(job)
            db.commit()
            return job.id


class HealthAndAuthRoutes(WebTestBase):

    def test_healthz_no_auth(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_unauthenticated_root_redirects_to_login(self):
        # Remove the require_user override so the real dependency runs; no session
        # cookie => AuthRedirect => 302 to /login.
        app_module.app.dependency_overrides.pop(require_user, None)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_dashboard_renders_for_logged_in_user(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", r.text)
        self.assertIn("tester@example.com", r.text)

    def test_dashboard_shows_ward_and_hospital(self):
        # _SAMPLE_CSV carries ward "Ward 4B" / "Siriraj Hospital". Both the
        # inputs table (from data) and the jobs table (resolved in the route)
        # should display them.
        input_id = self._make_input()
        self._make_job(status=JobStatus.succeeded, input_id=input_id)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.count("Ward 4B") >= 2, True)  # inputs + jobs rows
        self.assertIn("Siriraj Hospital", r.text)


class MonthDashboardTests(WebTestBase):

    _CSV_WITH_MONTH = _SAMPLE_CSV.replace(
        "hospital,Siriraj Hospital\n",
        "hospital,Siriraj Hospital\nmonth,9\nyear,2026\n",
    )

    def test_dashboard_one_row_per_month(self):
        self._make_input(csv_text=self._CSV_WITH_MONTH)
        self._make_input(csv_text=self._CSV_WITH_MONTH)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.count("กันยายน 2026"), 1)
        self.assertIn('data-versions="2"', r.text)

    def test_dashboard_status_pill_infeasible(self):
        input_id = self._make_input(csv_text=self._CSV_WITH_MONTH)
        self._make_job(status=JobStatus.succeeded, solver_status="INFEASIBLE", input_id=input_id)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ไม่สำเร็จ / Infeasible", r.text)

    def test_dashboard_links_next_month(self):
        input_id = self._make_input(csv_text=self._CSV_WITH_MONTH)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"/inputs/{input_id}/duplicate", r.text)


class InputRoutes(WebTestBase):

    def test_upload_csv_creates_input_and_redirects(self):
        r = self.client.post(
            "/inputs",
            data={"source": "csv_upload"},
            files={"file": ("ward.csv", _SAMPLE_CSV.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/inputs/\d+/edit$")
        # The row really landed in the DB, parsed, in the user's ward.
        with self.Session() as db:
            rows = db.query(ScheduleInputRow).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source, "csv_upload")
            self.assertEqual(rows[0].ward_id, self.ward_id)
            self.assertEqual(rows[0].data["settings"]["num_days"], 7)
            self.assertEqual(len(rows[0].data["nurses"]), 3)

    def test_upload_xlsx_creates_input_and_redirects(self):
        import io, openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Roster"
        ws["A5"]="Ward name / x"; ws["C5"]="XlsxWard"
        ws["A11"]="Plan / x";     ws["C11"]="ward"
        ws["H5"]="Days in month / x"; ws["P5"]=5
        ws["A16"]="Nurse"; ws["B16"]="Type"
        for i, d in enumerate(range(1, 6)):
            ws.cell(row=16, column=3+i, value=d)
        ws["A18"]="Alice"; ws["B18"]="senior"; ws["E18"]="vac"
        ws["A19"]="Bob";   ws["B19"]="new";    ws["D19"]="off"
        buf=io.BytesIO(); wb.save(buf)
        r = self.client.post(
            "/inputs",
            data={"source": "csv_upload"},
            files={"file": ("roster.xlsx", buf.getvalue(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/inputs/\d+/edit$")
        with self.Session() as db:
            rows = db.query(ScheduleInputRow).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source, "xlsx_upload")
            self.assertEqual(rows[0].data["settings"]["num_days"], 5)
            self.assertEqual([n["name"] for n in rows[0].data["nurses"]], ["Alice", "Bob"])
            self.assertEqual(rows[0].data["nurses"][0]["shifts"]["3"], "vac")

    def test_upload_without_file_shows_friendly_error(self):
        r = self.client.post("/inputs", data={"source": "csv_upload"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("choose a CSV", r.text)

    def test_edit_page_prefills_settings(self):
        input_id = self._make_input()
        r = self.client.get(f"/inputs/{input_id}/edit")
        self.assertEqual(r.status_code, 200)
        # num_days prefilled
        self.assertIn('value="7"', r.text)
        # weekends prefilled as "6 7"
        self.assertIn('value="6 7"', r.text)
        # allow_night_to_day was true in the CSV -> checkbox checked;
        # allow_evening_to_night was false -> not checked. Assert both.
        night_to_day = _checkbox_block(r.text, "allow_night_to_day")
        evening_to_night = _checkbox_block(r.text, "allow_evening_to_night")
        self.assertIn("checked", night_to_day)
        self.assertNotIn("checked", evening_to_night)
        # enable_meetings is present but read-only/disabled, never a live field.
        self.assertNotIn('name="enable_meetings"', r.text)
        # Soft-request option is present.
        self.assertIn('name="min_request_percent"', r.text)
        self.assertIn('name="relax_days_off"', r.text)

    def test_autofill_from_month_sets_days_and_weekends(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}/autofill",
            data={"month": "4", "year": "2026", "num_days": "31",
                  "weekends": "", "row_count": "0"},
        )
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            s = db.get(ScheduleInputRow, input_id).data["settings"]
        self.assertEqual(s["num_days"], 30)      # April 2026 has 30 days
        # Holiday band (constraint.md section 22): weekends holds ONLY Sat/Sun and
        # public holidays go to their own list. Assert both directions so a
        # regression that merges them again (old behaviour) or drops the holiday
        # fails here.
        sat_sun = [4, 5, 11, 12, 18, 19, 25, 26]
        self.assertEqual(s["weekends"], sat_sun)
        self.assertNotIn(6, s["weekends"])       # Chakri Memorial Day is NOT a weekend
        self.assertIn(6, s["holidays"])          # ...it is a holiday
        for d in sat_sun:
            self.assertNotIn(d, s["holidays"])

    def test_autofill_requires_valid_month_year(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}/autofill",
            data={"month": "", "year": "", "num_days": "31", "row_count": "0"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("valid month", r.text)

    def test_edit_page_shows_coverage_inputs(self):
        input_id = self._make_input()
        r = self.client.get(f"/inputs/{input_id}/edit")
        for name in ("coverage_weekday_day", "coverage_weekday_night",
                     "coverage_weekend_evening"):
            self.assertIn(f'name="{name}"', r.text)
        self.assertIn('value="5"', r.text)   # default weekday day

    def test_update_saves_coverage_settings(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}",
            data={"num_days": "7", "weekends": "6 7",
                  "head_nurse_special_shift": "on",
                  "coverage_weekday_day": "6", "coverage_weekend_night": "1",
                  "row_count": "0"},
        )
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            s = db.get(ScheduleInputRow, input_id).data["settings"]
        self.assertEqual(s["coverage_weekday_day"], 6)
        self.assertEqual(s["coverage_weekend_night"], 1)

    def test_update_saves_soft_request_settings(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}",
            data={"num_days": "7", "weekends": "6 7",
                  "head_nurse_special_shift": "on",
                  "min_request_percent": "80", "relax_days_off": "on",
                  "row_count": "0"},
        )
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            s = db.get(ScheduleInputRow, input_id).data["settings"]
        self.assertEqual(s["min_request_percent"], 80)
        self.assertTrue(s["relax_days_off"])

    def test_update_input_persists_settings_and_grid(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}",
            data={
                "num_days": "5",
                "weekends": "4 5",
                "head_nurse_special_shift": "on",
                "row_count": "2",
                "name_0": "Alice", "type_0": "senior", "cell_0_1": "off",
                "name_1": "Bob", "type_1": "", "cell_1_2": "ช",
            },
        )
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            data = db.get(ScheduleInputRow, input_id).data
        self.assertEqual(data["settings"]["num_days"], 5)
        self.assertEqual(data["settings"]["weekends"], [4, 5])
        self.assertEqual(len(data["nurses"]), 2)
        self.assertEqual(data["nurses"][0]["shifts"]["1"], "off")

    def test_edit_page_shows_tier_panel(self):
        input_id = self._make_input()  # _SAMPLE_CSV has no tier -> free
        r = self.client.get(f"/inputs/{input_id}/edit")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Constraint options on this tier", r.text)
        self.assertIn("Tier:", r.text)
        self.assertIn("free", r.text)                 # resolved tier
        self.assertIn("Unavailable", r.text)          # meetings gated on free

    _GRID_CSV = (
        "[settings]\nnum_days,7\nweekends,6 7\nhead_nurse_special_shift,true\n"
        "[schedule]\nName,Type,1,2,3,4,5,6,7\n"
        "A,senior,,ช,,,,,\nB,senior,,,,,,,\nC,,,,,,,,off\n"
    )

    def test_edit_grid_cell_names_are_per_nurse_per_day(self):
        # Regression: cell inputs must be named cell_<nurse>_<day>, unique per
        # nurse. The old bug used the day-loop index, so with 3 nurses it emitted
        # cell_6_7 (a 7th row that doesn't exist) and never cell_0_7 / cell_2_7.
        input_id = self._make_input(csv_text=self._GRID_CSV, source="csv_upload")
        r = self.client.get(f"/inputs/{input_id}/edit")
        for good in ('name="cell_0_1"', 'name="cell_0_7"',
                     'name="cell_1_1"', 'name="cell_2_7"',
                     'name="active_0"', 'name="active_2"'):
            self.assertIn(good, r.text, f"missing {good}")
        self.assertNotIn('name="cell_6_7"', r.text)   # buggy day-indexed artifact

    def test_edit_form_round_trip_preserves_data(self):
        # Render the page, resubmit exactly what it rendered, and confirm the
        # shifts survive — the real browser path the direct-POST tests skipped.
        input_id = self._make_input(csv_text=self._GRID_CSV, source="csv_upload")
        rendered = self.client.get(f"/inputs/{input_id}/edit").text
        fields = _extract_form_fields(rendered)
        r = self.client.post(f"/inputs/{input_id}", data=fields)
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            data = db.get(ScheduleInputRow, input_id).data
        by = {n["name"]: n for n in data["nurses"]}
        self.assertEqual(by["A"]["shifts"].get("2"), "ช")   # preserved
        self.assertEqual(by["C"]["shifts"].get("7"), "off")
        # All three still active (checkboxes were rendered checked).
        self.assertTrue(all(by[n]["active"] for n in ("A", "B", "C")))

    def test_save_can_disable_a_nurse_without_deleting(self):
        input_id = self._make_input()  # NurseA, NurseB, NurseC
        r = self.client.post(
            f"/inputs/{input_id}",
            data={
                "num_days": "7", "weekends": "6 7",
                "head_nurse_special_shift": "on",
                "row_count": "3",
                "name_0": "NurseA", "type_0": "senior", "active_0": "on",
                "name_1": "NurseB", "type_1": "senior", "active_1": "on",
                # NurseC: name kept, but active checkbox omitted => disabled.
                "name_2": "NurseC", "type_2": "",
            },
        )
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            nurses = db.get(ScheduleInputRow, input_id).data["nurses"]
        by_name = {n["name"]: n for n in nurses}
        self.assertTrue(by_name["NurseA"]["active"])
        self.assertIn("NurseC", by_name)                 # not deleted
        self.assertFalse(by_name["NurseC"]["active"])     # just disabled
        # The tier panel's nurse count reflects active nurses (2 of 3 now).
        panel = self.client.get(f"/inputs/{input_id}/edit").text
        self.assertIn("Nurses: 2 /", panel)

    def test_edit_page_ward_scoped_404_for_other_ward(self):
        # Input in a ward the user is NOT a member of.
        with self.Session() as db:
            other = Ward(name="Other Ward", created_by=self.user_id)
            db.add(other)
            db.commit()
            other_id = other.id
        foreign_input = self._make_input(ward_id=other_id)
        r = self.client.get(f"/inputs/{foreign_input}/edit")
        self.assertEqual(r.status_code, 404)


class DuplicateMonthTests(WebTestBase):

    def test_duplicate_creates_next_month_and_redirects(self):
        input_id = self._make_input(csv_text=(
            "[ward]\nmonth,9\nyear,2026\n\n"
            "[settings]\nnum_days,7\nweekends,6 7\n\n"
            "[schedule]\nName,Type,1,2,3,4,5,6,7\nA,senior,ช,,,,,,\n"
        ))
        with self.Session() as db:
            before = db.get(ScheduleInputRow, input_id).data
        r = self.client.post(f"/inputs/{input_id}/duplicate")
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/inputs/\d+/edit$")
        new_id = int(r.headers["location"].split("/")[2])
        self.assertNotEqual(new_id, input_id)
        with self.Session() as db:
            rows = db.query(ScheduleInputRow).all()
            self.assertEqual(len(rows), 2)
            new_row = db.get(ScheduleInputRow, new_id)
            self.assertEqual(new_row.source, "duplicate")
            self.assertEqual(new_row.data["ward_meta"]["month"], "10")
            self.assertEqual(new_row.data["ward_meta"]["year"], "2026")
            # Source row untouched.
            self.assertEqual(db.get(ScheduleInputRow, input_id).data, before)

    def test_duplicate_other_ward_404(self):
        with self.Session() as db:
            other = Ward(name="Other Ward", created_by=self.user_id)
            db.add(other)
            db.commit()
            other_id = other.id
        foreign_input = self._make_input(ward_id=other_id)
        r = self.client.post(f"/inputs/{foreign_input}/duplicate")
        self.assertEqual(r.status_code, 404)
        with self.Session() as db:
            self.assertEqual(db.query(ScheduleInputRow).count(), 1)

    def test_edit_shows_next_month_button(self):
        input_id = self._make_input()
        r = self.client.get(f"/inputs/{input_id}/edit")
        self.assertIn(f"/inputs/{input_id}/duplicate", r.text)


class SolveAndJobRoutes(WebTestBase):

    def test_solve_creates_job_and_redirects_to_job_page(self):
        input_id = self._make_input(csv_text=_FEASIBLE_CSV)
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/jobs/\d+$")
        with self.Session() as db:
            jobs = db.query(SolveJob).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, JobStatus.queued)
        self.assertEqual(jobs[0].max_time_seconds, config.SOLVER_MAX_TIME_SECONDS)
        # enqueue_solve was called with the new job id (no Redis touched).
        self.assertEqual(self.enqueued, [jobs[0].id])
        self.assertEqual(r.headers["location"], f"/jobs/{jobs[0].id}")

    def test_solve_invalid_input_reports_friendly_error(self):
        # No nurse rows -> validate() raises; expect 400, no job.
        bad_csv = (
            "[settings]\nnum_days,7\nhead_nurse_special_shift,true\n"
            "[schedule]\nName,Type,1,2,3,4,5,6,7\n"
        )
        input_id = self._make_input(csv_text=bad_csv, source="grid")
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 400)
        self.assertIn("active nurse is required", r.text)
        with self.Session() as db:
            self.assertEqual(db.query(SolveJob).count(), 0)

    def test_job_page_renders_and_polls(self):
        job_id = self._make_job(status=JobStatus.queued)
        r = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"/jobs/{job_id}/status", r.text)  # HTMX poll target

    def test_status_fragment_succeeded_with_grid(self):
        grid = {"days": [1, 2], "rows": [
            {"nurse_index": 0, "name": "NurseA", "cells": ["ช", "off"]},
            {"nurse_index": 1, "name": "NurseB", "cells": ["off", "ด"]},
        ]}
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="OPTIMAL",
            result_grid=grid, result_csv="idx,1,2\n0,ช,off\n",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Schedule (OPTIMAL)", r.text)
        self.assertIn("NurseA", r.text)
        self.assertIn(f"/jobs/{job_id}/download", r.text)
        # Terminal state => no further polling attribute.
        self.assertNotIn("every 2s", r.text)

    def test_status_fragment_shows_fairness_stats(self):
        # 4 nurses: 0/1 are day-only seniors (excluded from the range by the
        # default head_nurse_special_shift rule); 2/3 differ in night load.
        grid = {"days": [1, 2], "rows": [
            {"nurse_index": 0, "name": "Head",   "cells": ["ช", "ช"]},
            {"nurse_index": 1, "name": "Deputy", "cells": ["ช", "ช"]},
            {"nurse_index": 2, "name": "NurseC", "cells": ["ด", "ด"]},
            {"nurse_index": 3, "name": "NurseD", "cells": ["ด", "off"]},
        ]}
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="OPTIMAL",
            result_grid=grid, result_csv="idx,1,2\n",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Fairness", r.text)
        self.assertIn("ช Day", r.text)                 # per-nurse table header
        self.assertIn("· senior", r.text)              # seniors tagged in table
        self.assertIn("excludes 2 day-only seniors", r.text)  # spread caption

    def test_fairness_marks_the_typed_senior_not_rows_0_and_1(self):
        # Seniority is the Type column: only the nurse at index 2 is senior.
        csv_text = (
            "[settings]\nnum_days,2\nhead_nurse_special_shift,true\n"
            "[schedule]\nName,Type,1,2\nA,,,\nB,,,\nC,senior,,\nD,,,\n"
        )
        input_id = self._make_input(csv_text=csv_text, source="grid")
        grid = {"days": [1, 2], "rows": [
            {"nurse_index": 0, "name": "A", "cells": ["ด", "ด"]},
            {"nurse_index": 1, "name": "B", "cells": ["ด", "off"]},
            {"nurse_index": 2, "name": "C", "cells": ["ช", "ช"]},
            {"nurse_index": 3, "name": "D", "cells": ["ด", "ด"]},
        ]}
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="OPTIMAL",
            result_grid=grid, result_csv="x", input_id=input_id,
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("excludes 1 day-only senior", r.text)
        self.assertEqual(r.text.count("· senior"), 1)

    def test_status_fragment_shows_request_acceptance_report(self):
        grid = {
            "days": [1, 2],
            "rows": [{"nurse_index": 0, "name": "A", "cells": ["ช", "off"]}],
            "request_report": {
                "total": 4, "accepted": 2, "percent": 50.0,
                "dropped": [
                    {"name": "N1", "day": 1, "shift": "off", "kind": "holiday"},
                    {"name": "N2", "day": 1, "shift": "off", "kind": "holiday"},
                ],
            },
        }
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="OPTIMAL",
            result_grid=grid, result_csv="x",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Accepted", r.text)
        self.assertIn("2/4", r.text)
        self.assertIn("50", r.text)
        self.assertIn("N1", r.text)          # a dropped request is listed

    def test_status_fragment_infeasible(self):
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="INFEASIBLE",
            result_grid=None, log="no solution",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No feasible schedule", r.text)
        self.assertIn("proved", r.text)            # distinguishes from a timeout
        self.assertNotIn("ran out of time", r.text)
        self.assertNotIn("every 2s", r.text)

    def test_status_fragment_timeout_is_not_called_infeasible(self):
        # UNKNOWN = ran out of time, NOT a proven contradiction. Must be shown
        # differently from INFEASIBLE so the user knows to raise the time limit.
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="UNKNOWN",
            result_grid=None,
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ran out of time", r.text)
        self.assertNotIn("No feasible schedule", r.text)

    def test_status_fragment_failed(self):
        job_id = self._make_job(
            status=JobStatus.failed, error="boom happened", log="trace",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Solve failed", r.text)
        self.assertIn("boom happened", r.text)

    def test_status_fragment_running_keeps_polling(self):
        job_id = self._make_job(status=JobStatus.running)
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Running", r.text)
        self.assertIn("every 2s", r.text)  # still polling

    def test_download_returns_csv(self):
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="OPTIMAL",
            result_grid={"days": [1], "rows": []}, result_csv="idx,1\n0,ช\n",
        )
        r = self.client.get(f"/jobs/{job_id}/download")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("attachment", r.headers["content-disposition"])
        # Must start with the UTF-8 BOM so Excel renders Thai correctly...
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"),
                        "download must start with a UTF-8 BOM")
        # ...and the Thai symbol must survive the round-trip (exactly one BOM).
        decoded = r.content.decode("utf-8-sig")
        self.assertIn("ช", decoded)
        self.assertFalse(decoded.startswith("﻿"), "should be exactly one BOM")

    def test_download_xlsx_roundtrip(self):
        import io, openpyxl
        grid = {"days": [1, 2], "rows": [
            {"nurse_index": 0, "name": "NurseA", "cells": ["ช", "off"]}]}
        job_id = self._make_job(status=JobStatus.succeeded, solver_status="OPTIMAL",
                                result_grid=grid, result_csv="x")
        r = self.client.get(f"/jobs/{job_id}/download.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r.headers["content-type"])
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        vals = [c.value for row in ws.iter_rows() for c in row]
        self.assertIn("NurseA", vals)
        self.assertIn("ช", vals)
        page = self.client.get(f"/jobs/{job_id}/status")
        self.assertIn(f"/jobs/{job_id}/download.xlsx", page.text)

    def test_download_xlsx_404_without_grid(self):
        job_id = self._make_job(status=JobStatus.failed)
        self.assertEqual(self.client.get(f"/jobs/{job_id}/download.xlsx").status_code, 404)

    def test_download_404_when_no_csv(self):
        job_id = self._make_job(status=JobStatus.succeeded, result_csv="")
        r = self.client.get(f"/jobs/{job_id}/download")
        self.assertEqual(r.status_code, 404)

    def test_job_ward_scoped_404_for_other_ward(self):
        with self.Session() as db:
            other = Ward(name="Other Ward", created_by=self.user_id)
            db.add(other)
            db.commit()
            other_id = other.id
        job_id = self._make_job(status=JobStatus.queued, ward_id=other_id)
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 404)


class PresolveRouteTests(WebTestBase):

    def test_solve_is_not_blocked_by_presolve_by_default(self):
        # _SAMPLE_CSV: 3 nurses vs default coverage 5/3/3 -> presolve flags it.
        # PRESOLVE_BLOCKS_SOLVE defaults off, so the job is queued anyway and
        # the ward gets an INFEASIBLE result explained by diagnose(), rather
        # than being refused up front.
        self.assertFalse(app_module.config.PRESOLVE_BLOCKS_SOLVE)
        input_id = self._make_input()
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 303)
        with self.Session() as db:
            self.assertEqual(db.query(SolveJob).count(), 1)
        self.assertEqual(len(self.enqueued), 1)

    def test_solve_blocked_by_presolve_error_when_enabled(self):
        orig = app_module.config.PRESOLVE_BLOCKS_SOLVE
        app_module.config.PRESOLVE_BLOCKS_SOLVE = True
        self.addCleanup(
            setattr, app_module.config, "PRESOLVE_BLOCKS_SOLVE", orig)
        input_id = self._make_input()
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ready to solve", r.text)
        self.assertIn("Day 1", r.text)
        with self.Session() as db:
            self.assertEqual(db.query(SolveJob).count(), 0)
        self.assertEqual(self.enqueued, [])

    def test_check_route_saves_and_renders_checklist(self):
        input_id = self._make_input()
        r = self.client.post(
            f"/inputs/{input_id}/check",
            data={
                "num_days": "7", "weekends": "6 7",
                "allow_night_to_day": "on",
                "head_nurse_special_shift": "on",
                "row_count": "3",
                "name_0": "NurseA", "type_0": "senior", "active_0": "on", "cell_0_2": "off",
                "name_1": "NurseB", "type_1": "senior", "active_1": "on",
                "cell_1_1": "ช", "cell_1_5": "off",
                "name_2": "NurseC", "type_2": "", "active_2": "on", "cell_2_3": "ด",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("checks passed", r.text)
        with self.Session() as db:
            data = db.get(ScheduleInputRow, input_id).data
        self.assertEqual(data["settings"]["num_days"], 7)
        self.assertEqual(len(data["nurses"]), 3)

    def test_solve_with_warnings_only_proceeds(self):
        # tier free (no tier declared), one nurse with 3 offs (over the free
        # plan's 2-day quota -> quota_off warning, not hard), coverage 1/1/1,
        # 4 nurses -> no errors, solve proceeds.
        feasible_csv = """[settings]
num_days,5
weekends,

coverage_weekday_day,1
coverage_weekday_evening,1
coverage_weekday_night,1
coverage_weekend_day,1
coverage_weekend_evening,1
coverage_weekend_night,1

[schedule]
Name,Type,1,2,3,4,5
Nurse1,,off,off,off,,
Nurse2,,,,,,
Nurse3,,,,,,
Nurse4,,,,,,
"""
        input_id = self._make_input(csv_text=feasible_csv, source="grid")
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/jobs/\d+$")
        with self.Session() as db:
            self.assertEqual(db.query(SolveJob).count(), 1)


class ResultGridRenderTests(WebTestBase):
    """The results page renders both the legacy flat grid and the grouped grid."""

    _VAC_TINT = "background:#CFFAFE"
    _OFF_TINT = "background:#FEE2E2"
    _OFFWARD_TINT = "background:#FFEDD5"

    def _cell(self, html, text):
        m = re.search(r'<td class="cell"([^>]*)>' + re.escape(text) + r"</td>", html)
        self.assertIsNotNone(m, f"cell {text!r} not rendered")
        return m.group(1)

    def test_old_flat_grid_still_renders_with_fairness(self):
        # Saved before the staff-class feature: no "groups" key at all.
        grid = {"days": [1, 2, 3], "rows": [
            {"nurse_index": 0, "name": "NurseA", "cells": ["ช", "off", "vac"]},
            {"nurse_index": 1, "name": "NurseB", "cells": ["บ", "ด", "off"]},
        ]}
        self.assertNotIn("groups", grid)
        jid = self._make_job(status=JobStatus.succeeded, solver_status="OPTIMAL",
                             result_grid=grid)
        r = self.client.get(f"/jobs/{jid}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Fairness", r.text)
        self.assertIn("NurseA", r.text)
        self.assertIn("NurseB", r.text)
        self.assertNotIn("<h3>RN", r.text)          # no per-group heading
        self.assertNotIn("PN/NA", r.text)
        self.assertNotIn("Off-ward", r.text)         # no off-ward column or note
        self.assertNotIn(self._OFFWARD_TINT, r.text)
        self.assertIn(self._VAC_TINT, self._cell(r.text, "vac"))
        self.assertIn(self._OFF_TINT, self._cell(r.text, "off"))
        # the full job page renders too
        self.assertEqual(self.client.get(f"/jobs/{jid}").status_code, 200)

    def test_grouped_grid_renders_a_table_per_group(self):
        grid = {"days": [1, 2], "rows": [
            {"nurse_index": 0, "name": "RN1", "cells": ["ช", "flt:ด"], "group": "rn"},
            {"nurse_index": 1, "name": "PN1", "cells": ["ref:ช", "off"], "group": "pn_na"},
        ], "groups": [
            {"key": "rn", "label": "RN", "nurse_indices": [0],
             "solver_status": "OPTIMAL", "request_report": None},
            {"key": "pn_na", "label": "PN/NA", "nurse_indices": [1],
             "solver_status": "FEASIBLE", "request_report": None},
        ]}
        jid = self._make_job(status=JobStatus.succeeded, solver_status="OPTIMAL",
                             result_grid=grid)
        r = self.client.get(f"/jobs/{jid}/status")
        self.assertEqual(r.status_code, 200)
        t = r.text
        self.assertIn("<h3>RN", t)
        self.assertIn("<h3>PN/NA", t)
        # A table per group: each nurse sits under her own group's heading.
        rn_head, pn_head = t.index("<h3>RN"), t.index("<h3>PN/NA")
        self.assertLess(rn_head, pn_head)
        self.assertGreater(t.index("RN1"), rn_head)
        self.assertLess(t.rindex("RN1"), pn_head)
        self.assertGreater(t.index("PN1"), pn_head)
        self.assertEqual(t.count("Fairness"), 2)     # one fairness block per group
        # Off-ward summary is shown because this grid has off-ward cells.
        self.assertIn("Off-ward", t)

    def test_offward_cell_is_tinted_distinctly_from_off_and_vac(self):
        grid = {"days": [1, 2, 3, 4], "rows": [
            {"nurse_index": 0, "name": "A",
             "cells": ["flt:ด", "ref:ช", "off", "vac"]},
        ]}
        jid = self._make_job(status=JobStatus.succeeded, solver_status="OPTIMAL",
                             result_grid=grid)
        t = self.client.get(f"/jobs/{jid}/status").text
        flt, ref = self._cell(t, "flt:ด"), self._cell(t, "ref:ช")
        off, vac = self._cell(t, "off"), self._cell(t, "vac")
        self.assertIn(self._OFFWARD_TINT, flt)
        self.assertIn(self._OFFWARD_TINT, ref)
        self.assertIn(self._OFF_TINT, off)
        self.assertIn(self._VAC_TINT, vac)
        # distinct: the off-ward tint is on neither of off / vac, and vice versa
        for other in (off, vac):
            self.assertNotIn(self._OFFWARD_TINT, other)
        self.assertNotIn(self._OFF_TINT, flt)
        self.assertNotIn(self._VAC_TINT, flt)


class NegativeRequestEditTests(WebTestBase):
    def test_edit_page_documents_and_tints_no_tokens(self):
        r = self.client.get(f"/inputs/{self._make_input()}/edit")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no:บ", r.text)               # legend
        self.assertIn("cell--neg", r.text)               # CSS class + JS tint


class CoverageBandsEditTests(WebTestBase):
    """Edit page: three coverage bands, the holidays input, no-clobber saves."""

    @staticmethod
    def _form(**extra):
        form = {"row_count": "3", "num_days": "7", "weekends": "6 7",
                "name_0": "NurseA", "name_1": "NurseB", "name_2": "NurseC",
                "active_0": "on", "active_1": "on", "active_2": "on"}
        form.update(extra)
        return form

    def _settings(self, iid):
        with self.Session() as db:
            return db.get(ScheduleInputRow, iid).data["settings"]

    def test_edit_page_renders_three_bands(self):
        iid = self._make_input()
        r = self.client.get(f"/inputs/{iid}/edit")
        self.assertEqual(r.status_code, 200)
        # Unclassed ward: 5 bands x 3 shifts, legacy keys only.
        self.assertEqual(len(re.findall(r'name="coverage_', r.text)), 15)
        self.assertNotIn("coverage_pnna", r.text)
        self.assertIn('name="coverage_holiday_day"', r.text)
        self.assertIn('name="coverage_holiday_evening"', r.text)
        self.assertIn('name="coverage_holiday_night"', r.text)
        self.assertIn('name="holidays"', r.text)

        # Classify every nurse -> 5 bands x 3 shifts x 2 groups.
        form = self._form(class_0="rn", class_1="RN", class_2="na",
                          coverage_weekday_day="9")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        r = self.client.get(f"/inputs/{iid}/edit")
        self.assertEqual(len(re.findall(r'name="coverage_', r.text)), 30)
        self.assertNotIn('name="coverage_weekday_day"', r.text)
        self.assertIn('name="coverage_rn_holiday_day"', r.text)
        self.assertIn('name="coverage_pnna_holiday_night"', r.text)
        self.assertIn('name="holidays"', r.text)

    def _coverage_table(self, html):
        m = re.search(r'<table class="opts cov" id="coverage-table">(.*?)</table>',
                      html, re.S)
        self.assertIsNotNone(m, "coverage table missing")
        return m.group(1)

    def test_coverage_table_stays_three_numeric_columns(self):
        # Layout regression: the table once had one column per band/group (up to
        # 7, 527px) and overflowed its 399px grid column, hiding the PN/NA
        # inputs. Shifts are the ONLY numeric columns; bands/groups go down the
        # rows. A future band must add a row, never a column.
        iid = self._make_input()
        t = self._coverage_table(self.client.get(f"/inputs/{iid}/edit").text)
        for tr in re.findall(r"<tr.*?</tr>", t, re.S):
            self.assertLessEqual(
                len(re.findall(r"<t[dh]\b", tr)), 4,
                "coverage table row has more than label + 3 shift cells")
        # Ungrouped: header + 5 band rows, 15 inputs, no group sub-headers.
        self.assertEqual(len(re.findall(r'<input type="number"', t)), 15)
        self.assertEqual(t.count("cov-group"), 0)
        form = self._form(class_0="rn", class_1="RN", class_2="na")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        t = self._coverage_table(self.client.get(f"/inputs/{iid}/edit").text)
        for tr in re.findall(r"<tr.*?</tr>", t, re.S):
            self.assertLessEqual(len(re.findall(r"<t[dh]\b", tr)), 4)
        self.assertEqual(len(re.findall(r'<input type="number"', t)), 30)
        self.assertEqual(t.count("cov-group"), 2)     # RN and PN/NA sub-headers
        self.assertEqual(len(re.findall(r"<tr", t)), 1 + 2 * (1 + 5))

    def test_edit_page_has_saturday_sunday_rows_and_lists(self):
        iid = self._make_input()
        t = self.client.get(f"/inputs/{iid}/edit").text
        for n in ("coverage_saturday_day", "coverage_saturday_night",
                  "coverage_sunday_day", "coverage_sunday_evening",
                  "saturdays", "sundays"):
            self.assertIn(f'name="{n}"', t)

    def test_saturday_sunday_save_and_no_clobber(self):
        iid = self._make_input()
        form = self._form(saturdays="6", sundays="7", coverage_saturday_day="9")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        st = self._settings(iid)
        self.assertEqual((st["saturdays"], st["sundays"]), ([6], [7]))
        self.assertEqual(st["coverage_saturday_day"], 9)
        # A save that omits the keys must not reset them.
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=self._form()).status_code, 303)
        st = self._settings(iid)
        self.assertEqual((st["saturdays"], st["sundays"]), ([6], [7]))
        self.assertEqual(st["coverage_saturday_day"], 9)

    def test_autofill_splits_saturdays_and_sundays(self):
        iid = self._make_input()
        r = self.client.post(f"/inputs/{iid}/autofill",
                             data={"month": "4", "year": "2026", "num_days": "31",
                                   "weekends": "", "row_count": "0"})
        self.assertEqual(r.status_code, 303)
        st = self._settings(iid)
        self.assertEqual(st["saturdays"], [4, 11, 18, 25])
        self.assertEqual(st["sundays"], [5, 12, 19, 26])
        self.assertEqual(st["weekends"], [4, 5, 11, 12, 18, 19, 25, 26])

    def test_save_omitting_group_coverage_does_not_clobber(self):
        iid = self._make_input()
        classes = dict(class_0="rn", class_1="rn", class_2="na")
        form = self._form(**classes, coverage_pnna_weekday_day="6",
                          coverage_pnna_holiday_night="5",
                          coverage_rn_weekday_night="7")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        st = self._settings(iid)
        self.assertEqual(st["coverage_pnna_weekday_day"], 6)
        self.assertEqual(st["coverage_pnna_holiday_night"], 5)

        # Second save omits every PN/NA coverage field (and the legacy ones).
        form2 = self._form(**classes, coverage_rn_weekday_night="8")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form2).status_code, 303)
        st = self._settings(iid)
        self.assertEqual(st["coverage_pnna_weekday_day"], 6)     # not reset to 5
        self.assertEqual(st["coverage_pnna_holiday_night"], 5)   # not reset to 2
        self.assertEqual(st["coverage_rn_weekday_night"], 8)     # submitted -> updated

    def test_min_request_percent_is_per_group_in_form(self):
        iid = self._make_input()
        # Unclassed: one legacy field.
        r = self.client.get(f"/inputs/{iid}/edit")
        self.assertIn('name="min_request_percent"', r.text)
        self.assertNotIn('name="min_request_percent_rn"', r.text)
        # Classed: the pair replaces it.
        classes = dict(class_0="rn", class_1="rn", class_2="na")
        form = self._form(**classes, min_request_percent_rn="100",
                          min_request_percent_pnna="40")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        st = self._settings(iid)
        self.assertEqual((st["min_request_percent_rn"], st["min_request_percent_pnna"]),
                         (100, 40))
        r = self.client.get(f"/inputs/{iid}/edit")
        self.assertIn('name="min_request_percent_rn"', r.text)
        self.assertIn('name="min_request_percent_pnna"', r.text)
        self.assertNotIn('name="min_request_percent"', r.text)

    def test_save_omitting_min_request_keys_does_not_clobber(self):
        iid = self._make_input()
        classes = dict(class_0="rn", class_1="rn", class_2="na")
        form = self._form(**classes, min_request_percent_rn="70",
                          min_request_percent_pnna="30")
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=form).status_code, 303)
        # Second save omits all three keys (field not rendered): nothing resets.
        self.assertEqual(self.client.post(f"/inputs/{iid}",
                                          data=self._form(**classes)).status_code, 303)
        st = self._settings(iid)
        self.assertEqual((st["min_request_percent_rn"], st["min_request_percent_pnna"]),
                         (70, 30))

    def test_save_omitting_holidays_leaves_stored_list_intact(self):
        iid = self._make_input()
        self.assertEqual(self.client.post(
            f"/inputs/{iid}", data=self._form(holidays="3 5")).status_code, 303)
        self.assertEqual(self._settings(iid)["holidays"], [3, 5])
        # The field is absent entirely (not blank).
        self.assertEqual(self.client.post(f"/inputs/{iid}", data=self._form()).status_code, 303)
        self.assertEqual(self._settings(iid)["holidays"], [3, 5])
        # A submitted blank is a deliberate clear: empty means empty.
        self.assertEqual(self.client.post(
            f"/inputs/{iid}", data=self._form(holidays="")).status_code, 303)
        self.assertEqual(self._settings(iid)["holidays"], [])

    def test_settings_from_form_without_current_omits_coverage_keys(self):
        s = app_module._settings_from_form({"num_days": "7"})
        self.assertEqual([k for k in s if k.startswith("coverage") or k == "holidays"], [])


class AllowlistUnit(unittest.TestCase):
    """Pure unit tests for the allowlist gate."""

    def setUp(self):
        self._emails = config.ALLOWED_EMAILS
        self._domain = config.ALLOWED_DOMAIN

    def tearDown(self):
        config.ALLOWED_EMAILS = self._emails
        config.ALLOWED_DOMAIN = self._domain

    def test_dev_mode_allows_anyone(self):
        config.ALLOWED_EMAILS = set()
        config.ALLOWED_DOMAIN = ""
        self.assertTrue(auth_module.is_email_allowed("anyone@gmail.com"))

    def test_email_allowlist_in_and_out(self):
        config.ALLOWED_EMAILS = {"ok@example.com"}
        config.ALLOWED_DOMAIN = ""
        self.assertTrue(auth_module.is_email_allowed("OK@example.com"))  # case-insensitive
        self.assertFalse(auth_module.is_email_allowed("nope@example.com"))

    def test_domain_allowlist_in_and_out(self):
        config.ALLOWED_EMAILS = set()
        config.ALLOWED_DOMAIN = "hospital.org"
        self.assertTrue(auth_module.is_email_allowed("dr@hospital.org"))
        self.assertFalse(auth_module.is_email_allowed("dr@other.org"))

    def test_empty_email_rejected_when_gated(self):
        config.ALLOWED_EMAILS = {"ok@example.com"}
        config.ALLOWED_DOMAIN = ""
        self.assertFalse(auth_module.is_email_allowed(""))


def _extract_form_fields(html: str) -> dict:
    """Parse <input> fields from rendered HTML into a form-data dict, the way a
    browser would submit them: text/number/hidden by value, checkboxes only when
    checked, and never disabled/nameless inputs."""
    import re
    fields: dict = {}
    for tag in re.findall(r"<input\b[^>]*>", html):
        if "disabled" in tag:
            continue
        m = re.search(r'name="([^"]*)"', tag)
        if not m:
            continue
        name = m.group(1)
        itype = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1] \
            if re.search(r'type="([^"]*)"', tag) else "text"
        if itype == "checkbox":
            if re.search(r"\bchecked\b", tag):
                fields[name] = "on"
        else:
            v = re.search(r'value="([^"]*)"', tag)
            fields[name] = v.group(1) if v else ""
    return fields


def _checkbox_block(html: str, name: str) -> str:
    """Return the <input ...> tag text for the checkbox named `name`."""
    marker = f'name="{name}"'
    idx = html.find(marker)
    if idx == -1:
        return ""
    start = html.rfind("<input", 0, idx)
    end = html.find(">", idx)
    return html[start:end + 1]


class DevAuthBypassTest(unittest.TestCase):
    """The DEV_AUTH_BYPASS escape hatch: on => no login required; off => the
    gate still redirects unauthenticated users to /login."""

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.Session = make_session_factory(f"sqlite:///{self._db_path}")

        def _override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        # Only get_db is overridden; require_user runs for real so the bypass
        # branch is genuinely exercised.
        app_module.app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app_module.app, follow_redirects=False)
        self._orig_bypass = config.DEV_AUTH_BYPASS
        self._orig_auth_enabled = config.AUTH_ENABLED

    def tearDown(self):
        config.DEV_AUTH_BYPASS = self._orig_bypass
        config.AUTH_ENABLED = self._orig_auth_enabled
        app_module.app.dependency_overrides.clear()
        os.unlink(self._db_path)

    def test_gate_blocks_when_bypass_off(self):
        config.DEV_AUTH_BYPASS = False
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_bypass_grants_access_without_login(self):
        config.DEV_AUTH_BYPASS = True
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", r.text)
        # A local dev user was auto-provisioned into a shared ward.
        with self.Session() as db:
            user = db.execute(
                select(User).where(User.email == config.DEV_USER_EMAIL)
            ).scalars().first()
            self.assertIsNotNone(user)

    def test_auth_disabled_flag_grants_access(self):
        # AUTH_ENABLED=false turns OAuth off (independent of the dev bypass).
        config.DEV_AUTH_BYPASS = False
        config.AUTH_ENABLED = False
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", r.text)

    def test_login_redirects_home_when_auth_off(self):
        config.DEV_AUTH_BYPASS = False
        config.AUTH_ENABLED = False
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
