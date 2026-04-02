
"""
main.py
-------
FastAPI application entry point.

Run locally:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 7860
Push for HF Space:
  git push space master:main --force
"""

from pathlib import Path
from typing import Optional
import json

from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse as _RedirectResponse

from app.core.config import settings, STATIC_DIR, TEMPLATES_DIR
from app.core.logging_config import get_logger, set_current_session_id

from app.api.profiles  import router as profiles_router
from app.api.documents import router as documents_router
from app.api.indexing  import router as indexing_router, history_router
from app.api.chat      import router as chat_router
from app.api.prompts   import router as prompts_router
from app.api.logs      import router as logs_router
from app.api.owner     import router as owner_router
from app.api.billing   import router as billing_router

from app.services.profile_service      import profile_service
from app.services.preferences_service  import preferences_service
from app.services.index_service        import index_service
from app.services.log_service          import log_service
from app.services.prompt_service       import prompt_service
from app.services.llm_prompts_service      import llm_prompts_service
from app.services.email_template_service   import email_template_service
from app.services.token_service        import token_service
from app.services.user_service         import user_service
from app.services.billing_service      import billing_service
from app.models.billing_models         import BillingTier
from app.storage.file_storage          import ProfileFileStorage
from app.auth.google                   import oauth, redirect_to_google, handle_callback
from app.auth.dependencies             import get_current_user, require_admin, require_owner
from app.utils.notifier                import notifier

logger = get_logger(__name__)

app = FastAPI(title="AI Profile Platform", version=settings.APP_VERSION)

class AdminAuthMiddleware(BaseHTTPMiddleware):
    """
    Guards all /admin/* routes.
    Requires role=admin regardless of environment — IS_LOCAL does not bypass this.
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/admin"):
            user = request.session.get("user")
            if not user or user.get("role") != "admin":
                return _RedirectResponse(url="/login", status_code=302)
        return await call_next(request)


class ActorContextMiddleware(BaseHTTPMiddleware):
    """
    Stamps every log record in this request with the logged-in user's email.
    Falls back to 'system' for unauthenticated requests.
    """
    async def dispatch(self, request: Request, call_next):
        user = request.session.get("user")
        if user and user.get("email"):
            set_current_session_id(user["email"])
        return await call_next(request)


# Middleware is applied in reverse-add order (last added = outermost = runs first).
# SessionMiddleware must be outermost so it populates request.session before
# the inner middlewares read it.
app.add_middleware(AdminAuthMiddleware)      # innermost — runs third
app.add_middleware(ActorContextMiddleware)   # middle    — runs second (session already populated)
app.add_middleware(                          # outermost — runs first, populates session
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    https_only=False,        # set True in production behind HTTPS
    max_age=60 * 60 * 6,     # 6-hour session
)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Custom Jinja2 filter: convert **bold** markdown to <strong> tags
import re as _re
templates.env.filters['md_bold'] = lambda t: _re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', t or '')
templates.env.globals['support_email'] = settings.SUPPORT_EMAIL

app.include_router(profiles_router)
app.include_router(documents_router)
app.include_router(indexing_router)
app.include_router(history_router)
app.include_router(chat_router)
app.include_router(prompts_router)
app.include_router(logs_router)
app.include_router(owner_router)
app.include_router(billing_router)


def _r(request: Request, template: str, ctx: dict = {}):
    """
    Starlette-version-safe TemplateResponse wrapper.

    Starlette < 0.36:  TemplateResponse(name, {"request": ..., ...})
    Starlette >= 0.36: TemplateResponse(request, name, context)

    We detect which API to use by inspecting the signature — no extra deps needed.
    """
    import inspect
    sig = inspect.signature(templates.TemplateResponse)
    first_param = list(sig.parameters.keys())[0]
    if first_param == "request":
        # New API (Starlette >= 0.36)
        return templates.TemplateResponse(request, template, ctx)
    else:
        # Old API
        return templates.TemplateResponse(template, {"request": request, **ctx})


# =============================================================================
# AUTH ROUTES
# =============================================================================

@app.get("/login", include_in_schema=False, response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/owner/dashboard" if user["role"] == "owner" else "/admin", status_code=302)
    return _r(request, "auth/login.html", {})


@app.get("/auth/google", include_in_schema=False)
async def auth_google(request: Request):
    return await redirect_to_google(request)


@app.get("/auth/callback", name="auth_callback", include_in_schema=False)
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


@app.get("/owner", include_in_schema=False)
def owner_root():
    return RedirectResponse(url="/owner/dashboard", status_code=302)


@app.get("/auth/logout", include_in_schema=False)
def auth_logout(request: Request):
    user = get_current_user(request)
    if user:
        logger.info("Logout: %s", user.get("email"))
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/owner/dashboard" if user["role"] == "owner" else "/admin", status_code=302)
    return RedirectResponse(url="/explore", status_code=302)


# =============================================================================
# PUBLIC DIRECTORY + SELF-REGISTRATION
# =============================================================================

@app.get("/explore", include_in_schema=False, response_class=HTMLResponse)
def explore(request: Request, q: str = ""):
    """Public profile directory — no auth required."""
    profiles = profile_service.list_profiles(status_filter="enabled")
    if q:
        ql = q.lower()
        profiles = [p for p in profiles if ql in p.name.lower() or ql in p.slug.lower()]
    return _r(request, "explore.html", {
        "user":     get_current_user(request),
        "profiles": profiles,
    })

@app.post("/register", include_in_schema=False, response_class=HTMLResponse)
async def register_submit(request: Request, name: str = Form(...)):
    """Create a new profile + owner account from self-registration."""
    pending = request.session.get("pending_registration")
    if not pending:
        return RedirectResponse(url="/explore", status_code=302)

    from app.models.profile_models import CreateProfileRequest
    import httpx

    email   = pending["email"]
    picture = pending.get("picture", "")
    name    = name.strip()

    # Guard: if this email already has a user record (e.g. double-submit), go straight to dashboard
    existing = user_service.get_user(email)
    if existing:
        logger.warning("register_submit: email %s already registered (slug=%s) — skipping duplicate", email, existing.slug)
        request.session.pop("pending_registration", None)
        request.session["user"] = {"email": email, "name": existing.name or name, "role": "owner", "slug": existing.slug}
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
    notifier.notify_new_registration(name=name, email=email, slug=profile.slug)

    # 4. Log in the new owner and clear pending state
    request.session.pop("pending_registration", None)
    request.session["user"] = {"email": email, "name": name, "role": "owner", "slug": profile.slug}
    logger.info("New self-registration: %s (%s) → /chat/%s", name, email, profile.slug)

    return RedirectResponse(url="/owner/dashboard", status_code=303)


# =============================================================================
# ADMIN UI ROUTES
# =============================================================================

@app.get("/admin", include_in_schema=False, response_class=HTMLResponse)
@app.get("/admin/registry", include_in_schema=False, response_class=HTMLResponse)
def admin_registry(request: Request):
    return _r(request, "admin/layout.html", {
        "active_tab": "registry",
        "tab_content_template": "admin/tab_registry.html",
        "current_user": get_current_user(request),
    })


@app.get("/admin/manage", include_in_schema=False, response_class=HTMLResponse)
def admin_manage_list(request: Request):
    return RedirectResponse(url="/admin/registry")


@app.get("/admin/manage/{slug}", include_in_schema=False, response_class=HTMLResponse)
def admin_manage_profile(request: Request, slug: str):
    from fastapi.responses import HTMLResponse
    from fastapi import Depends

    @app.post("/admin/manage/{slug}/preferences", include_in_schema=False, response_class=HTMLResponse)
    async def admin_update_owner_preferences(
        request: Request,
        slug: str,
        owner_email: str = Form(...),
        name: str = Form(""),
        notify_unanswered_email: str = Form(None),
        current_user: dict = Depends(require_admin),
    ):
        # Find current owner user by slug
        owner_user = user_service.get_user_by_slug(slug)
        prefs = preferences_service.get(slug)
        error = None
        saved = False
        if not owner_user:
            error = "Owner user not found for this profile."
        else:
            old_email = owner_user.email
            new_email = owner_email.strip().lower()
            # Update email if changed
            if new_email and new_email != old_email:
                ok, err = user_service.update_email(old_email, new_email)
                if not ok:
                    error = err
            # Update name
            if not error:
                ok, err = user_service.update_name(new_email, name.strip())
                if not ok:
                    error = err
            # Update preferences
            if not error:
                prefs = {
                    "notify_unanswered_email": notify_unanswered_email == "on",
                }
                preferences_service.save(slug, prefs)
                saved = True
                # Re-fetch owner_user for re-render (slug unchanged after email update)
                owner_user = user_service.get_user_by_slug(slug)

        # Re-render only the preferences form partial for HTMX swap
        return _r(request, "admin/partials/owner_prefs_form.html", {
            "profile": profile_service.get_profile(slug),
            "owner_user": owner_user,
            "prefs": prefs,
            "owner_prefs_saved": saved,
            "owner_prefs_error": error,
        })
    profile = profile_service.get_profile(slug)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    fs = ProfileFileStorage(slug)
    prompts, is_default = prompt_service.get_prompts(slug)
    owner_user = user_service.get_user_by_slug(slug)
    prefs = preferences_service.get(slug)

    return _r(request, "admin/layout.html", {
        "active_tab":         "manage",
        "profile":            profile,
        "slides_data":        fs.read_slides(),
        "profile_css":        fs.read_css(),
        "prompts":            prompts,
        "prompts_is_default": is_default,
        "billing_entry":      billing_service.get_entry(slug),
        "billing_status":     billing_service.get_status(slug),
        "tab_content_template": "admin/tab_manage.html",
        "current_user": get_current_user(request),
        "owner_user":         owner_user,
        "prefs":              prefs,
        "owner_prefs_saved":  False,
        "owner_prefs_error":  None,
    })


@app.get("/admin/system", include_in_schema=False, response_class=HTMLResponse)
def admin_system(request: Request):
    return _r(request, "admin/layout.html", {
        "active_tab": "system",
        "tab_content_template": "admin/tab_system.html",
        "current_user": get_current_user(request),
    })


# ── HTMX partials ─────────────────────────────────────────────────────────────

@app.get("/admin/registry/profiles", include_in_schema=False, response_class=HTMLResponse)
def htmx_profiles_table(
    request: Request,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    status: Optional[str] = None,
):
    profiles = profile_service.list_profiles(
        status_filter=status or None,
        name_filter=name or None,
        slug_filter=slug or None,
    )
    if not status:
        profiles = [p for p in profiles if p.status != "deleted"]
    return _r(request, "admin/partials/profiles_table.html", {"profiles": profiles})


@app.post("/admin/registry/create", include_in_schema=False, response_class=HTMLResponse)
async def htmx_create_profile(
    request: Request,
    name: str = Form(...),
    owner_email: str = Form(...),
    status: str = Form("enabled"),
):
    from app.models.profile_models import CreateProfileRequest
    import json as _json
    error_msg = None
    try:
        profile_service.create_profile(
            CreateProfileRequest(name=name, owner_email=owner_email, status=status)
        )
    except ValueError as e:
        error_msg = str(e)

    profiles = [p for p in profile_service.list_profiles() if p.status != "deleted"]
    response = _r(request, "admin/partials/profiles_table.html", {"profiles": profiles})
    if error_msg:
        response.headers["HX-Trigger"] = _json.dumps({"showToast": {"message": error_msg, "type": "error"}})
    else:
        response.headers["HX-Trigger"] = _json.dumps({"showToast": {"message": "Profile created!", "type": "success"}})
    return response


@app.post("/admin/manage/{slug}/slides", include_in_schema=False)
async def save_slides_htmx(request: Request, slug: str):
    """Save carousel slides for a profile (called from manage tab form)."""
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, "Profile not found")
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
    slides = [s for s in slides if any(v for k, v in s.items() if k != "type")][:5]
    ProfileFileStorage(slug).write_slides({"slides": slides})
    return {"success": True}


@app.get("/admin/manage/{slug}/docs", include_in_schema=False, response_class=HTMLResponse)
def htmx_docs_list(request: Request, slug: str):
    from app.services.document_service import document_service
    result = document_service.list_documents(slug)
    return _r(request, "admin/partials/docs_list.html", {
        "slug": slug,
        "documents": result.documents,
    })


@app.get("/admin/manage/{slug}/index-status", include_in_schema=False, response_class=HTMLResponse)
def htmx_index_status(request: Request, slug: str):
    status = index_service.get_status(slug)
    return _r(request, "admin/partials/index_status.html", {
        "slug": slug,
        "status": status,
    })


@app.get("/admin/manage/{slug}/chunks", include_in_schema=False, response_class=HTMLResponse)
def htmx_chunks(request: Request, slug: str):
    """Return indexed chunks for a profile — shown in the advanced toggle."""
    engine = index_service.get_engine(slug)
    if not engine or engine.chunk_count() == 0:
        return HTMLResponse('<p class="text-xs text-gray-400 italic">No chunks indexed yet.</p>')

    result = engine.collection.get(include=["documents", "metadatas"])
    docs = result.get("documents", [])
    metas = result.get("metadatas", [])

    rows = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        topic = (meta or {}).get("topic", "—")
        source = (meta or {}).get("source", "—")
        preview = doc[:200].replace("<", "&lt;").replace(">", "&gt;")
        rows.append(
            f'<div class="border border-gray-100 rounded-lg p-3 space-y-1">'
            f'<div class="flex items-center gap-2">'
            f'<span class="text-xs font-mono bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">#{i}</span>'
            f'<span class="text-xs font-medium text-gray-700">{topic}</span>'
            f'<span class="text-xs text-gray-400 truncate">— {source}</span>'
            f'</div>'
            f'<p class="text-xs text-gray-600 font-mono leading-relaxed whitespace-pre-wrap">{preview}{"…" if len(doc) > 200 else ""}</p>'
            f'</div>'
        )

    html = f'<div class="space-y-2 max-h-96 overflow-y-auto mt-2">{"".join(rows)}</div>'
    return HTMLResponse(html)


@app.get("/admin/manage/{slug}/logs", include_in_schema=False, response_class=HTMLResponse)
def htmx_profile_logs(request: Request, slug: str):
    result = log_service.read_log(log_type="profile", slug=slug, tail=100)
    lines = result.get("lines", [])
    if not lines:
        return HTMLResponse('<p class="text-sm text-gray-400 font-mono">No log entries yet for this profile.</p>')
    content = "\n".join(lines)
    return HTMLResponse(
        f'<pre class="text-xs text-green-400 font-mono bg-gray-900 rounded-lg p-4 overflow-x-auto '
        f'whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">{content}</pre>'
    )


# ── System sub-tab partials ───────────────────────────────────────────────────
@app.get("/admin/system/billing", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_billing(
    request: Request,
    name: str = Query("", alias="name"),
    slug: str = Query("", alias="slug"),
    plan: str = Query("", alias="plan"),
    payment_status: str = Query("", alias="payment_status"),
):
    billing_path = Path("system/billing.json")
    billing_data = json.loads(billing_path.read_text(encoding="utf-8")) if billing_path.exists() else {}

    # Collect all plans
    plans_set = set()
    for b in billing_data.values():
        plans_set.add(b.get("tier", ""))
    plans = sorted([p for p in plans_set if p])

    # Build billing rows
    billing_rows = []
    total_received = 0
    pending_users = 0
    pending_amount = 0

    # Map slug to name from users.json (single source of truth)
    slug_to_name = {o.slug: o.name for o in user_service.list_owners()}

    for user_slug, user_data in billing_data.items():
        user_name = slug_to_name.get(user_slug, user_slug)
        user_plan = user_data.get("tier", "")
        for inv in user_data.get("invoices", []):
            # Filtering
            if name and name.lower() not in user_name.lower():
                continue
            if slug and slug.lower() not in user_slug.lower():
                continue
            if plan and plan != user_plan:
                continue
            if payment_status and payment_status.lower() != inv.get("status", "").lower():
                continue
            billing_rows.append({
                "name": user_name,
                "slug": user_slug,
                "plan": user_plan,
                "due_date": inv.get("due_date", ""),
                "amount": inv.get("amount", 0),
                "payment_status": inv.get("status", "").capitalize(),
            })
            if inv.get("status", "").lower() == "paid":
                total_received += inv.get("amount", 0)
            else:
                pending_users += 1
                pending_amount += inv.get("amount", 0)

    filters = {"name": name, "slug": slug, "plan": plan, "payment_status": payment_status}
    return _r(request, "admin/partials/system_billing.html", {
        "billing_rows": billing_rows,
        "total_received": total_received,
        "pending_users": pending_users,
        "pending_amount": pending_amount,
        "plans": plans,
        "filters": filters,
    })


@app.get("/admin/system/history", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_history(request: Request, slug: Optional[str] = None):
    history = index_service.get_history(slug=slug or None, limit=100)
    return _r(request, "admin/partials/system_history.html", {
        "history": history,
        "slug": slug,
    })


@app.get("/admin/system/deleted", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_deleted(request: Request):
    profiles = profile_service.list_profiles(status_filter="deleted")
    return _r(request, "admin/partials/system_deleted.html", {"profiles": profiles})


@app.get("/admin/system/logs", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_logs(
    request: Request,
    log_type: str = Query("app"),
    slug: Optional[str] = Query(None),
    tail: int = Query(200),
    search: Optional[str] = Query(None),
):
    result = log_service.read_log(log_type=log_type, slug=slug, tail=tail, search=search)
    profile_slugs = log_service.list_profile_logs()
    return _r(request, "admin/partials/system_logs.html", {
        "log_type":      log_type,
        "slug":          slug,
        "tail":          tail,
        "search":        search,
        "lines":         result["lines"],
        "total_lines":   result["total_lines"],
        "profile_slugs": profile_slugs,
    })


@app.get("/admin/system/llm", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_llm(request: Request):
    prompts = llm_prompts_service.get_prompts()
    usage   = token_service.get_all()
    totals  = token_service.get_totals()
    profiles = profile_service.list_profiles()
    return _r(request, "admin/partials/system_llm.html", {
        "prompts":  prompts,
        "usage":    usage,
        "totals":   totals,
        "profiles": [p for p in profiles if p.status != "deleted"],
    })


@app.post("/admin/system/llm/prompt/{key}", include_in_schema=False, response_class=HTMLResponse)
async def htmx_save_llm_prompt(request: Request, key: str, content: str = Form(...)):
    ok = llm_prompts_service.update_prompt(key, content)
    if not ok:
        return HTMLResponse(
            '<p class="text-red-600 text-sm">Unknown prompt key.</p>',
            status_code=400,
        )
    return HTMLResponse(
        '<p class="text-green-600 text-sm font-medium">Saved successfully.</p>'
    )


@app.post("/admin/system/llm/prompt/restore", include_in_schema=False, response_class=HTMLResponse)
async def htmx_restore_llm_prompts(request: Request):
    llm_prompts_service.restore_defaults()
    prompts = llm_prompts_service.get_prompts()
    usage   = token_service.get_all()
    totals  = token_service.get_totals()
    profiles = profile_service.list_profiles()
    return _r(request, "admin/partials/system_llm.html", {
        "prompts":  prompts,
        "usage":    usage,
        "totals":   totals,
        "profiles": [p for p in profiles if p.status != "deleted"],
    })


@app.post("/admin/system/llm/tokens/reset/{slug}", include_in_schema=False)
async def htmx_reset_token_usage(slug: str):
    token_service.reset_profile(slug)
    return HTMLResponse('<p class="text-green-600 text-xs">Reset.</p>')


# ── Email Templates ───────────────────────────────────────────────────────────

@app.get("/admin/system/email", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_email(request: Request):
    return _r(request, "admin/partials/system_email_templates.html", {
        "templates": email_template_service.get_templates(),
    })


@app.post("/admin/system/email/save/{name}", include_in_schema=False, response_class=HTMLResponse)
async def htmx_save_email_template(
    name:      str,
    subject:   str = Form(...),
    body_text: str = Form(...),
    body_html: str = Form(...),
):
    ok = email_template_service.update_template(name, subject, body_text, body_html)
    if not ok:
        return HTMLResponse(
            '<span class="text-red-600">Unknown template name.</span>',
            status_code=400,
        )
    return HTMLResponse('<span class="text-green-600 font-medium">Saved.</span>')


@app.post("/admin/system/email/restore/{name}", include_in_schema=False, response_class=HTMLResponse)
def htmx_restore_email_template(request: Request, name: str):
    email_template_service.restore_defaults(name)
    templates = email_template_service.get_templates()
    if name not in templates:
        return HTMLResponse("", status_code=404)
    return _r(request, "admin/partials/system_email_templates.html", {
        "templates": templates,
    })


@app.post("/admin/system/email/restore_all", include_in_schema=False, response_class=HTMLResponse)
def htmx_restore_all_email_templates(request: Request):
    email_template_service.restore_defaults()
    return _r(request, "admin/partials/system_email_templates.html", {
        "templates": email_template_service.get_templates(),
    })


@app.get("/admin/system/users", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_users(request: Request):
    users   = user_service.list_users()
    profiles = [p for p in profile_service.list_profiles() if p.status != "deleted"]
    return _r(request, "admin/partials/system_users.html", {
        "users": users, "profiles": profiles,
    })


@app.post("/admin/system/users/add", include_in_schema=False, response_class=HTMLResponse)
async def htmx_add_user(
    request: Request,
    email: str = Form(...),
    name:  str = Form(...),
    role:  str = Form(...),
    slug:  str = Form(""),
):
    ok, error = user_service.add_user(email, name, role, slug or None)
    users    = user_service.list_users()
    profiles = [p for p in profile_service.list_profiles() if p.status != "deleted"]
    return _r(request, "admin/partials/system_users.html", {
        "users": users, "profiles": profiles,
        "add_error": error if not ok else None,
    })


@app.post("/admin/system/users/remove/{email:path}", include_in_schema=False, response_class=HTMLResponse)
async def htmx_remove_user(email: str):
    user_service.remove_user(email)
    return HTMLResponse("")     # HTMX swaps the row to empty (effectively removes it)

# ── Templates (System Prompts + Email) ──────────────────────────────────────
@app.get("/admin/system/templates", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_templates(request: Request):
    return _r(request, "admin/partials/system_templates.html", {
        "templates": email_template_service.get_templates(),
        "prompts": llm_prompts_service.get_prompts(),
    })

@app.get("/admin/system/users/edit/{email:path}", include_in_schema=False, response_class=HTMLResponse)
def htmx_user_edit_row(email: str):
    """Return an inline edit row for the given user (email only — name is managed via owner preferences)."""
    user = user_service.get_user(email)
    if not user:
        return HTMLResponse("", status_code=404)
    safe_email = email.replace('"', "&quot;").replace("'", "&#39;")
    html = f"""
    <tr>
      <td colspan="6" class="px-4 py-3 bg-indigo-50 border-b border-indigo-100">
        <form hx-post="/admin/system/users/update"
              hx-target="#users-container" hx-swap="outerHTML"
              class="flex items-end gap-3 flex-wrap">
          <input type="hidden" name="old_email" value="{safe_email}">
          <div>
            <label class="block text-xs font-medium text-gray-500 mb-1">Email</label>
            <input type="email" name="new_email" value="{safe_email}" required
                   class="text-sm border border-gray-300 rounded-lg px-3 py-1.5 font-mono w-64
                          focus:outline-none focus:ring-2 focus:ring-indigo-300">
          </div>
          <div class="flex gap-2">
            <button type="submit"
                    class="px-3 py-1.5 text-sm font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">
              Save
            </button>
            <button type="button"
                    hx-get="/admin/system/users"
                    hx-target="#users-container" hx-swap="outerHTML"
                    class="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors">
              Cancel
            </button>
          </div>
        </form>
      </td>
    </tr>"""
    return HTMLResponse(html)


@app.post("/admin/system/users/update", include_in_schema=False, response_class=HTMLResponse)
async def htmx_update_user(
    request: Request,
    old_email: str = Form(...),
    new_email: str = Form(...),
):
    ok, error = user_service.update_email(old_email, new_email)
    users    = user_service.list_users()
    profiles = [p for p in profile_service.list_profiles() if p.status != "deleted"]
    return _r(request, "admin/partials/system_users.html", {
        "users":        users,
        "profiles":     profiles,
        "update_error": error if not ok else None,
    })


@app.get("/admin/system/config", include_in_schema=False, response_class=HTMLResponse)
def htmx_system_config(request: Request):
    return _r(request, "admin/partials/system_config.html", {
        "rows":     settings.get_config_display(),
        "is_local": settings.IS_LOCAL,
    })


# ── Admin billing endpoints ───────────────────────────────────────────────────

def _billing_partial(request: Request, slug: str):
    """Return the billing manage partial with up-to-date context."""
    return _r(request, "admin/partials/billing_manage.html", {
        "slug":           slug,
        "billing_entry":  billing_service.get_entry(slug),
        "billing_status": billing_service.get_status(slug),
        "platform_fee":   settings.PLATFORM_FEE_INR,
    })


@app.get("/admin/billing/{slug}", include_in_schema=False, response_class=HTMLResponse)
def admin_billing_panel(request: Request, slug: str):
    """HTMX partial: billing manage accordion content."""
    return _billing_partial(request, slug)


@app.post("/admin/billing/{slug}/tier", include_in_schema=False, response_class=HTMLResponse)
async def admin_set_tier(request: Request, slug: str, tier: str = Form(...)):
    """Change billing tier for a profile."""
    try:
        new_tier     = BillingTier(tier)
        admin_email  = get_current_user(request)["email"]
        billing_service.set_tier(slug, new_tier, admin_email)
    except (ValueError, KeyError) as exc:
        return HTMLResponse(
            f'<p class="text-red-600 text-sm">Error: {exc}</p>', status_code=400
        )
    resp = _billing_partial(request, slug)
    resp.headers["HX-Trigger"] = '{"showToast":"Billing tier updated."}'
    return resp


@app.post("/admin/billing/{slug}/invoice", include_in_schema=False, response_class=HTMLResponse)
async def admin_create_invoice(request: Request, slug: str):
    """Manually create the next billing period invoice."""
    try:
        admin_email = get_current_user(request)["email"]  # noqa: F841 — for future audit
        billing_service.create_invoice(slug)
    except ValueError as exc:
        return HTMLResponse(
            f'<p class="text-red-600 text-sm">Error: {exc}</p>', status_code=400
        )
    resp = _billing_partial(request, slug)
    resp.headers["HX-Trigger"] = '{"showToast":"Invoice created."}'
    return resp


@app.post(
    "/admin/billing/{slug}/invoice/{invoice_id}/confirm",
    include_in_schema=False,
    response_class=HTMLResponse,
)
async def admin_confirm_payment(request: Request, slug: str, invoice_id: str):
    """Mark an invoice as paid (admin-confirmed)."""
    try:
        admin_email = get_current_user(request)["email"]
        billing_service.confirm_payment(slug, invoice_id, admin_email)
    except ValueError as exc:
        return HTMLResponse(
            f'<p class="text-red-600 text-sm">Error: {exc}</p>', status_code=400
        )
    resp = _billing_partial(request, slug)
    resp.headers["HX-Trigger"] = '{"showToast":"Payment confirmed."}'
    return resp


# ── Tab switching via HTMX (return fragments only, no full layout) ────────────

@app.get("/admin/tab/registry", include_in_schema=False, response_class=HTMLResponse)
def htmx_tab_registry(request: Request):
    return _r(request, "admin/tab_registry.html", {})


@app.get("/admin/manage-tab", include_in_schema=False, response_class=HTMLResponse)
def htmx_tab_manage(request: Request):
    return HTMLResponse(
        '<div class="text-center text-gray-400 py-12 text-sm">'
        'Select a profile from the Registry tab to manage it.</div>'
    )


@app.get("/admin/tab/system", include_in_schema=False, response_class=HTMLResponse)
def htmx_tab_system(request: Request):
    return _r(request, "admin/tab_system.html", {})


# ── Soft-delete alias (HTMX POST workaround) ──────────────────────────────────

@app.post("/api/profiles/{slug}/soft-delete", include_in_schema=False)
def soft_delete_alias(slug: str):
    from app.models.api_models import SuccessResponse
    ok = profile_service.soft_delete(slug)
    if not ok:
        raise HTTPException(404, f"Profile '{slug}' not found")
    return SuccessResponse(message=f"Profile '{slug}' soft-deleted")


# =============================================================================
# CHAT UI
# =============================================================================

@app.get("/chat/{slug}", include_in_schema=False, response_class=HTMLResponse)
def chat_page(request: Request, slug: str):
    from app.services.chat_service import chat_service

    profile = profile_service.get_profile(slug)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    if profile.status != "enabled":
        return HTMLResponse(
            '<html><body style="font-family:sans-serif;text-align:center;padding:4rem;">'
            '<h2>This profile is not available.</h2></body></html>',
            status_code=403,
        )

    fs = ProfileFileStorage(slug)
    welcome   = chat_service.get_welcome_message(slug)
    followups = chat_service.get_initial_followups(slug)
    placeholder = prompt_service.chat_placeholder(slug)

    return _r(request, "chat/chat.html", {
        "profile":         profile,
        "slides_data":     fs.read_slides(),
        "profile_css":     fs.read_css(),
        "welcome_message": welcome,
        "followups":       followups,
        "placeholder":     placeholder,
    })


# =============================================================================
# STARTUP / SHUTDOWN
# =============================================================================

@app.on_event("startup")
async def startup_event():
    import asyncio
    import shutil as _shutil
    from app.storage.hf_sync import hf_sync

    # ── Pull persistent data from HF Dataset (HF Spaces only) ────────────
    # Runs in a thread pool so the async event loop is not blocked.
    # On local dev hf_sync.pull() is a no-op — this line is harmless.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, hf_sync.pull)

    # ── Start periodic log sync (HF Spaces only) ──────────────────────────
    hf_sync.start_log_sync_loop(settings.HF_LOG_SYNC_INTERVAL_MINUTES)

    logger.info("=" * 60)
    logger.info("AI Profile Platform starting up")
    logger.info("Model: %s", settings.AI_MODEL)
    enabled = [p for p in profile_service.list_profiles() if p.status == "enabled"]
    logger.info("Enabled profiles: %d", len(enabled))
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("AI Profile Platform shutting down")
    from app.storage.hf_sync import hf_sync
    hf_sync.push_logs()   # final log flush to HF Dataset before process exits