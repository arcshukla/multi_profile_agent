"""
owner.py
--------
Owner portal routes.

All routes require the caller to be authenticated as role=owner or admin.
The profile slug is always taken from the session (never a URL param) so an
owner can only ever touch their own profile.

An admin visiting /owner/* is served the same view — slug comes from their
session["slug"] if set, otherwise they are redirected to /admin.
"""

import io
from datetime import date
from pathlib import Path
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.core.config import TEMPLATES_DIR, settings
from app.services.billing_service      import billing_service
from app.core.logging_config           import get_logger
from app.auth.dependencies             import get_current_user
from app.services.profile_service     import profile_service
from app.services.prompt_service      import prompt_service
from app.services.index_service       import index_service
from app.services.token_service       import token_service
from app.services.preferences_service import preferences_service
from app.services.user_service        import user_service
from app.core.constants            import (
    ALLOWED_DOC_EXTENSIONS,
    MAX_FILE_SIZE_PDF, MAX_FILE_SIZE_OTHER, MAX_DOCS_PER_PROFILE,
)
from app.storage.file_storage      import ProfileFileStorage
from app.rag.default_prompts       import REQUIRED_PLACEHOLDERS, LOCKED_PROMPT_SUFFIXES

logger    = get_logger(__name__)
router    = APIRouter(prefix="/owner", include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals['support_email'] = settings.SUPPORT_EMAIL


# ── Helpers ────────────────────────────────────────────────────────────────────

def _r(request: Request, template: str, ctx: dict = {}):
    import inspect
    sig = inspect.signature(templates.TemplateResponse)
    first = list(sig.parameters.keys())[0]
    if first == "request":
        return templates.TemplateResponse(request, template, ctx)
    return templates.TemplateResponse(template, {"request": request, **ctx})


def _get_owner_slug(request: Request) -> str | None:
    """
    Return the profile slug for the current user.
    Returns None if not authenticated or no slug assigned (e.g. admin with no slug).
    """
    user = get_current_user(request)
    if not user:
        return None
    return user.get("slug")


def _auth_redirect(request: Request):
    """Return a redirect if the user is not authorised for the owner portal."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.get("role") == "admin" and not user.get("slug"):
        # Admin with no assigned profile → send back to admin UI
        return RedirectResponse(url="/admin", status_code=302)
    return None


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug    = _get_owner_slug(request)
    profile = profile_service.get_profile(slug)
    status  = index_service.get_status(slug)
    usage   = token_service.get_all().get(slug, {})

    return _r(request, "owner/dashboard.html", {
        "user":           get_current_user(request),
        "profile":        profile,
        "status":         status,
        "usage":          usage,
        "billing_entry":  billing_service.get_entry(slug),
        "billing_status": billing_service.get_status(slug),
    })


# ── Docs ───────────────────────────────────────────────────────────────────────

@router.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    fs   = ProfileFileStorage(slug)
    docs = fs.list_documents()

    return _r(request, "owner/docs.html", {
        "user":    get_current_user(request),
        "slug":    slug,
        "documents": [
            {"name": d.name, "size_kb": round(d.stat().st_size / 1024, 1)}
            for d in docs
        ],
    })


@router.post("/docs/upload", response_class=HTMLResponse)
async def docs_upload(request: Request, file: UploadFile = File(...)):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    fs   = ProfileFileStorage(slug)

    # ── Validate file count ───────────────────────────────────────────────────
    existing = fs.list_documents()
    if len(existing) >= MAX_DOCS_PER_PROFILE:
        return RedirectResponse(
            url=f"/owner/docs?upload_error=max_files", status_code=303
        )

    # ── Validate file size ────────────────────────────────────────────────────
    data = await file.read()
    ext  = Path(file.filename).suffix.lower()
    max_size  = MAX_FILE_SIZE_PDF if ext == ".pdf" else MAX_FILE_SIZE_OTHER
    limit_mb  = max_size // (1024 * 1024)
    if len(data) > max_size:
        return RedirectResponse(
            url=f"/owner/docs?upload_error=too_large&ext={ext.lstrip('.')}&limit={limit_mb}",
            status_code=303,
        )

    # ── Save ──────────────────────────────────────────────────────────────────
    try:
        fs.save_document(file.filename, data)
        logger.info("Owner upload: slug=%s file=%s size=%d", slug, file.filename, len(data))
    except ValueError as e:
        return RedirectResponse(
            url=f"/owner/docs?upload_error=invalid&msg={e}", status_code=303
        )

    return RedirectResponse(url="/owner/docs?reindex=1", status_code=303)


@router.post("/docs/delete/{filename}", response_class=HTMLResponse)
async def docs_delete(request: Request, filename: str):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    ProfileFileStorage(slug).delete_document(filename)
    return RedirectResponse(url="/owner/docs?reindex=1", status_code=303)


@router.get("/docs/view/{filename}")
async def docs_view(request: Request, filename: str):
    """Serve an uploaded document for in-browser preview (PDF / TXT)."""
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    fs   = ProfileFileStorage(slug)
    # Sanitise: strip any path separators — only the bare filename is allowed
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = fs.docs_dir / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Only allow extensions the platform already accepts
    if path.suffix.lower() not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(status_code=403, detail="File type not viewable")
    # CSV and plain-text formats are served as text/plain so the browser renders them inline
    media_map = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md":  "text/plain",
        ".csv": "text/plain",
    }
    ext = path.suffix.lower()
    if ext not in media_map:
        raise HTTPException(status_code=403, detail="File type not viewable")
    media_type = media_map[ext]
    # No Content-Disposition header → browser uses the media type to decide how to handle the file.
    # application/pdf and text/plain are displayed inline by all modern browsers.
    return FileResponse(str(path), media_type=media_type)


# ── Appearance (header + CSS) ─────────────────────────────────────────────────

@router.get("/appearance", response_class=HTMLResponse)
def appearance_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    fs   = ProfileFileStorage(slug)
    has_photo = fs.has_photo()
    photo_ts  = int(fs.photo_path.stat().st_mtime) if has_photo else 0
    return _r(request, "owner/appearance.html", {
        "user":        get_current_user(request),
        "slug":        slug,
        "has_photo":   has_photo,
        "photo_ts":    photo_ts,
        "slides_data": fs.read_slides(),
        "profile_css": fs.read_css(),
    })


@router.post("/appearance/slides")
async def save_slides(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir
    import html as html_mod
    form = await request.form()
    slides = []
    i = 0
    while f"type_{i}" in form:
        slide_type = form.get(f"type_{i}", "standard")
        if slide_type == "quote":
            slides.append({
                "type": "quote",
                "quote":       form.get(f"quote_{i}", "").strip(),
                "attribution": form.get(f"attribution_{i}", "").strip(),
            })
        else:
            slides.append({
                "type":     "standard",
                "title":    form.get(f"title_{i}", "").strip(),
                "subtitle": form.get(f"subtitle_{i}", "").strip(),
                "body":     form.get(f"body_{i}", "").strip(),
            })
        i += 1
    # Drop completely empty slides; cap at 5
    slides = [s for s in slides if any(v for k, v in s.items() if k != "type")][:5]
    ProfileFileStorage(_get_owner_slug(request)).write_slides({"slides": slides})
    return HTMLResponse('<p class="text-green-600 text-sm font-medium">Slides saved.</p>')


@router.post("/appearance/css")
async def save_css(request: Request, content: str = Form(...)):
    redir = _auth_redirect(request)
    if redir:
        return redir
    ProfileFileStorage(_get_owner_slug(request)).write_css(content)
    return HTMLResponse('<p class="text-green-600 text-sm font-medium">CSS saved.</p>')


# ── Prompts ────────────────────────────────────────────────────────────────────

@router.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug = _get_owner_slug(request)
    prompts, _ = prompt_service.get_prompts(slug)
    return _r(request, "owner/prompts.html", {
        "user":    get_current_user(request),
        "prompts": prompts,
        "required": REQUIRED_PLACEHOLDERS,
        "locked":   LOCKED_PROMPT_SUFFIXES,
    })


@router.post("/prompts/{key}")
async def save_prompt(request: Request, key: str, content: str = Form(...)):
    redir = _auth_redirect(request)
    if redir:
        return redir

    required = REQUIRED_PLACEHOLDERS.get(key, [])
    missing  = [p for p in required if p not in content]
    if missing:
        return HTMLResponse(
            f'<p class="text-red-600 text-sm">Missing required placeholder(s): '
            f'{", ".join(f"<code>{m}</code>" for m in missing)}</p>',
            status_code=400,
        )

    slug = _get_owner_slug(request)
    ok   = prompt_service.update_prompt(slug, key, content)
    if not ok:
        return HTMLResponse('<p class="text-red-600 text-sm">Unknown prompt key.</p>', status_code=400)
    return HTMLResponse('<p class="text-green-600 text-sm font-medium">Saved.</p>')


# ── Photo ──────────────────────────────────────────────────────────────────────

@router.post("/photo")
async def upload_photo(request: Request, file: UploadFile = File(...)):
    redir = _auth_redirect(request)
    if redir:
        return redir
    slug = _get_owner_slug(request)
    data = await file.read()
    if not data:
        return HTMLResponse('<p class="text-red-600 text-sm font-medium">No file data received. Please select a file and try again.</p>', status_code=400)
    ProfileFileStorage(slug).save_photo(data)
    import time
    ts = int(time.time())
    oob_img = (
        f'<img id="photo-preview-img" hx-swap-oob="true" '
        f'src="/api/profiles/{slug}/photo?t={ts}" '
        f'alt="Profile photo" '
        f'class="w-24 h-24 rounded-full object-cover border-2 border-gray-200 shadow-sm"/>'
    )
    return HTMLResponse(
        f'<p class="text-green-600 text-sm font-medium">Photo updated successfully.</p>{oob_img}'
    )


# ── Status (enable / disable only — no delete for owners) ─────────────────────

@router.post("/status")
async def toggle_status(request: Request, status: str):
    redir = _auth_redirect(request)
    if redir:
        return redir
    if status not in ("enabled", "disabled"):
        return HTMLResponse('<p class="text-red-600 text-sm">Invalid status.</p>', status_code=400)
    slug = _get_owner_slug(request)
    profile_service.update_status(slug, status)
    if status == "enabled":
        btn = (
            '<button form="status-form" name="status" value="disabled" '
            'class="inline-flex items-center gap-1.5 text-xs font-medium bg-green-100 text-green-700 '
            'hover:bg-red-50 hover:text-red-600 px-3 py-1.5 rounded-full border border-green-200 '
            'hover:border-red-200 transition-colors">'
            '<span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>Live — click to disable</button>'
        )
    else:
        btn = (
            '<button form="status-form" name="status" value="enabled" '
            'class="inline-flex items-center gap-1.5 text-xs font-medium bg-gray-100 text-gray-600 '
            'hover:bg-green-50 hover:text-green-700 px-3 py-1.5 rounded-full border border-gray-200 '
            'hover:border-green-200 transition-colors">'
            '<span class="w-1.5 h-1.5 rounded-full bg-gray-400"></span>Disabled — click to enable</button>'
        )
    return HTMLResponse(f'<div id="status-btn">{btn}</div>')


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug   = _get_owner_slug(request)
    events = ProfileFileStorage(slug).read_chat_events(limit=200)
    return _r(request, "owner/analytics.html", {
        "user":   get_current_user(request),
        "events": events,
    })


# ── Analytics download ────────────────────────────────────────────────────────

@router.get("/analytics/download")
def analytics_download(request: Request):
    """Download all chat events as an Excel workbook."""
    redir = _auth_redirect(request)
    if redir:
        return redir

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl is not installed — add it to requirements.txt")

    slug   = _get_owner_slug(request)
    # Read all events (no display limit) in chronological order for analysis
    events = list(reversed(ProfileFileStorage(slug).read_chat_events(limit=100_000)))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Chat History"

    header_fill = PatternFill("solid", fgColor="EFF6FF")
    headers = ["Timestamp (UTC)", "Session ID", "Question", "Answer", "Tokens", "Answered"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
        cell.fill = header_fill

    wrap_top = Alignment(wrap_text=True, vertical="top")
    top      = Alignment(vertical="top")

    for row_idx, e in enumerate(events, 2):
        ws.cell(row=row_idx, column=1, value=e.get("ts", "")).alignment = top
        ws.cell(row=row_idx, column=2, value=e.get("session_id", "")).alignment = top
        ws.cell(row=row_idx, column=3, value=e.get("question", "")).alignment = wrap_top
        ws.cell(row=row_idx, column=4, value=e.get("answer", "")).alignment = wrap_top
        ws.cell(row=row_idx, column=5, value=e.get("tokens") or 0).alignment = top
        ws.cell(row=row_idx, column=6, value="Yes" if e.get("was_answered") else "No").alignment = top

    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 55
    ws.column_dimensions["D"].width = 70
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 12
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{slug}-chat-history-{date.today().isoformat()}.xlsx"
    logger.info("Analytics download: slug=%s events=%d file=%s", slug, len(events), filename)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── AI & Indexing (was: Tokens) ────────────────────────────────────────────────

@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir

    slug   = _get_owner_slug(request)
    usage  = token_service.get_all().get(slug, {})
    status = index_service.get_status(slug)
    return _r(request, "owner/ai.html", {
        "user":    get_current_user(request),
        "usage":   usage,
        "slug":    slug,
        "status":  status,
        "history": index_service.get_history(slug, limit=20),
    })


@router.get("/tokens", response_class=HTMLResponse)
def tokens_redirect(request: Request):
    """Backwards-compat redirect for old bookmarks."""
    return RedirectResponse(url="/owner/ai", status_code=301)


@router.post("/index")
async def owner_index(request: Request, background_tasks: BackgroundTasks):
    """Start indexing in background."""
    redir = _auth_redirect(request)
    if redir:
        return redir
    slug = _get_owner_slug(request)
    if not index_service.is_indexing(slug):
        background_tasks.add_task(index_service.index_profile, slug, False)
    return RedirectResponse(url="/owner/ai?indexing=1", status_code=303)


@router.post("/index/force")
async def owner_force_index(request: Request, background_tasks: BackgroundTasks):
    """Force full re-index (wipes existing index)."""
    redir = _auth_redirect(request)
    if redir:
        return redir
    slug = _get_owner_slug(request)
    if not index_service.is_indexing(slug):
        background_tasks.add_task(index_service.force_reindex, slug)
    return RedirectResponse(url="/owner/ai?indexing=1", status_code=303)


# ── Preferences ────────────────────────────────────────────────────────────────

@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir
    slug  = _get_owner_slug(request)
    user  = get_current_user(request)
    prefs = preferences_service.get(slug)
    return _r(request, "owner/preferences.html", {
        "user":        user,
        "slug":        slug,
        "prefs":       prefs,
        "saved":       False,
        "error":       None,
        "active_page": "preferences",
    })


@router.post("/preferences", response_class=HTMLResponse)
async def preferences_save(
    request: Request,
    name:                    str = Form(""),
    notify_unanswered_email: str = Form(""),   # checkbox: "on" when checked, absent otherwise
):
    redir = _auth_redirect(request)
    if redir:
        return redir
    slug  = _get_owner_slug(request)
    user  = get_current_user(request)

    new_name = name.strip()
    error    = None
    if new_name:
        ok, err = user_service.update_name(user["email"], new_name)
        if not ok:
            error = err
        else:
            request.session["user"]["name"] = new_name

    prefs = {
        "notify_unanswered_email": notify_unanswered_email == "on",
    }
    if error is None:
        preferences_service.save(slug, prefs)

    return _r(request, "owner/preferences.html", {
        "user":        get_current_user(request),
        "slug":        slug,
        "prefs":       prefs,
        "saved":       error is None,
        "error":       error,
        "active_page": "preferences",
    })
