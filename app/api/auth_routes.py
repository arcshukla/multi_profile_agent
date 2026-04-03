"""
auth_routes.py
--------------
Authentication routes: login, Google OAuth callback, logout,
self-registration flow, and the public profile directory.

Extracted from app/main.py to keep main.py focused on app wiring.
"""

import httpx

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings, TEMPLATES_DIR
from app.core.logging_config import get_logger
from app.auth.google import redirect_to_google, handle_callback
from app.auth.dependencies import get_current_user
from app.services.user_service import user_service
from app.services.profile_service import profile_service
from app.models.profile_models import CreateProfileRequest
from app.storage.file_storage import ProfileFileStorage
from app.services.notification_service import notification_service
from app.utils.template_utils import render

logger    = get_logger(__name__)
router    = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["support_email"] = settings.SUPPORT_EMAIL

import re as _re
templates.env.filters["md_bold"] = lambda t: _re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", t or "")


def _r(request: Request, template: str, ctx: dict = None):
    return render(templates, request, template, ctx)


# ── Login / logout ────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        dest = "/owner/dashboard" if user["role"] == "owner" else "/admin"
        return RedirectResponse(url=dest, status_code=302)
    return _r(request, "auth/login.html", {})


@router.get("/auth/google")
async def auth_google(request: Request):
    return await redirect_to_google(request)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    google_user = await handle_callback(request)
    if not google_user:
        return _r(request, "auth/login.html", {"error": "Google sign-in failed. Please try again."})

    session_user = user_service.resolve_session(google_user["email"], google_user["name"])
    if not session_user:
        # Unknown user — send to self-registration form
        request.session["pending_registration"] = {
            "email":   google_user["email"],
            "name":    google_user["name"],
            "picture": google_user.get("picture", ""),
        }
        return RedirectResponse(url="/register", status_code=302)

    request.session["user"] = session_user
    logger.info("Login: %s (role=%s)", session_user["email"], session_user["role"])

    if session_user["role"] == "owner":
        return RedirectResponse(url="/owner/dashboard", status_code=302)
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/auth/logout")
def auth_logout(request: Request):
    user = get_current_user(request)
    if user:
        logger.info("Logout: %s", user.get("email"))
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/owner")
def owner_root():
    return RedirectResponse(url="/owner/dashboard", status_code=302)


# ── Root redirect ─────────────────────────────────────────────────────────────

@router.get("/")
def root(request: Request):
    user = get_current_user(request)
    if user:
        dest = "/owner/dashboard" if user["role"] == "owner" else "/admin"
        return RedirectResponse(url=dest, status_code=302)
    return RedirectResponse(url="/explore", status_code=302)


# ── Public profile directory ──────────────────────────────────────────────────

@router.get("/explore", response_class=HTMLResponse)
def explore(request: Request, q: str = ""):
    """Public profile directory — no auth required."""
    profiles = profile_service.list_profiles(status_filter="enabled")
    if q:
        ql = q.lower()
        profiles = [p for p in profiles if ql in p.name.lower() or ql in p.slug.lower()]
    return _r(request, "explore.html", {
        "user":     get_current_user(request),
        "profiles": profiles,
        "q":        q,
    })


# ── Self-registration ─────────────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    """Show self-registration form after Google sign-in for unknown users."""
    pending = request.session.get("pending_registration")
    if not pending:
        return RedirectResponse(url="/explore", status_code=302)
    return _r(request, "auth/register.html", {"pending": pending})


@router.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request, name: str = Form(...)):
    """Create a new profile + owner account from self-registration."""
    pending = request.session.get("pending_registration")
    if not pending:
        return RedirectResponse(url="/explore", status_code=302)

    email   = pending["email"]
    picture = pending.get("picture", "")
    name    = name.strip()

    # Guard: if this email already has a user record (e.g. double-submit), go straight to dashboard
    existing = user_service.get_user(email)
    if existing:
        logger.warning(
            "register_submit: email %s already registered (slug=%s) — skipping duplicate",
            email, existing.slug,
        )
        request.session.pop("pending_registration", None)
        request.session["user"] = {
            "email": email, "name": existing.name or name, "role": "owner", "slug": existing.slug,
        }
        return RedirectResponse(url="/owner/dashboard", status_code=303)

    # 1. Create profile + register owner in one call (disabled — admin must approve)
    profile = profile_service.create_profile(
        CreateProfileRequest(name=name, owner_email=email, status="disabled")
    )

    # 2. Import Google profile picture (best-effort, non-critical)
    if picture:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(picture, timeout=5.0)
            if resp.status_code == 200:
                ProfileFileStorage(profile.slug).save_photo(resp.content)
        except Exception:
            pass

    # 3. Notify admin
    notification_service.notify_new_registration(name=name, email=email, slug=profile.slug)

    # 4. Log in the new owner and clear pending state
    request.session.pop("pending_registration", None)
    request.session["user"] = {"email": email, "name": name, "role": "owner", "slug": profile.slug}
    logger.info("New self-registration: %s (%s) → /chat/%s", name, email, profile.slug)

    return RedirectResponse(url="/owner/dashboard", status_code=303)
