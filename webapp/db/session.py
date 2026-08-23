"""Engine + session factory.

For the MVP the schema is created with `create_all`; Alembic migrations are the
product-phase upgrade. `make_session_factory(url)` builds an isolated factory
(used by tests) so nothing depends on the global engine.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp import config
from webapp.db.models import Base


def normalize_db_url(url: str) -> str:
    """Normalize a database URL for SQLAlchemy.

    Managed Postgres providers (Railway, Heroku, etc.) hand out URLs like
    ``postgres://...`` or ``postgresql://...``. SQLAlchemy needs an explicit
    driver, and we use psycopg (v3), so rewrite both to ``postgresql+psycopg://``.
    Non-Postgres URLs (e.g. sqlite) are returned unchanged.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None):
    u = normalize_db_url(url or config.DATABASE_URL)
    connect_args = {"check_same_thread": False} if u.startswith("sqlite") else {}
    return create_engine(u, connect_args=connect_args, future=True)


engine = make_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, future=True
)


def init_db(eng=None) -> None:
    Base.metadata.create_all(eng or engine)


def make_session_factory(url: str):
    """Build a fresh engine + schema + session factory for the given URL."""
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    return sessionmaker(
        bind=eng, autoflush=False, expire_on_commit=False, future=True
    )
