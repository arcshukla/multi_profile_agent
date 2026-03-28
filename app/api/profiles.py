"""
api/profiles.py
---------------
REST endpoints for profile CRUD and status management.

All routes are prefixed /api/profiles (registered in main.py).
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from app.core.logging_config import get_logger
from app.models.api_models import SuccessResponse, ErrorResponse
from app.models.profile_models import CreateProfileRequest, ProfileResponse
from app.services.profile_service import profile_service
from app.storage.file_storage import ProfileFileStorage

logger = get_logger(__name__)
router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileResponse])
def list_profiles(
    status: str | None = None,
    name: str | None = None,
    slug: str | None = None,
):
    """List all profiles with optional filters."""
    return profile_service.list_profiles(
        status_filter=status,
        name_filter=name,
        slug_filter=slug,
    )


@router.post("", response_model=ProfileResponse, status_code=201)
def create_profile(req: CreateProfileRequest):
    """Create a new profile."""
    try:
        return profile_service.create_profile(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{slug}", response_model=ProfileResponse)
def get_profile(slug: str):
    profile = profile_service.get_profile(slug)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    return profile


@router.patch("/{slug}/status")
def update_status(slug: str, status: str):
    try:
        result = profile_service.update_status(slug, status)
        if not result:
            raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{slug}/soft")
def soft_delete(slug: str):
    """Soft-delete: mark as deleted, keep files."""
    success = profile_service.soft_delete(slug)
    if not success:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    return SuccessResponse(message=f"Profile '{slug}' soft-deleted")


@router.delete("/{slug}")
def hard_delete(slug: str):
    """Hard-delete: remove from registry and wipe all files."""
    success = profile_service.hard_delete(slug)
    if not success:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    return SuccessResponse(message=f"Profile '{slug}' permanently deleted")


@router.post("/{slug}/restore")
def restore_profile(slug: str):
    result = profile_service.restore_deleted(slug)
    if not result:
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found or not deleted")
    return result


# ── Photo ──────────────────────────────────────────────────────────────────────

@router.post("/{slug}/photo")
async def upload_photo(slug: str, file: UploadFile = File(...)):
    """Upload a profile photo."""
    if not profile_service.profile_exists(slug):
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    data = await file.read()
    fs = ProfileFileStorage(slug)
    fs.save_photo(data)
    return SuccessResponse(message="Photo uploaded")


@router.get("/{slug}/photo")
def get_photo(slug: str):
    """Serve the profile photo."""
    fs = ProfileFileStorage(slug)
    if not fs.has_photo():
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(str(fs.photo_path), media_type="image/jpeg")


# ── Header / CSS / JS ─────────────────────────────────────────────────────────

@router.get("/{slug}/header")
def get_header(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(status_code=404, detail="Profile not found")
    fs = ProfileFileStorage(slug)
    return {"content": fs.read_header()}


@router.post("/{slug}/header")
async def save_header(slug: str, content: str = Form(...)):
    if not profile_service.profile_exists(slug):
        raise HTTPException(status_code=404, detail="Profile not found")
    fs = ProfileFileStorage(slug)
    fs.write_header(content)
    return SuccessResponse(message="Header saved")


@router.get("/{slug}/css")
def get_css(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"content": ProfileFileStorage(slug).read_css()}


@router.post("/{slug}/css")
async def save_css(slug: str, content: str = Form(...)):
    if not profile_service.profile_exists(slug):
        raise HTTPException(status_code=404, detail="Profile not found")
    ProfileFileStorage(slug).write_css(content)
    return SuccessResponse(message="CSS saved")
