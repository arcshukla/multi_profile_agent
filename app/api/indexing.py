"""
api/indexing.py  —  Document indexing endpoints
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.models.api_models import IndexStatusResponse, SuccessResponse
from app.services.index_service import index_service
from app.services.profile_service import profile_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/profiles/{slug}/index", tags=["indexing"])


@router.get("", response_model=IndexStatusResponse)
def get_index_status(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    status = index_service.get_status(slug)
    return IndexStatusResponse(slug=slug, **status)


@router.post("")
def trigger_index(slug: str, background_tasks: BackgroundTasks):
    """Trigger a full index rebuild in the background. Returns immediately; poll GET /index for status."""
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    if index_service.is_indexing(slug):
        return {"message": "Indexing already in progress", "status": "running"}

    background_tasks.add_task(index_service.index_profile, slug)
    return SuccessResponse(message=f"Indexing started for '{slug}'")


# ── System-level history (all profiles) ──────────────────────────────────────

from fastapi import APIRouter as _AR
history_router = _AR(prefix="/api/system/index-history", tags=["system"])


@history_router.get("")
def get_index_history(slug: str | None = None, limit: int = 100):
    return index_service.get_history(slug=slug, limit=limit)
