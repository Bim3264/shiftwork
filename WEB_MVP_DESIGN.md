# ShiftWork Web MVP — Design

Status: **draft for review** · Author: design pass · Scope: internal team MVP that converts cleanly into the final SaaS product.

---

## 1. Purpose & scope

Give the team a web app to generate nurse schedules without touching the codebase: sign in, provide a ward's requests (by uploading a CSV **or** editing a grid), tweak the rule settings, run the solver, and view/download the result. It must be minimal to build now but must not have to be thrown away when it becomes the customer-facing product.

**In scope (MVP):** Google sign-in, a single canonical schedule-input model fed by CSV upload *and* an in-browser grid, a constraint selector defaulted from the input, background solving with live status, and result view + CSV download — all multi-ward from day one.

**Out of scope (MVP, deferred to product):** self-serve signup, billing/PromptPay, the tier/license *enforcement UI*, polished styling, real-time collaborative editing, mobile-first layout.

---

## 2. Guiding principles

1. **Reuse the solver unchanged.** `shiftwork.Solver(...).run(max_time_seconds=...)` is the engine. The web app wraps it; it does not fork or rewrite solver logic. The recent `build()/solve()/run()` split and the extracted `_write_schedule()` make this wrapping clean.
2. **Model tenancy from day one.** A *ward* is the future customer tenant (your tier/pricing is already per-ward). Even in the MVP, inputs and jobs belong to a ward, and every query is ward-scoped. Productizing later means changing *who can create wards and how they pay* — not the data model.
3. **One canonical input representation.** Upload and grid both normalize into the same `ScheduleInput`. The solver, the storage layer, and the grid renderer all speak that one shape, so no feature is bolted on the side.
4. **The solve is a background job, never an HTTP request.** A solve takes up to 120s and saturates every CPU core. That cannot happen inside a page request. This is the single most important architectural decision below.

---

## 3. High-level architecture

```
                       ┌──────────────────────────────────────┐
        Google OIDC ── │  Web app (FastAPI + Jinja + HTMX)     │
                       │  - auth / sessions                    │
   Browser ──────────▶ │  - upload / grid / settings forms     │
      ▲   HTMX poll    │  - enqueue solve, render results      │
      │                └───────────┬───────────────┬──────────┘
      │                            │ write          │ enqueue job
      │                            ▼                │
      │                   ┌─────────────────┐       ▼
      └─ status/grid ◀────│  Database        │   ┌────────────┐
                          │  (SQLite→PG)     │◀──│ Redis queue│
                          │  users, wards,   │   └─────┬──────┘
                          │  inputs, jobs    │         │ pop job
                          └────────▲─────────┘         ▼
                                   │ write result ┌────────────────────┐
                                   └──────────────│ Worker process     │
                                                  │ runs Solver.run()  │
                                                  │ in an isolated dir │
                                                  └────────────────────┘
```

Four processes on one small cloud VM (or one PaaS project with a web + worker + Redis add-on): the **web app**, a **worker**, **Redis** (job queue), and the **database**. Object/file storage is just the local disk for the MVP (job temp dirs + stored CSVs), swappable for S3-style storage later.

---

## 4. Why background jobs (the core decision)

A schedule solve runs up to `max_time_seconds` (default 120) and uses `os.cpu_count()` search workers, so it pegs the machine. If it ran inside the request:

- the page would hang for up to two minutes and likely hit gateway timeouts,
- one solve would starve every other user,
- a browser refresh or drop would kill or duplicate the work.

So: a form submit **creates a `solve_job` row (status `queued`) and enqueues it**, then immediately returns a job page. An HTMX fragment on that page polls job status every ~2s and swaps in the grid + download link when the worker finishes. The worker pulls one job at a time (concurrency 1 on a small VM, since each solve already uses all cores) and writes the result back to the job row.

For a truly bare-minimum start this could be a single background thread, but that loses jobs on restart and can't scale to multiple workers — so a real queue (**Redis + RQ**) is the recommended MVP baseline; it's a few lines more and is the same thing you'd run in production.

---

## 5. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** + Uvicorn/Gunicorn | Async (good for status polling), typed, tiny; server-renders via Jinja. |
| Templating / interactivity | **Jinja2 + HTMX** | Server-rendered pages with just enough interactivity (grid edits, poll-and-swap) without a JS SPA. |
| Background jobs | **RQ + Redis** | Simplest solid Python queue; worker runs the solver out-of-process. |
| Database | **SQLite** now → **Postgres** later | SQLite is zero-ops for a small team; SQLAlchemy models make the swap mechanical. |
| ORM / migrations | **SQLAlchemy + Alembic** | Clean schema evolution from MVP to product. |
| Auth | **Authlib** (Google OIDC) + signed session cookie | Standard, minimal Google SSO. |
| Solver | existing `shiftwork` package | Unchanged. |

Everything is Python, matching your "all-Python, server-rendered" choice, so there's one language across web, worker, and solver.

---

## 6. Component breakdown

- **`web/`** — FastAPI app: routes, Jinja templates, HTMX fragments, auth middleware, session handling.
- **`core/schedule_input.py`** — the canonical `ScheduleInput` model (ward meta + settings + nurse rows) plus converters: `from_csv(text)`, `from_grid(form)`, `to_solver_csv()`, `to_grid_view()`. This is the hub that unifies upload and grid.
- **`core/solver_runner.py`** — the adapter that takes a `ScheduleInput`, runs the solver in an isolated temp workdir, and returns a normalized result (grid + status + log + CSV bytes). The one place that touches `shiftwork.Solver`.
- **`worker/`** — RQ worker entrypoint; the task function loads the job, calls `solver_runner`, writes the result back.
- **`db/`** — SQLAlchemy models + session; Alembic migrations.
- **`shiftwork` (existing)** — solver, imported as a library.

---

## 7. Data model

Ward-scoped from the start; light enough for an MVP.

- **user** — `id, google_sub (unique), email, name, role (member|admin), created_at`. MVP access is gated by an email/domain **allowlist**.
- **ward** — `id, name, hospital, created_by, created_at`. The tenant. (Later: `tier, license_key, subscription_id`.)
- **ward_member** — `ward_id, user_id, role` — which users can see/use a ward. (MVP could auto-add all allowlisted users to a shared ward; the table still exists so productizing is additive.)
- **schedule_input** — `id, ward_id, created_by, source (csv_upload|grid), ward_meta (JSON), settings (JSON), nurses (JSON rows), original_csv (text, nullable), created_at`. The stored canonical input.
- **solve_job** — `id, input_id, ward_id, submitted_by, status (queued|running|succeeded|failed), max_time_seconds, solver_status (OPTIMAL|FEASIBLE|INFEASIBLE|null), result_grid (JSON), result_csv (text), log (text), error (text), created_at, started_at, finished_at`.

The two JSON blobs (`settings`, `nurses`) are exactly what the grid and the constraint selector read/write, and exactly what `solver_runner` turns into the sectioned CSV the solver expects. `result_grid` is what the results page renders; `result_csv` is the download.

---

## 8. Solver integration

The solver currently reads a **filename** and writes `output/{label} Final.csv` relative to the working directory. The adapter respects that without changing solver code:

1. `solver_runner` creates a per-job temp dir with `input/` and `output/` subfolders.
2. It writes `ScheduleInput.to_solver_csv()` into `input/<name>.csv`.
3. It runs `Solver(path, num_days, weekends, allow_e_n, allow_n_d).run(max_time_seconds=...)` with the working dir set to the temp dir.
4. It reads back `output/<label> Final.csv`, parses it into `result_grid`, captures stdout as `log`, and returns `{solver_status, grid, csv, log}`.
5. The temp dir is deleted; only the parsed result is persisted on the job.

This is zero-risk reuse. **Optional small enhancement** (natural follow-on to the `_write_schedule()` extraction we already did): have the writer also *return* the grid as a dict so the runner can skip the disk round-trip. Not required for MVP.

Concurrency note: because `num_search_workers = os.cpu_count()`, each solve wants the whole box — so worker concurrency stays at 1 on a small VM. Per-tier queue priority/concurrency is a product-phase concern the queue already makes easy.

---

## 9. Authentication (Google SSO)

- **Flow:** Authlib OIDC — `/login` redirects to Google, `/auth/callback` exchanges the code, verifies the ID token, and looks up (or creates, if allowlisted) the `user` by `google_sub`. A signed, HTTP-only session cookie holds the user id.
- **Gating for the MVP:** an **allowlist** of emails or an allowed Google Workspace domain. Anyone else who authenticates is refused — this keeps the internal MVP private without building signup/roles yet.
- **Route protection:** middleware requires a valid session for everything except `/login`, `/auth/callback`, and `/healthz`.
- **CSRF:** state param on the OAuth round-trip; CSRF tokens on all form POSTs.
- **Product path:** swapping the allowlist for open signup + ward invites is additive — the `user`/`ward_member` model already supports it.

---

## 10. Input layer: upload, grid, and constraint selector

All three feed the one `ScheduleInput`.

**CSV upload.** Parse the sectioned file (the existing `[ward]`/`[settings]`/`[schedule]` format) into `ScheduleInput`. Validate size/shape and surface friendly errors instead of tracebacks. Legacy grid-only CSVs still parse (defaults applied), matching current `DataImporter` behavior.

**Editable grid.** An HTML table: one row per nurse (name + type: senior/new/blank), one column per day, each cell a shift value (`ช`, `บ`, `ด`, `ช/บ`, `ด/บ`, `off`, `mtg`, or blank). HTMX handles add/remove-nurse-row. On submit it serializes to the same `ScheduleInput`. Upload and grid are interchangeable: you can upload a CSV and then keep editing it in the grid.

**Constraint selector.** A form bound to the `[settings]` fields, **defaulted from the parsed input** exactly as you asked:

- `num_days` (schedule length)
- `weekends` (which days)
- `allow_evening_to_night` (E→N transition)
- `allow_night_to_day` (N→D transition)
- `head_nurse_special_shift` (senior DAY/OFF rule)
- `enable_meetings` — shown read-only, because it's **tier-gated** in `DataImporter`, not a free choice.

When a CSV is uploaded, the selector pre-fills from its `[settings]`; the user can override before solving, and the overrides are merged into the `ScheduleInput` that goes to the solver. Tier limits (`max_nurses`, `max_req_shifts`, `max_holidays`) are validated on submit, reusing the existing tier logic.

---

## 11. Core user flow

1. **Sign in** with Google → land on the ward's dashboard (recent inputs + jobs).
2. **New schedule** → choose *Upload CSV* or *Start blank grid*.
3. **Review/edit** the grid and the **constraint selector** (pre-filled). Optionally set the solve time limit.
4. **Solve** → creates a `solve_job`, enqueues it, redirects to the **job page**.
5. **Job page** polls status: `queued → running → succeeded/failed`. On success it shows the schedule grid and a **Download CSV** button; on `INFEASIBLE` it shows the "no solution" explanation and the solver log; on error it shows a friendly failure with the log for debugging.
6. History: past inputs and jobs are listed on the dashboard, re-openable.

---

## 12. Route inventory (MVP)

```
GET  /                      dashboard (recent inputs + jobs)      [auth]
GET  /login                 start Google OIDC
GET  /auth/callback         OIDC callback → session
POST /logout
GET  /inputs/new            choose upload vs grid                 [auth]
POST /inputs                create ScheduleInput (upload or grid) [auth]
GET  /inputs/{id}/edit      grid + constraint selector           [auth]
POST /inputs/{id}           update grid/settings                 [auth]
POST /inputs/{id}/solve     enqueue solve_job                     [auth]
GET  /jobs/{id}             job page (polls fragment)            [auth]
GET  /jobs/{id}/status      HTMX status fragment                 [auth]
GET  /jobs/{id}/download    result CSV                           [auth]
GET  /healthz               liveness (no auth)
```

Every `[auth]` route also enforces **ward scoping**: the resource's `ward_id` must be one the user belongs to.

---

## 13. Security & isolation

- Each solve runs in its **own temp dir**; nothing shared between jobs.
- Uploaded CSVs are size-capped and structurally validated before parsing; parsing errors never leak stack traces to users.
- **Tenancy:** every input/job query is filtered by the user's ward membership; no cross-ward reads.
- Sessions are signed, HTTP-only, `Secure` (HTTPS). TLS terminated at the host/proxy.
- Secrets (Google client id/secret, session key, Redis URL) come from environment/secret store, never the repo.

---

## 14. Deployment (cloud)

Smallest sensible footprint:

- **Option A — one small VM:** Nginx/Caddy (TLS) → Gunicorn/Uvicorn (web), a systemd-managed RQ **worker**, a local **Redis**, and **SQLite** on disk. Cheapest; fine for a team.
- **Option B — a PaaS** (Render/Railway/Fly): a *web* service + a *worker* service + a *Redis* add-on + a small *Postgres*. Slightly more money, less ops, and it's already the shape of the production deploy.

Recommendation: **Option B with SQLite→Postgres from the start** if you want the least friction converting to product; **Option A** if you want the cheapest possible internal MVP. Either way the app code is identical.

---

## 15. MVP scope vs. deferred

**MVP builds:** Google SSO + allowlist · ward-scoped data model · `ScheduleInput` with CSV upload + grid + constraint selector · background solve via RQ · job status polling · result grid + CSV download · basic dashboard/history.

**Deferred to product:** self-serve signup & ward creation · billing/PromptPay + activating `LICENSE_REGISTRY`/`TIER_LIMITS` as DB-backed subscriptions · admin UI · nicer styling/branding · per-tier queue priority · audit logs · S3 storage · optional in-memory solver result path.

---

## 16. Path from MVP to product

Nothing here is a throwaway. Converting means **adding**, not rewriting:

1. Replace the email allowlist with **open Google signup + ward invites** (model already supports it).
2. Move tiers/licenses from the in-code dicts into **DB tables** and add **billing**; enforce at ward creation and on solve.
3. **SQLite → Postgres** (swap the SQLAlchemy URL + run migrations).
4. Add **per-tier concurrency/priority** on the existing queue.
5. Polish the grid UX and branding.

The solver, the `ScheduleInput` model, the job pipeline, and the auth mechanism all carry straight over.

---

## 17. Open questions / risks

- **Concurrency vs. cost:** one worker serializes solves. If several team members solve at once they queue. Fine for a small team; revisit worker count / machine size if it bites.
- **Solve-time UX:** even capped, a solve can take a minute+. The job page must make waiting obvious (spinner + elapsed time). Consider a shorter default `max_time_seconds` for interactive use.
- **`enable_meetings` gating** is tier-controlled and therefore read-only in the selector — confirm that's the intended behavior for the team MVP (vs. always-on internally).
- **Ward setup for the MVP:** simplest is one shared internal ward for the whole team; confirm whether the team needs multiple wards on day one.

---

_No code has been written. This document is the design to review and adjust before implementation._
