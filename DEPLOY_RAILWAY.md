# Deploying ShiftWork to Railway

Two phases: get it live cheaply first (web + Postgres, solves run inline — no
Redis), then add Redis + a worker when you need solves to run in the background.
The code already supports both via env flags; nothing to rewrite between phases.

Prerequisite: push this repo to GitHub (Railway deploys from a Git repo). The
deploy files are already here: `requirements.txt`, `Procfile`, `.python-version`.

---

## Phase 1 — web + Postgres (free-ish, no Redis)

### 1. Create the project
1. Go to railway.app → **New Project → Deploy from GitHub repo** → pick the ShiftWork repo.
2. Railway auto-detects Python, installs `requirements.txt`, pins Python 3.12
   (`.python-version`), and starts the `web` process from the `Procfile`.

### 2. Add a database
1. In the project: **New → Database → PostgreSQL**.
2. Railway creates a `DATABASE_URL` for that DB. You'll reference it in step 3.
   The app auto-creates its tables on first start.

### 3. Set the web service's variables
Open the web service → **Variables** and add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference the Postgres service) |
| `SESSION_SECRET` | a long random string (e.g. `python -c "import secrets;print(secrets.token_hex(32))"`) |
| `SESSION_HTTPS_ONLY` | `1` |
| `SOLVE_INLINE` | `1` (Phase 1: solve in-process, no Redis) |
| `GOOGLE_CLIENT_ID` | from step 5 |
| `GOOGLE_CLIENT_SECRET` | from step 5 |
| `ALLOWED_DOMAIN` | your team's Google Workspace domain (e.g. `yourhospital.org`) — or use `ALLOWED_EMAILS` = comma-separated addresses |

Do **not** set `DEV_AUTH_BYPASS` in production.

### 4. Get your public URL
Web service → **Settings → Networking → Generate Domain**. You'll get something
like `https://shiftwork-production.up.railway.app`. Optionally set the
**Healthcheck Path** to `/healthz`.

### 5. Create Google OAuth credentials
1. Google Cloud Console → **APIs & Services → Credentials → Create Credentials →
   OAuth client ID → Web application**.
2. **Authorized redirect URI**: `https://<your-railway-domain>/auth/callback`
   (exactly — must match, and it's https).
3. Copy the Client ID and Client Secret into the Railway variables from step 3.
4. Redeploy the web service.

Visit your domain → sign in with Google. You're live. Solves will take a few
seconds (they run inline) — fine for a small team.

---

## Phase 2 — add Redis + a worker (background solves, scales)

Do this when inline solves feel slow or several people solve at once.

1. **New → Database → Redis** in the same project.
2. **New → GitHub Repo → (same repo)** to create a **second service** (the worker).
   In that service's **Settings → Deploy → Custom Start Command**, set:
   `python -m webapp.worker.run_worker`
3. Give the **worker** service the same variables as the web service
   (`DATABASE_URL`, `GOOGLE_*` not needed, `SESSION_SECRET` not needed) plus:
   `REDIS_URL = ${{Redis.REDIS_URL}}`.
   The worker really only needs `DATABASE_URL` and `REDIS_URL`.
4. On the **web** service: add `REDIS_URL = ${{Redis.REDIS_URL}}` and change
   `SOLVE_INLINE` to `0`.
5. Redeploy both. Now clicking Solve queues a job; the worker runs it and the job
   page updates when it's done.

To scale further: increase the worker service's replicas (each solve uses all
CPU on its instance, so scale by adding worker instances / larger instances), and
bump `WORKER_JOB_TIMEOUT_SECONDS` / `SOLVER_MAX_TIME_SECONDS` together if wards
need longer optimization (keep solver < worker).

---

## Notes & gotchas

- **HTTPS/OAuth:** the `web` start command already passes `--proxy-headers` so the
  app sees Railway's HTTPS and builds the correct `https://.../auth/callback`
  redirect. If Google rejects the redirect, re-check the URI matches your domain
  exactly.
- **Secrets:** never commit real secrets; set them as Railway variables. Rotate
  `SESSION_SECRET` only when you're OK logging everyone out.
- **Logs:** errors are written under `logs/` in the service (see the deploy logs
  in Railway, or the file). A crash traceback lands there.
- **Cost:** Railway runs on a monthly credit; the web + Postgres are light, but an
  always-on worker consumes credit. Staying in Phase 1 (inline) is cheapest until
  you need concurrency.
