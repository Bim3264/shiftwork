"""SQLAlchemy models — ward-scoped from day one.

A *ward* is the future SaaS tenant (pricing is already per-ward), so inputs and
jobs belong to a ward even in the internal MVP. Productizing later means adding
columns/tables (tier, subscription, billing) and swapping SQLite for Postgres —
not reshaping these core entities.

JSON columns hold the canonical ScheduleInput (`schedule_input.data`) and the
rendered result grid (`solve_job.result_grid`); both are exactly the shapes the
web and worker layers already pass around.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="member")  # member | admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Ward(Base):
    __tablename__ = "wards"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    hospital: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Product-phase fields (tier, license_key, subscription_id) are added later.


class WardMember(Base):
    """Which users may see/use a ward. In the MVP everyone allowlisted can be
    added to a shared ward; the table exists so per-ward membership is additive
    when the product opens up."""
    __tablename__ = "ward_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")


class ScheduleInputRow(Base):
    """A stored, re-editable solve input (canonical ScheduleInput as JSON)."""
    __tablename__ = "schedule_inputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(32))  # csv_upload | grid
    data: Mapped[dict] = mapped_column(JSON)          # ScheduleInput.to_dict()
    original_csv: Mapped[str] = mapped_column(Text, default="", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    jobs: Mapped[list["SolveJob"]] = relationship(back_populates="input")


class SolveJob(Base):
    __tablename__ = "solve_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    input_id: Mapped[int] = mapped_column(ForeignKey("schedule_inputs.id"), index=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id"), index=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.queued, index=True
    )
    max_time_seconds: Mapped[int] = mapped_column(Integer)

    solver_status: Mapped[str] = mapped_column(String(32), default="", nullable=True)
    result_grid: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_csv: Mapped[str] = mapped_column(Text, default="", nullable=True)
    log: Mapped[str] = mapped_column(Text, default="", nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    input: Mapped["ScheduleInputRow"] = relationship(back_populates="jobs")
