"""The background solve task and its enqueue helper.

`run_solve_job` is a plain function that owns the job's lifecycle: mark it
running, solve, write the outcome back, and always stamp `finished_at`. It takes
an optional `session_factory` so it can run against a test database without any
global state — and it needs no Redis, so it is fully unit-testable.

Outcome mapping (what the results page keys off):
  - solved            -> status=succeeded, result_grid set, solver_status OPTIMAL/FEASIBLE
  - no feasible sched  -> status=succeeded, result_grid None, solver_status INFEASIBLE
                          (the solve *ran*; the honest answer is "no schedule exists")
  - crash / timeout    -> status=failed, error set

`enqueue_solve` is the only place that touches RQ/Redis; the import is lazy so
the rest of the app and the tests don't require them to be installed.
"""

from __future__ import annotations

from datetime import datetime

from webapp import config
from webapp.core.schedule_input import ScheduleInput
from webapp.core.solver_runner import run_schedule
from webapp.db.models import JobStatus, ScheduleInputRow, SolveJob
from webapp.db.session import SessionLocal


def run_solve_job(job_id: int, *, session_factory=None) -> int:
    """Execute the solve for `job_id`, writing status/result onto the job row."""
    Session = session_factory or SessionLocal
    with Session() as db:
        job = db.get(SolveJob, job_id)
        if job is None:
            raise ValueError(f"solve_job {job_id} not found")

        job.status = JobStatus.running
        job.started_at = datetime.utcnow()
        db.commit()

        try:
            row = db.get(ScheduleInputRow, job.input_id)
            if row is None:
                raise ValueError(f"schedule_input {job.input_id} not found")

            schedule_input = ScheduleInput.from_dict(row.data)
            schedule_input.validate()  # surfaces bad input as a clean failure

            result = run_schedule(
                schedule_input, max_time_seconds=job.max_time_seconds
            )
            job.solver_status = result.solver_status
            job.log = result.log
            if result.solved:
                job.result_grid = result.grid
                job.result_csv = result.csv_text
                job.status = JobStatus.succeeded
            else:
                # Ran fine, but the model has no solution — a valid answer, not
                # an error. Job succeeded; the UI shows a "no schedule" message.
                job.result_grid = None
                job.result_csv = ""
                job.status = JobStatus.succeeded
        except Exception as exc:  # noqa: BLE001 — any failure marks the job failed
            job.status = JobStatus.failed
            job.error = str(exc)
        finally:
            job.finished_at = datetime.utcnow()
            db.commit()

    return job_id


def enqueue_solve(job_id: int):
    """Hand a solve off to run.

    In inline mode (config.SOLVE_INLINE) the solve runs synchronously in-process
    — no Redis needed, handy for local development. Otherwise it is enqueued on
    the RQ queue with the hard worker job timeout. rq/redis are imported lazily
    so importing this module never requires them, and inline mode never touches
    them at all.
    """
    if config.SOLVE_INLINE:
        return run_solve_job(job_id)

    from redis import Redis
    from rq import Queue

    queue = Queue("solves", connection=Redis.from_url(config.REDIS_URL))
    return queue.enqueue(
        run_solve_job,
        job_id,
        job_timeout=config.WORKER_JOB_TIMEOUT_SECONDS,
    )
