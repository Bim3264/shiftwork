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


class SolveAndJobRoutes(WebTestBase):

    def test_solve_creates_job_and_redirects_to_job_page(self):
        input_id = self._make_input()
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
        # Single nurse + head-nurse rule -> validate() raises; expect 400, no job.
        bad_csv = (
            "[settings]\nnum_days,7\nhead_nurse_special_shift,true\n"
            "[schedule]\nName,Type,1,2,3,4,5,6,7\nSolo,,,,,,,,\n"
        )
        input_id = self._make_input(csv_text=bad_csv, source="grid")
        r = self.client.post(f"/inputs/{input_id}/solve")
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least 2 nurses", r.text)
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

    def test_status_fragment_infeasible(self):
        job_id = self._make_job(
            status=JobStatus.succeeded, solver_status="INFEASIBLE",
            result_grid=None, log="no solution",
        )
        r = self.client.get(f"/jobs/{job_id}/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No feasible schedule", r.text)
        self.assertIn("INFEASIBLE", r.text)
        self.assertNotIn("every 2s", r.text)

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
