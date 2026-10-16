"""Month helpers for the month-centric dashboard and "next month" duplication.

Shared contract (landed first, see docs/specs/2026-09-26-parallel-build.md):
``THAI_MONTHS``, ``EN_MONTHS`` and ``parse_month_year``. The dashboard feature
extends this module; duplication imports ``parse_month_year`` only.
"""

from __future__ import annotations

from dataclasses import dataclass

THAI_MONTHS: list[str] = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]
EN_MONTHS: list[str] = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def parse_month_year(ward_meta: dict | None) -> tuple[int, int] | None:
    """Return (year, month) from ward_meta, or None if missing/invalid.

    Values are stored as strings ("9", "09", "2026"). Month must be 1..12 and
    year > 0; anything else (blank, non-numeric, out of range) is undated.
    """
    meta = ward_meta or {}
    try:
        month = int(str(meta.get("month", "")).strip())
        year = int(str(meta.get("year", "")).strip())
    except (TypeError, ValueError):
        return None
    if not (1 <= month <= 12) or year <= 0:
        return None
    return year, month



STATE_LABELS: dict[str, str] = {
    "draft": "ร่าง / Draft",
    "running": "กำลังจัด / Running",
    "done": "เสร็จ / Done",
    "infeasible": "ไม่สำเร็จ / Infeasible",
    "timeout": "หมดเวลา / Timed out",
    "error": "ผิดพลาด / Error",
}

STATE_PILL: dict[str, str] = {
    "draft": "draft",
    "running": "run",
    "done": "done",
    "infeasible": "fail",
    "timeout": "draft",
    "error": "fail",
}


def job_state(job) -> str:
    """Map a SolveJob (duck-typed: .status .solver_status) to a dashboard state.

    ``job`` may be None (no job submitted yet for this input) -> "draft".
    """
    if job is None:
        return "draft"
    status = getattr(job.status, "value", job.status)
    if status in ("queued", "running"):
        return "running"
    if status == "failed":
        return "error"
    if status == "succeeded":
        solver_status = job.solver_status
        if solver_status in ("OPTIMAL", "FEASIBLE"):
            return "done"
        if solver_status == "INFEASIBLE":
            return "infeasible"
        return "timeout"
    return "draft"


@dataclass
class MonthRow:
    label: str
    ward_name: str
    hospital: str
    year: int | None
    month: int | None
    input_id: int
    versions: int
    nurse_count: int
    status: str
    job_id: int | None
    requests: str | None


def _nurse_count(data: dict) -> int:
    nurses = (data or {}).get("nurses", []) or []
    count = 0
    for n in nurses:
        if n.get("active", True):
            count += 1
    return count


def _requests_from_job(job) -> str | None:
    if job is None:
        return None
    result_grid = getattr(job, "result_grid", None) or {}
    report = result_grid.get("request_report")
    if not report:
        return None
    return f"{report.get('accepted')}/{report.get('total')}"


def month_rows(inputs, latest_job_by_input: dict) -> list["MonthRow"]:
    """Build one MonthRow per (ward_name, year, month) group, plus one row per
    undated input (never merged). ``inputs`` should be ordered so that, within
    each group, later entries are more recent (created_at, id) - the caller's
    query order (created_at desc) is fine since we track the max ourselves.
    """
    groups: dict[tuple, dict] = {}
    undated: list = []

    for inp in inputs:
        data = inp.data or {}
        ward_meta = data.get("ward_meta", {}) or {}
        ward_name = (ward_meta.get("ward_name") or "").strip()
        hospital = ward_meta.get("hospital") or ""
        parsed = parse_month_year(ward_meta)
        key = (ward_name.lower(), parsed[0], parsed[1]) if parsed else None

        if key is None:
            undated.append((inp, ward_name, hospital))
            continue

        year, month = parsed
        g = groups.get(key)
        if g is None:
            g = {
                "ward_name": ward_name,
                "hospital": hospital,
                "year": year,
                "month": month,
                "inputs": [],
            }
            groups[key] = g
        g["inputs"].append(inp)

    rows: list[MonthRow] = []

    for key, g in groups.items():
        group_inputs = g["inputs"]
        latest = max(group_inputs, key=lambda i: (i.created_at, i.id))
        job = latest_job_by_input.get(latest.id)
        status = job_state(job)
        label = (
            f"{THAI_MONTHS[g['month']]} {g['year']} / "
            f"{EN_MONTHS[g['month']]} {g['year']}"
        )
        rows.append(MonthRow(
            label=label,
            ward_name=g["ward_name"],
            hospital=g["hospital"],
            year=g["year"],
            month=g["month"],
            input_id=latest.id,
            versions=len(group_inputs),
            nurse_count=_nurse_count(latest.data),
            status=status,
            job_id=getattr(job, "id", None),
            requests=_requests_from_job(job),
        ))

    for inp, ward_name, hospital in undated:
        job = latest_job_by_input.get(inp.id)
        status = job_state(job)
        rows.append(MonthRow(
            label="ยังไม่ระบุเดือน / No month set",
            ward_name=ward_name,
            hospital=hospital,
            year=None,
            month=None,
            input_id=inp.id,
            versions=1,
            nurse_count=_nurse_count(inp.data),
            status=status,
            job_id=getattr(job, "id", None),
            requests=_requests_from_job(job),
        ))

    dated_rows = [r for r in rows if r.year is not None]
    undated_rows = [r for r in rows if r.year is None]
    dated_rows.sort(key=lambda r: (-r.year, -r.month, r.ward_name.lower()))
    # Undated rows: newest input first.
    undated_input_created = {inp.id: inp.created_at for inp, _, _ in undated}
    undated_rows.sort(key=lambda r: undated_input_created[r.input_id], reverse=True)

    return dated_rows + undated_rows
