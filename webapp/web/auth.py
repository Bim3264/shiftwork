"""Authentication + tenancy for the web layer.

Google OIDC via Authlib, a signed session cookie holding the user id, an
allowlist gate, and the ward-scoping helpers every data route leans on.

Design notes:
  - ``install_auth(app)`` is the single wiring point: it adds the session
    middleware, registers the Google OAuth client, mounts the auth router, and
    installs the redirect-on-missing-session exception handler.
  - ``require_user`` is a FastAPI dependency. When there is no valid session it
    raises ``AuthRedirect``, which the handler turns into a 302 to ``/login`` so
    protected routes short-circuit cleanly (and can be overridden in tests).
  - ``is_email_allowed`` reads ``config`` at call time (never binds the values at
    import) so tests can monkeypatch the allowlist. Both config values empty ⇒
    dev mode: any authenticated Google account is allowed.
  - Tenancy: MVP uses one shared internal ward. ``get_or_create_default_ward``
    makes it on first login and adds a ``WardMember``; ``user_ward_ids`` /
    ``ensure_ward_access`` enforce that a resource's ward belongs to the user.
"""

from __future__ import annotations

import html

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from webapp import config
from webapp.db.models import User, Ward, WardMember
from webapp.db.session import SessionLocal

DEFAULT_WARD_NAME = "Shared Ward"

# One OAuth registry for the process; `install_auth` registers the google client
# into it (guarded so a second call is a no-op).
oauth = OAuth()


# ── DB dependency ─────────────────────────────────────────────────────────────
def get_db():
    """Yield a DB session. Overridden in tests to point at a temp database."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Auth on/off ───────────────────────────────────────────────────────────────
def auth_is_off() -> bool:
    """True when login is disabled — either AUTH_ENABLED=false (a deliberate
    deployment choice) or DEV_AUTH_BYPASS (local dev). In this mode every request
    runs as a single shared local user and Google OAuth is not used.

    Read at call time so it is overridable/testable."""
    return config.DEV_AUTH_BYPASS or not config.AUTH_ENABLED


# ── Allowlist gate ────────────────────────────────────────────────────────────
def is_email_allowed(email: str) -> bool:
    """True if `email` may use the app under the MVP allowlist policy.

    Allowed when the email is in ``ALLOWED_EMAILS`` or its domain matches
    ``ALLOWED_DOMAIN``. If BOTH config values are empty this is dev mode and any
    authenticated Google account is allowed.
    """
    if not email:
        return False
    email = email.strip().lower()
    allowed_emails = config.ALLOWED_EMAILS
    allowed_domain = config.ALLOWED_DOMAIN
    if not allowed_emails and not allowed_domain:
        return True  # dev mode: no gate configured
    if email in allowed_emails:
        return True
    if allowed_domain and email.endswith("@" + allowed_domain):
        return True
    return False


# ── Redirect-to-login plumbing ────────────────────────────────────────────────
class AuthRedirect(Exception):
    """Raised by `require_user` when no valid session exists."""

    def __init__(self, location: str = "/login"):
        self.location = location
        super().__init__(location)


async def _auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(url=exc.location, status_code=302)


DEV_USER_SUB = "dev-local-bypass"


def _get_or_create_dev_user(db: Session) -> User:
    """Return the local dev user (creating it on first use). Only reached when
    DEV_AUTH_BYPASS is on."""
    user = db.execute(
        select(User).where(User.google_sub == DEV_USER_SUB)
    ).scalars().first()
    if user is None:
        user = User(
            google_sub=DEV_USER_SUB, email=config.DEV_USER_EMAIL, name="Local Dev"
        )
        db.add(user)
        db.flush()
        db.commit()
    get_or_create_default_ward(db, user)
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the logged-in `User`, or short-circuit to /login via AuthRedirect."""
    # When auth is turned off (AUTH_ENABLED=false or DEV_AUTH_BYPASS), skip
    # Google entirely and run as a single shared local user.
    if auth_is_off():
        return _get_or_create_dev_user(db)

    user_id = request.session.get("user_id")
    if not user_id:
        raise AuthRedirect("/login")
    user = db.get(User, user_id)
    if user is None:
        request.session.clear()
        raise AuthRedirect("/login")
    return user


# ── Tenancy helpers ───────────────────────────────────────────────────────────
def get_or_create_default_ward(db: Session, user: User) -> Ward:
    """Return the shared internal ward, creating it (and the user's membership)
    on first use. MVP: one ward everyone allowlisted shares."""
    ward = db.execute(
        select(Ward).where(Ward.name == DEFAULT_WARD_NAME)
    ).scalars().first()
    if ward is None:
        ward = Ward(name=DEFAULT_WARD_NAME, hospital="", created_by=user.id)
        db.add(ward)
        db.flush()
    member = db.execute(
        select(WardMember).where(
            WardMember.ward_id == ward.id, WardMember.user_id == user.id
        )
    ).scalars().first()
    if member is None:
        db.add(WardMember(ward_id=ward.id, user_id=user.id, role="member"))
    db.commit()
    return ward


def user_ward_ids(db: Session, user: User) -> list[int]:
    """The ward ids this user may access."""
    return list(
        db.execute(
            select(WardMember.ward_id).where(WardMember.user_id == user.id)
        ).scalars().all()
    )


def ensure_ward_access(db: Session, user: User, ward_id: int) -> bool:
    """True if `user` may access `ward_id`."""
    return ward_id in user_ward_ids(db, user)


# ── OAuth routes ──────────────────────────────────────────────────────────────
router = APIRouter()


@router.get("/login")
async def login(request: Request):
    # With auth off there is no Google client; send them to the app.
    if auth_is_off():
        return RedirectResponse(url="/", status_code=303)
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        user_info = await oauth.google.userinfo(token=token)

    google_sub = user_info.get("sub")
    email = (user_info.get("email") or "").strip().lower()
    name = user_info.get("name") or ""

    if not is_email_allowed(email):
        return HTMLResponse(
            _forbidden_page(email), status_code=403
        )

    user = db.execute(
        select(User).where(User.google_sub == google_sub)
    ).scalars().first()
    if user is None:
        user = User(google_sub=google_sub, email=email, name=name)
        db.add(user)
        db.flush()
    else:
        # Keep the profile fresh on each login.
        user.email = email
        user.name = name
    db.commit()

    # First-login provisioning: shared ward + membership.
    get_or_create_default_ward(db, user)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _forbidden_page(email: str) -> str:
    # Escape the email before reflecting it into hand-built HTML. Google-verified
    # addresses are constrained, but reflecting any external value unescaped is a
    # bad habit and an XSS foothold, so never do it.
    safe_email = html.escape(email) if email else "you signed in with"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Access denied</title></head><body>"
        "<h1>Access denied</h1>"
        f"<p>The account <strong>{safe_email}</strong> "
        "is not on the allowlist for this app.</p>"
        "<p>Ask an administrator to add you, then "
        "<a href='/login'>try again</a>.</p>"
        "</body></html>"
    )


def install_auth(app) -> None:
    """Wire auth into the app: session middleware, OAuth client, router, handler."""
    if auth_is_off():
        import sys
        print(
            "WARNING: authentication is DISABLED (AUTH_ENABLED=false or "
            "DEV_AUTH_BYPASS). Google sign-in is off and every request runs as a "
            "single shared user. Anyone who can reach this URL has full access — "
            "only run like this behind a trusted network or temporarily.",
            file=sys.stderr,
        )

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SESSION_SECRET,
        https_only=config.SESSION_HTTPS_ONLY,
        same_site="lax",  # sent on the top-level OAuth callback redirect
    )

    # Register the Google client only when auth is on. `oauth.google` is None
    # until registered; guard so a repeat call is a no-op.
    if not auth_is_off() and oauth.create_client("google") is None:
        oauth.register(
            name="google",
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            server_metadata_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile"},
        )

    app.add_exception_handler(AuthRedirect, _auth_redirect_handler)
    app.include_router(router)
