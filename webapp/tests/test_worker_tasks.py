"""Tests for the background solve task.

These drive `run_solve_job` against a real (temp-file SQLite) database and the
real solver, with no Redis, asserting the job lifecycle it is responsible for:
  - a feasible input ends `succeeded` with a result grid,
  - an infeasible input ends `succeeded` but with no grid and solver_status
    INFEASIBLE (a valid "no schedule" answer, not a crash),
  - bad input (caught by validation) ends `failed` with the reason recorded.

If the task forgot to set a terminal status, stamp finished_at, or capture the
error, these would fail — they exercise the branch logic, not incidental detail.

Run from the project root:  python3 -m unittest webapp.tests.test_worker_tasks
"""

import os
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp import config  # noqa: E402
from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.db.models import (  # noqa: E402
    JobStatus, ScheduleInputRow, SolveJob, User, Ward,
)
from webapp.db.session import make_session_factory  # noqa: E402
from webapp.worker.tasks import run_solve_job  # noqa: E402


def _feasible_input():
    nurses = [Nurse(name=f"N{i}", type=("senior" if i < 2 else "")) for i in range(12)]
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "WorkerWard", "tier": "ward"},
        settings={"num_days": 7, "weekends": [6, 7]},
        nurses=nurses,
    )


def _infeasible_input():
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "TinyWard", "tier": "ward"},
        settings={"num_days": 3, "weekends": []},
        nurses=[Nurse(name="A", type="senior"), Nurse(name="B", type="senior")],
    )


def _bad_input():
    # Single nurse + senior rule on -> validation rejects it.
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "BadWard"},
        settings={"num_days": 7, "weekends": [6, 7],
                  "head_nurse_special_shift": True},
        nurses=[Nurse(name="Solo")],
    )


class WorkerTaskTest(unittest.TestCase):

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.Session = make_session_factory(f"sqlite:///{self._db_path}")

    def tearDown(self):
        os.unlink(self._db_path)

    def _seed_job(self, schedule_input, max_time_seconds=8) -> int:
        with self.Session() as db:
            user = User(google_sub="sub-1", email="a@example.com", name="A")
            ward = Ward(name="W", created_by=1)
            db.add_all([user, ward])
            db.flush()
            row = ScheduleInputRow(
                ward_id=ward.id, created_by=user.id, source="grid",
                data=schedule_input.to_dict(),
            )
            db.add(row)
            db.flush()
            job = SolveJob(
                input_id=row.id, ward_id=ward.id, submitted_by=user.id,
                max_time_seconds=max_time_seconds,
            )
            db.add(job)
            db.commit()
            return job.id

    def _reload(self, job_id) -> SolveJob:
        with self.Session() as db:
            return db.get(SolveJob, job_id)

    def test_feasible_job_succeeds_with_grid(self):
        job_id = self._seed_job(_feasible_input())
        run_solve_job(job_id, session_factory=self.Session)

        job = self._reload(job_id)
        self.assertEqual(job.status, JobStatus.succeeded)
        self.assertIn(job.solver_status, ("OPTIMAL", "FEASIBLE"))
        self.assertIsNotNone(job.result_grid)
        self.assertEqual(len(job.result_grid["rows"]), 12)
        self.assertTrue(job.result_csv)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

    def test_infeasible_job_succeeds_without_grid(self):
        job_id = self._seed_job(_infeasible_input())
        run_solve_job(job_id, session_factory=self.Session)

        job = self._reload(job_id)
        self.assertEqual(job.status, JobStatus.succeeded)
        self.assertEqual(job.solver_status, "INFEASIBLE")
        self.assertIsNone(job.result_grid)
        self.assertIsNotNone(job.finished_at)

    def test_bad_input_marks_job_failed(self):
        job_id = self._seed_job(_bad_input())
        run_solve_job(job_id, session_factory=self.Session)

        job = self._reload(job_id)
        self.assertEqual(job.status, JobStatus.failed)
        self.assertIn("2 active nurses", job.error)
        self.assertIsNone(job.result_grid)
        self.assertIsNotNone(job.finished_at)

    def test_enqueue_inline_runs_synchronously_without_redis(self):
        # In inline mode, enqueue_solve must call run_solve_job directly and
        # never import/connect to Redis. Monkeypatch run_solve_job to a recorder.
        import webapp.worker.tasks as t
        calls = []
        orig_fn, orig_flag = t.run_solve_job, config.SOLVE_INLINE
        t.run_solve_job = lambda jid: calls.append(jid)
        config.SOLVE_INLINE = True
        try:
            t.enqueue_solve(4242)
        finally:
            t.run_solve_job = orig_fn
            config.SOLVE_INLINE = orig_flag
        self.assertEqual(calls, [4242])


if __name__ == "__main__":
    unittest.main(verbosity=2)
