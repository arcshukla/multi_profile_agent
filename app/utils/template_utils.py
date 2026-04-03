"""
template_utils.py
-----------------
Shared Jinja2 TemplateResponse helper.

Starlette changed the TemplateResponse signature in 0.36:
  < 0.36:  TemplateResponse(name, {"request": ..., ...})
  >= 0.36: TemplateResponse(request, name, context)

We detect which API to use by inspecting the signature at call time so the
codebase works with both old and new Starlette without version pinning.
"""

import inspect
import re
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_REGISTERED: set = set()


def _ensure_filters(templates: Jinja2Templates) -> None:
    """Register shared filters on a templates instance (idempotent)."""
    tid = id(templates)
    if tid in _REGISTERED:
        return
    templates.env.filters["md_bold"] = lambda t: re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", t or "")
    _REGISTERED.add(tid)


def render(templates: Jinja2Templates, request: Request, template: str, ctx: dict = None):
    """Version-safe wrapper around Jinja2Templates.TemplateResponse."""
    _ensure_filters(templates)
    ctx = ctx or {}
    sig = inspect.signature(templates.TemplateResponse)
    if list(sig.parameters.keys())[0] == "request":
        return templates.TemplateResponse(request, template, ctx)
    return templates.TemplateResponse(template, {"request": request, **ctx})


def htmx_ok(msg: str) -> HTMLResponse:
    """HTMX micro-response: green success paragraph."""
    return HTMLResponse(f'<p class="text-green-600 text-sm font-medium">{msg}</p>')


def htmx_err(msg: str, status: int = 400) -> HTMLResponse:
    """HTMX micro-response: red error paragraph."""
    return HTMLResponse(f'<p class="text-red-600 text-sm font-medium">{msg}</p>', status_code=status)
