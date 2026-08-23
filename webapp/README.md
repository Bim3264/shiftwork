# ShiftWork Web (MVP)

A server-rendered web layer over the existing `shiftwork` solver, per
`../WEB_MVP_DESIGN.md`. Team sign in with Google, provide a ward's requests
(CSV upload or grid), tune the constraints, run a background solve, view/download
the schedule.

## Layout

```
webapp/
  config.py            env-driven config + the solver/worker timeout invariant
  core/
    schedule_input.py  ScheduleInput — the one canonical input (CSV & grid unify here)
    solver_runner.py   the ONLY caller of shiftwork.Solver (runs it in a temp dir)
  db/
    models.py          user / ward / ward_member / schedule_input / solve_job
    session.py         engine + session factory
  worker/
    tasks.py           run_solve_job (the background task) + enqueue_solve (RQ)
  web/
    auth.py            Google OIDC, sessions, allowlist, ward scoping
    app.py             FastAPI app + all routes (design §12)
    templates/         Jinja2 + HTMX pages
  tests/               unit + TestClient tests (39, all passing)
```

## Run locally

```bash
pip install -r webapp/requirements.txt

# Background pieces
redis-server &                                  # the job queue backend
python -m webapp.worker.run_worker &            # the solve worker (RQ + logging)

# Config (minimum for a real login; omit the Google vars for dev-mode auth)
export SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export GOOGLE_CLIENT_ID=...        # from Google Cloud OAuth credentials
export GOOGLE_CLIENT_SECRET=...
export ALLOWED_DOMAIN=yourteam.com # or ALLOWED_EMAILS=a@x.com,b@x.com

# App
uvicorn webapp.web.app:app --reload
```

Auth dev-mode: if both `ALLOWED_EMAILS` and `ALLOWED_DOMAIN` are unset, any
authenticated Google account is allowed. Set one before exposing the app.

### Run locally with NO auth (no Google needed)

To click through the app without configuring Google OAuth at all, set the
dev bypass. Every request then runs as a single local user (`dev@localhost`):

```bash
export DEV_AUTH_BYPASS=1        # skip Google sign-in
export SOLVE_INLINE=1           # run solves in-process (no Redis/worker needed)
export SESSION_SECRET=dev
uvicorn webapp.web.app:app
```

`DEV_AUTH_BYPASS` skips login; `SOLVE_INLINE` runs each solve synchronously in
the request (so the Solve button works without Redis — the page just waits while
it solves). On Windows, `webapp\run_local.bat` sets both for you. Both are
development-only escape hatches, off by default, and print a warning; **never
enable them in a deployed environment.**

To turn login off in a *deployed* setting (e.g. to launch before Google OAuth is
configured), use `AUTH_ENABLED=0` instead of the dev bypass — same single-user
effect, but it's a deliberate, production-facing switch. It still means anyone
with the URL is in, so treat it as temporary.

The Google OAuth redirect URI to register is `<your-host>/auth/callback`.

## Timeouts

`SOLVER_MAX_TIME_SECONDS` (default 120) caps the solver's own search;
`WORKER_JOB_TIMEOUT_SECONDS` (default 180) is RQ's hard backstop. `config.py`
asserts the former is strictly less than the latter at import, so a bad config
fails fast instead of killing solves mid-run.

## Logs / debugging

Errors are written to a per-run, timestamped file under `logs/`, e.g.
`logs/shiftwork_2026-08-23_12-17-26.log` (override the exact path with the
`SHIFTWORK_LOG` env var). The file — and the `logs/` folder — are created **only
when something goes wrong**: a request that 500s, a worker crash, or any uncaught
exception (including a crash on startup). A clean run leaves nothing behind. When
something breaks, open the newest file in `logs/` for the full traceback.

## Tests

```bash
python3 -m unittest discover -s webapp/tests -p "test_*.py"
```

Web tests use FastAPI's `TestClient` with the auth dependency overridden and
`enqueue_solve` monkeypatched, so they need neither Google nor Redis. Solver
tests run the real solver on small wards.

## Security follow-ups before production

These are known, deliberate MVP gaps — safe enough for an internal team, but
close them before the app faces real customers:

1. **CSRF tokens** on state-changing POSTs (`/inputs`, `/inputs/{id}`,
   `/inputs/{id}/solve`, `/logout`). The session cookie alone does not stop
   cross-site form submission.
2. **Harden the session cookie**: set `https_only=True` (and keep
   `same_site="lax"` so the OAuth callback still carries it) once served over
   HTTPS. Best done via a config flag so local HTTP dev still works.
3. **Set `SESSION_SECRET`** to a strong random value in every non-dev
   environment (the default is an obvious placeholder).
4. **Rate-limit** `/login` and the solve endpoint.

## Not end-to-end verified in this environment

The real Google OAuth network round-trip (`/login` → Google → `/auth/callback`)
and the live Redis/RQ path were not exercised here (no outbound OAuth, no Redis
in the sandbox). Routing/wiring is confirmed via TestClient; the external steps
need a real deployment to validate.
