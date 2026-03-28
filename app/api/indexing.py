"""
api/indexing.py  —  Document indexing endpoints
"""
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.models.api_models import IndexRequest, IndexStatusResponse, SuccessResponse
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
def trigger_index(slug: str, req: IndexRequest, background_tasks: BackgroundTasks):
    """
    Trigger indexing in the background.
    Returns immediately; poll GET /index for status.
    """
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    if index_service.is_indexing(slug):
        return {"message": "Indexing already in progress", "status": "running"}

    background_tasks.add_task(index_service.index_profile, slug, req.force)
    return SuccessResponse(message=f"Indexing started for '{slug}'")


@router.post("/force")
def force_reindex(slug: str, background_tasks: BackgroundTasks):
    """Force full reindex — wipes existing ChromaDB first."""
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    if index_service.is_indexing(slug):
        return {"message": "Indexing already in progress", "status": "running"}

    background_tasks.add_task(index_service.force_reindex, slug)
    return SuccessResponse(message=f"Force reindex started for '{slug}'")


# ── System-level history (all profiles) ──────────────────────────────────────

from fastapi import APIRouter as _AR
history_router = _AR(prefix="/api/system/index-history", tags=["system"])


@history_router.get("")
def get_index_history(slug: str | None = None, limit: int = 100):
    return index_service.get_history(slug=slug, limit=limit)
