"""
billing.py
----------
Owner-facing billing routes.

GET  /owner/billing              — billing page (tier badge, QR, history)
GET  /owner/billing/qr/{inv_id}  — serve QR PNG (validates ownership)
"""

import inspect
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import get_current_user
from app.core.config import STATIC_DIR, TEMPLATES_DIR, settings
from app.services.billing_service import billing_service
from app.api.owner import _auth_redirect, _get_owner_slug

router    = APIRouter(prefix="/owner", include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["support_email"] = settings.SUPPORT_EMAIL


def _r(request: Request, template: str, ctx: dict = {}):
    sig   = inspect.signature(templates.TemplateResponse)
    first = list(sig.parameters.keys())[0]
    if first == "request":
        return templates.TemplateResponse(request, template, ctx)
    return templates.TemplateResponse(template, {"request": request, **ctx})


# ── Billing page ──────────────────────────────────────────────────────────

@router.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug            = _get_owner_slug(request)
    billing_entry   = billing_service.get_entry(slug)
    billing_status  = billing_service.get_status(slug)

    return _r(request, "owner/billing.html", {
        "user":           get_current_user(request),
        "billing_entry":  billing_entry,
        "billing_status": billing_status,
        "upi_vpa":        settings.UPI_VPA,
    })


# ── Serve QR image ────────────────────────────────────────────────────────

@router.post("/billing/qr/regenerate/{invoice_id}")
def regenerate_qr_endpoint(request: Request, invoice_id: str):
    """
    Owner-triggered QR regeneration.
    Useful when the QR was missing at invoice creation (library not installed)
    or was wiped by an HF Spaces restart.
    """
    redir = _auth_redirect(request)
    if redir:
        return redir

    safe_id = Path(invoice_id).name
    if not safe_id or safe_id != invoice_id:
        return HTMLResponse("Invalid invoice ID", status_code=400)

    slug  = _get_owner_slug(request)
    entry = billing_service.get_entry(slug)
    inv   = next((i for i in entry.invoices if i.id == safe_id), None)
    if inv is None:
        return HTMLResponse("Invoice not found", status_code=404)

    billing_service.regenerate_qr(slug, safe_id)
    return RedirectResponse(url="/owner/billing?qr_regenerated=1", status_code=303)


@router.get("/billing/qr/{invoice_id}")
def serve_qr(request: Request, invoice_id: str):
    """
    Serve the QR PNG for an invoice.
    Validates that the invoice belongs to the requesting owner's profile.
    Regenerates the QR from the stored UPI URI if the file is missing
    (e.g. after an HF Spaces restart that wiped static/).
    """
    redir = _auth_redirect(request)
    if redir:
        return redir

    # Sanitise: only allow bare IDs (no path separators)
    safe_id = Path(invoice_id).name
    if not safe_id or safe_id != invoice_id:
        return HTMLResponse("Invalid invoice ID", status_code=400)

    slug  = _get_owner_slug(request)
    entry = billing_service.get_entry(slug)

    # Confirm invoice belongs to this owner
    inv = next((i for i in entry.invoices if i.id == safe_id), None)
    if inv is None:
        return HTMLResponse("Invoice not found", status_code=404)

    qr_file = STATIC_DIR / inv.qr_path
    if not qr_file.exists():
        # Lazy regeneration (HF Spaces restart / first-time QR failure)
        billing_service.regenerate_qr(slug, safe_id)

    if not qr_file.exists():
        return HTMLResponse("QR image not available", status_code=404)

    return FileResponse(str(qr_file), media_type="image/png")
