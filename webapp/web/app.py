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
from webapp.core import presolve
from webapp.core.tiers import tier_view
from webapp.core.fairness import fairness_stats
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
        "min_request_percent": form.get("min_request_percent", "100"),
        "relax_days_off": form.get("relax_days_off") is not None,
        "enforce_vacation": form.get("enforce_vacation") is not None,
        "coverage_weekday_day": form.get("coverage_weekday_day", "5"),
        "coverage_weekday_evening": form.get("coverage_weekday_evening", "3"),
        "coverage_weekday_night": form.get("coverage_weekday_night", "3"),
        "coverage_weekend_day": form.get("coverage_weekend_day", "4"),
        "coverage_weekend_evening": form.get("coverage_weekend_evening", "2"),
        "coverage_weekend_night": form.get("coverage_weekend_night", "2"),
        "extra_holidays": form.get("extra_holidays", ""),
    }


def _schedule_input_from_form(form, row) -> ScheduleInput:
    """Build a ScheduleInput from a submitted edit form (settings + grid + the
    month/year in ward_meta). Shared by save and auto-fill."""
    raw_settings = _settings_from_form(form)
    try:
        num_days = int(raw_settings["num_days"])
    except (TypeError, ValueError):
        num_days = ScheduleInput.from_dict(row.data).settings["num_days"]
        raw_settings["num_days"] = num_days
    nurses = _nurses_from_form(form, num_days)
    ward_meta = dict(ScheduleInput.from_dict(row.data).ward_meta)
    for key in ("month", "year"):
        v = form.get(key)
        if v is not None and str(v).strip() != "":
            ward_meta[key] = str(v).strip()
    return ScheduleInput.from_grid(
        ward_meta=ward_meta, settings=raw_settings, nurses=nurses
    )


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
        # Checkbox present-iff-checked; a disabled nurse is kept but not solved.
        active = form.get(f"active_{r}") is not None
        shifts: dict[int, str] = {}
        for d in range(1, num_days + 1):
            raw = (form.get(f"cell_{r}_{d}") or "").strip()
            if not raw:
                continue
            # Match the CSV path: control words are lowercased, symbols kept.
            token = raw.lower() if raw.lower() in {"off", "mtg"} else raw
            shifts[d] = token
        nurses.append(Nurse(name=name, type=ntype, shifts=shifts, active=active))
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

    # Resolve ward name/hospital for each job from its input's stored metadata
    # (jobs don't carry it directly). Done here, not in the template, to avoid
    # lazy relationship loads during rendering.
    job_wards: dict[int, dict] = {}
    job_input_ids = {j.input_id for j in jobs}
    if job_input_ids:
        meta_by_input = {
            r.id: (r.data or {}).get("ward_meta", {})
            for r in db.execute(
                select(ScheduleInputRow).where(ScheduleInputRow.id.in_(job_input_ids))
            ).scalars().all()
        }
        for j in jobs:
            m = meta_by_input.get(j.input_id, {})
            job_wards[j.id] = {
                "ward_name": m.get("ward_name", ""),
                "hospital": m.get("hospital", ""),
            }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "inputs": inputs, "jobs": jobs, "job_wards": job_wards},
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
                {"user": user, "error": "Please choose a CSV or Excel file to upload."},
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
        # Accept both the sectioned CSV and the Excel roster template. Detect by
        # filename, falling back to the file's magic bytes (xlsx is a zip: "PK").
        filename = (getattr(upload, "filename", "") or "").lower()
        is_xlsx = filename.endswith((".xlsx", ".xlsm")) or contents[:4] == b"PK\x03\x04"
        try:
            if filename.endswith(".xls") and not is_xlsx:
                raise ValueError("Legacy .xls isn't supported — please save as .xlsx.")
            if is_xlsx:
                schedule_input = ScheduleInput.from_xlsx(contents)
                # Store the equivalent sectioned CSV as the canonical "original".
                original_csv = schedule_input.to_solver_csv()
                db_source = "xlsx_upload"
            else:
                text = contents.decode("utf-8-sig")
                schedule_input = ScheduleInput.from_csv(text)
                original_csv = text
                db_source = "csv_upload"
        except (UnicodeDecodeError, ValueError) as exc:
            return templates.TemplateResponse(
                request,
                "new_input.html",
                {"user": user, "error": f"Could not read that file: {exc}"},
                status_code=400,
            )

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
    # A full grid is many fields (nurses × days). Starlette's default form limit
    # is 1000 fields, which a large roster exceeds — silently dropping cells and
    # appearing to "randomly delete" data. Raise the limit generously.
    form = await request.form(max_fields=100_000)
    schedule_input = _schedule_input_from_form(form, row)
    row.data = schedule_input.to_dict()
    db.commit()
    return RedirectResponse(url=f"/inputs/{row.id}/edit", status_code=303)


@app.post("/inputs/{input_id}/autofill")
async def autofill_dates(input_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    """Auto-fill num_days + weekends from the input's month/year: calendar days,
    all Sat/Sun, public holidays (Thailand), plus the manual extra_holidays.
    Applies any pending form edits first so nothing typed is lost."""
    row = _load_input(db, user, input_id)
    form = await request.form(max_fields=100_000)
    schedule_input = _schedule_input_from_form(form, row)
    try:
        month = int(schedule_input.ward_meta.get("month"))
        year = int(schedule_input.ward_meta.get("year"))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return _render_edit(request, user, row, schedule_input,
                            error="Enter a valid month (1–12) and year, then auto-fill.",
                            status_code=400)

    from webapp.core.calendar_util import month_dates
    md = month_dates(year, month, schedule_input.settings.get("extra_holidays", []))
    schedule_input.settings["num_days"] = md["num_days"]
    schedule_input.settings["weekends"] = md["combined"]
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

    report = presolve.check(schedule_input)
    if not report.ok:
        return _render_edit(request, user, row, schedule_input,
                            error="Fix the blockers below before solving.",
                            check=report, status_code=400)

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


@app.post("/inputs/{input_id}/check")
async def check_input(input_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    """Save the form (like autofill) and run the pre-solve checklist without
    queuing a solve — the "ตรวจสอบ / Check" button."""
    row = _load_input(db, user, input_id)
    form = await request.form(max_fields=100_000)
    schedule_input = _schedule_input_from_form(form, row)
    row.data = schedule_input.to_dict()
    db.commit()
    try:
        schedule_input.validate()
    except ValueError as exc:
        return _render_edit(request, user, row, schedule_input,
                            error=str(exc), status_code=400)

    report = presolve.check(schedule_input)
    return _render_edit(request, user, row, schedule_input, error=None, check=report)


def _fairness_for_job(job) -> dict | None:
    """Per-nurse fairness stats for a solved job, or None. Pure derivation from
    the stored result_grid — no solver run, works on old jobs too."""
    if job.status.value != "succeeded" or not job.result_grid:
        return None
    settings = (getattr(job.input, "data", None) or {}).get("settings", {}) or {}
    seniors = {0, 1} if settings.get("head_nurse_special_shift") else set()
    return fairness_stats(
        job.result_grid, settings.get("weekends"), seniors
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    job = _load_job(db, user, job_id)
    return templates.TemplateResponse(
        request, "job.html",
        {"user": user, "job": job, "fair": _fairness_for_job(job)},
    )


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status(job_id: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    job = _load_job(db, user, job_id)
    return templates.TemplateResponse(
        request, "status_fragment.html",
        {"user": user, "job": job, "fair": _fairness_for_job(job)},
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
def _render_edit(request, user, row, schedule_input, error, check=None, status_code=200):
    num_days = schedule_input.settings["num_days"]
    days = list(range(1, num_days + 1))

    # If month/year are set, show which public holidays would be picked up.
    holidays_preview = None
    try:
        month = int(schedule_input.ward_meta.get("month"))
        year = int(schedule_input.ward_meta.get("year"))
        from webapp.core.calendar_util import month_dates
        holidays_preview = month_dates(
            year, month, schedule_input.settings.get("extra_holidays", [])
        )["holidays"]
    except (TypeError, ValueError):
        pass

    return templates.TemplateResponse(
        request,
        "edit_input.html",
        {
            "user": user,
            "row": row,
            "settings": schedule_input.settings,
            "weekends_str": " ".join(str(d) for d in schedule_input.settings["weekends"]),
            "extra_holidays_str": " ".join(
                str(d) for d in schedule_input.settings.get("extra_holidays", [])),
            "holidays_preview": holidays_preview,
            "nurses": schedule_input.nurses,
            "days": days,
            "valid_tokens": sorted(t for t in VALID_CELL_TOKENS if t),
            "ward_meta": schedule_input.ward_meta,
            "tier": tier_view(schedule_input),
            "error": error,
            "check": check,
        },
        status_code=status_code,
    )
