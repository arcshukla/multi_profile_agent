"""
dependencies.py
---------------
FastAPI dependency functions for route-level access control.

Session payload stored in request.session["user"]:
  {
    "email": "user@example.com",
    "name":  "Jane Doe",
    "role":  "admin" | "owner",
    "slug":  "profile-slug"   # only for role=owner
  }
"""

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def get_current_user(request: Request) -> dict | None:
    """Return the session user dict, or None if not logged in."""
    return request.session.get("user")


def require_admin(request: Request):
    """
    FastAPI dependency — allows only admin-role users.
    Redirects to /login if not authenticated or not admin.
    """
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        logger.debug("require_admin: access denied for %s", user)
        return RedirectResponse(url="/login", status_code=302)
    return user


def require_owner(request: Request):
    """
    FastAPI dependency — allows owner or admin.
    Redirects to /login if not authenticated.
    """
    user = get_current_user(request)
    if not user or user.get("role") not in ("owner", "admin"):
        logger.debug("require_owner: access denied for %s", user)
        return RedirectResponse(url="/login", status_code=302)
    return user
