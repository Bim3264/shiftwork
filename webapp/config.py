"""Application configuration — the single source of truth for tunables.

Values come from environment variables with sensible defaults so the app runs
locally out of the box but is configurable in the cloud without code changes.

The two timeout knobs are deliberately kept together with an invariant assertion
(see below): the solver must always stop itself *before* the worker's hard job
timeout fires, otherwise solves would be killed mid-run and marked failed. If
someone misconfigures them, the process should refuse to start rather than
silently kill every job.
"""

import os


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ── Solve timeouts ────────────────────────────────────────────────────────────
# The solver caps its own CP-SAT search at SOLVER_MAX_TIME_SECONDS (graceful:
# returns the best schedule found so far). WORKER_JOB_TIMEOUT_SECONDS is the
# queue's hard backstop for a job that hangs outside the solver.
SOLVER_MAX_TIME_SECONDS = _get_int("SOLVER_MAX_TIME_SECONDS", 120)
WORKER_JOB_TIMEOUT_SECONDS = _get_int("WORKER_JOB_TIMEOUT_SECONDS", 180)

# ── Storage ───────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///shiftwork_web.db")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ── Auth (Google OIDC) ────────────────────────────────────────────────────────
# Master switch for login. When false, Google OAuth is turned off entirely and
# every request runs as a single shared user (same behaviour as DEV_AUTH_BYPASS,
# but intended as a deliberate deployment choice — e.g. launching before Google
# OAuth is set up). WARNING: with auth off, anyone who can reach the URL is in.
AUTH_ENABLED = _get_bool("AUTH_ENABLED", True)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-change-me")
# Mark the session cookie Secure (HTTPS-only). Turn ON in any HTTPS deployment
# (e.g. Railway); leave OFF for local http dev or the cookie won't be sent.
SESSION_HTTPS_ONLY = _get_bool("SESSION_HTTPS_ONLY", False)

# MVP access gate: an allowlist of emails and/or an allowed Google Workspace
# domain. Empty allowlist + empty domain means "allow any authenticated Google
# account" — fine for local dev, but set one of these in the cloud.
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "").strip().lower()

# Local-development escape hatch: when true, the app skips Google sign-in
# entirely and runs every request as a single local dev user. This exists so you
# can click through the app without configuring OAuth. It is OFF by default and
# must NEVER be enabled in a deployed/production environment.
DEV_AUTH_BYPASS = _get_bool("DEV_AUTH_BYPASS", False)
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "dev@localhost")

# Run solves synchronously in-process instead of enqueuing to Redis/RQ. This
# lets you use the app locally without running Redis + a worker. The request
# blocks until the solve finishes (up to SOLVER_MAX_TIME_SECONDS), so this is a
# development convenience, not how production should run. OFF by default.
SOLVE_INLINE = _get_bool("SOLVE_INLINE", False)

# ── Uploads ───────────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = _get_int("MAX_UPLOAD_BYTES", 1 * 1024 * 1024)  # 1 MiB


# ── Invariant ─────────────────────────────────────────────────────────────────
# Enforced at import time so a bad configuration fails fast and loudly.
assert SOLVER_MAX_TIME_SECONDS < WORKER_JOB_TIMEOUT_SECONDS, (
    f"SOLVER_MAX_TIME_SECONDS ({SOLVER_MAX_TIME_SECONDS}) must be strictly less "
    f"than WORKER_JOB_TIMEOUT_SECONDS ({WORKER_JOB_TIMEOUT_SECONDS}); otherwise "
    f"the worker would kill solves before they finish."
)
