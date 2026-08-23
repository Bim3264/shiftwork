"""The ShiftWork web application.

Server-rendered FastAPI + Jinja2 + HTMX. Google sign-in and tenancy live in
``auth.py``; this module owns the data routes from the design's route inventory
(§12). Every ``[auth]`` route enforces ward scoping: the resource's ``ward_id``
must be one the logged-in user belongs to.

The solve is never run in a request. ``POST /inputs/{id}/solve`` only creates a
``SolveJob`` (status ``queued``) and hands it to ``enqueue_solve``; the worker
runs it out-of-process and writes the result back, which the job page polls for.
``enqueue_solve`` is imported as a module attribute here so tests can monkeypatch
it (run synchronously / no-op) without a Redis dependency.
"""

from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp import config
from webapp.core.schedule_input import (
    VALID_CELL_TOKENS,
    Nurse,
    ScheduleInput,
)
from webapp.db.models import (
    JobStatus,
    ScheduleInputRow,
    SolveJob,
)
from webapp.db.session import init_db
from webapp.logging_setup import log_path, setup_logging
from webapp.web.auth import (
    ensure_ward_access,
    get_db,
    get_or_create_default_ward,
    install_auth,
    require_user,
    user_ward_ids,
)
from webapp.worker.tasks import enqueue_solve

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATE_DIR)

# Configure error logging as early as possible so startup and request failures
# are written to log.txt (see webapp/logging_setup.py).
setup_logging()
logger = logging.getLogger("shiftwork.web")

app = FastAPI(title="ShiftWork")
install_auth(app)


@app.exception_handler(Exception)
async def _log_unhandled(request: Request, exc: Exception):
    """Log any unhandled error (with traceback) to log.txt and show a plain 500.

    HTTPException (404s, etc.) has its own handler and is not caught here, so
    only genuine bugs reach this and get logged."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return HTMLResponse(
        "<h1>Something went wrong</h1>"
        f"<p>The error has been logged to <code>{log_path()}</code>.</p>",
        status_code=500,
    )


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _load_input(db: Session, user, input_id: int) -> ScheduleInputRow:
    row = db.get(ScheduleInputRow, input_id)
    if row is None or not ensure_ward_access(db, user, row.ward_id):
        # 404 rather than 403 so we don't leak the existence of other wards' rows.
        raise HTTPException(status_code=404, detail="Input not found")
    return row


def _load_job(db: Session, user, job_id: int) -> SolveJob:
    job = db.get(SolveJob, job_id)
    if job is None or not ensure_ward_access(db, user, job.ward_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _settings_from_form(form) -> dict:
    """Pull the constraint-selector fields out of a submitted form.

    ``enable_meetings`` is deliberately NOT read here: it is tier-derived, shown
    read-only, and never user-editable. Checkboxes are present-iff-checked.
    """
    return {
        "num_days": form.get("num_days", ""),
        "weekends": form.get("weekends", ""),
        "allow_evening_to_night": form.get("allow_evening_to_night") is not None,
        "allow_night_to_day": form.get("allow_night_to_day") is not None,
        "head_nurse_special_shift": form.get("head_nurse_special_shift") is not None,
    }


def _nurses_from_form(form, num_days: int) -> list[Nurse]:
    """Rebuild nurse rows from grid form fields: name_{r}, type_{r}, cell_{r}_{d}."""
    try:
        row_count = int(form.get("row_count", "0"))
    except (TypeError, ValueError):
        row_count = 0
    nurses: list[Nurse] = []
    for r in range(row_count):
        name = (form.get(f"name_{r}") or "").strip()
        if not name:
            continue
        ntype = (form.get(f"type_{r}") or "").strip()
        shifts: dict[int, str] = {}
        for d in range(1, num_days + 1):
            raw = (form.get(f"cell_{r}_{d}") or "").strip()
            if not raw:
                continue
            # Match the CSV path: control words are lowercased, symbols kept.
            token = raw.lower() if raw.lower() in {"off", "mtg"} else raw
            shifts[d] = token
        nurses.append(Nurse(name=name, type=ntype, shifts=shifts))
    return nurses


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    ward_ids = user_ward_ids(db, user)
    inputs = []
    jobs = []
    if ward_ids:
        inputs = db.execute(
            select(ScheduleInputRow)
            .where(ScheduleInputRow.ward_id.in_(ward_ids))
            .order_by(ScheduleInputRow.created_at.desc())
            .limit(20)
        ).scalars().all()
        jobs = db.execute(
            select(SolveJob)
            .where(SolveJob.ward_id.in_(ward_ids))
            .order_by(SolveJob.created_at.desc())
            .limit(20)
        ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "inputs": inputs, "jobs": jobs},
    )


@app.get("/inputs/new", response_class=HTMLResponse)
def new_input(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse(
        request, "new_input.html", {"user": user, "error": None}
    )


@app.post("/inputs")
async def create_input(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    source = form.get("source", "csv_upload")
    ward = get_or_create_default_ward(db, user)

    if source == "grid":
        # Start-blank-grid: default settings, no nurses yet (added on the edit page).
        schedule_input = ScheduleInput.from_grid(ward_meta={}, settings={}, nurses=[])
        original_csv = ""
        db_source = "grid"
    else:
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            return templates.TemplateResponse(
                request,
                "new_input.html",
                {"user": user, "error": "Please choose a CSV file to upload."},
                status_code=400,
            )
        contents = await upload.read()
        if len(contents) > config.MAX_UPLOAD_BYTES:
            return templates.TemplateResponse(
                request,
                "new_input.html",
                {"user": user,
                 "error": f"File is too large (limit {config.MAX_UPLOAD_BYTES} bytes)."},
                status_code=400,
            )
        try:
            text = contents.decode("utf-8-sig")
            schedule_input = ScheduleInput.from_csv(text)
        except (UnicodeDecodeError, ValueError) as exc:
            return templates.TemplateResponse(
                request,
                "new_input.html",
                {"user": user, "error": f"Could not read that CSV: {exc}"},
                status_code=400,
            )
        original_csv = text
        db_source = "csv_upload"

    row = ScheduleInputRow(
        ward_id=ward.id,
        created_by=user.id,
        source=db_source,
        data=schedule_input.to_dict(),
        original_csv=original_csv,
    )
    db.add(row)
    db.commit()
    return RedirectResponse(url=f"/inputs/{row.id}/edit", status_code=303)


@app.get("/inputs/{input_id}/edit", response_class=HTMLResponse)
def edit_input(input_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    row = _load_input(db, user, input_id)
    schedule_input = ScheduleInput.from_dict(row.data)
    return _render_edit(request, user, row, schedule_input, error=None)


@app.post("/inputs/{input_id}")
async def update_input(input_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    row = _load_input(db, user, input_id)
    form = await request.form()
    raw_settings = _settings_from_form(form)
    try:
        num_days = int(raw_settings["num_days"])
    except (TypeError, ValueError):
        num_days = ScheduleInput.from_dict(row.data).settings["num_days"]
        raw_settings["num_days"] = num_days

    nurses = _nurses_from_form(form, num_days)
    schedule_input = ScheduleInput.from_grid(
        ward_meta=ScheduleInput.from_dict(row.data).ward_meta,
        settings=raw_settings,
        nurses=nurses,
    )
    row.data = schedule_input.to_dict()
    db.commit()
    return RedirectResponse(url=f"/inputs/{row.id}/edit", status_code=303)


@app.post("/inputs/{input_id}/solve")
def solve_input(input_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    row = _load_input(db, user, input_id)
    schedule_input = ScheduleInput.from_dict(row.data)
    try:
        schedule_input.validate()
    except ValueError as exc:
        return _render_edit(request, user, row, schedule_input,
                            error=str(exc), status_code=400)

    job = SolveJob(
        input_id=row.id,
        ward_id=row.ward_id,
        submitted_by=user.id,
        status=JobStatus.queued,
        max_time_seconds=config.SOLVER_MAX_TIME_SECONDS,
    )
    db.add(job)
    db.commit()
    job_id = job.id
    enqueue_solve(job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    job = _load_job(db, user, job_id)
    return templates.TemplateResponse(
        request, "job.html", {"user": user, "job": job}
    )


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status(job_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    job = _load_job(db, user, job_id)
    return templates.TemplateResponse(
        request, "status_fragment.html", {"user": user, "job": job}
    )


@app.get("/jobs/{job_id}/download")
def job_download(job_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    job = _load_job(db, user, job_id)
    if not job.result_csv:
        raise HTTPException(status_code=404, detail="No result CSV for this job")
    # Prepend a UTF-8 BOM so Excel (esp. on Windows) detects UTF-8 and renders
    # the Thai shift symbols correctly instead of falling back to the system
    # codepage. lstrip guards against double-BOM if one is ever already present.
    csv_bytes = ("﻿" + job.result_csv.lstrip("﻿")).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="schedule_{job_id}.csv"'
        },
    )


# ── Shared render for the edit page ───────────────────────────────────────────
def _render_edit(request, user, row, schedule_input, error, status_code=200):
    num_days = schedule_input.settings["num_days"]
    days = list(range(1, num_days + 1))
    return templates.TemplateResponse(
        request,
        "edit_input.html",
        {
            "user": user,
            "row": row,
            "settings": schedule_input.settings,
            "weekends_str": " ".join(str(d) for d in schedule_input.settings["weekends"]),
            "nurses": schedule_input.nurses,
            "days": days,
            "valid_tokens": sorted(t for t in VALID_CELL_TOKENS if t),
            "error": error,
        },
        status_code=status_code,
    )
