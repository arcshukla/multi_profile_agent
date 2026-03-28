"""
api/prompts.py  —  Prompt management endpoints
"""
from fastapi import APIRouter, HTTPException
from app.models.api_models import UpdatePromptRequest, SuccessResponse
from app.services.prompt_service import prompt_service
from app.services.profile_service import profile_service

router = APIRouter(prefix="/api/profiles/{slug}/prompts", tags=["prompts"])


@router.get("")
def get_prompts(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, "Profile not found")
    prompts, is_default = prompt_service.get_prompts(slug)
    return {"prompts": prompts, "is_default": is_default}


@router.patch("")
def update_prompt(slug: str, req: UpdatePromptRequest):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, "Profile not found")
    ok = prompt_service.update_prompt(slug, req.short_name, req.content)
    if not ok:
        raise HTTPException(400, f"Unknown prompt key: {req.short_name}")
    return SuccessResponse(message="Prompt updated")


@router.post("/restore")
def restore_defaults(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, "Profile not found")
    prompt_service.restore_defaults(slug)
    return SuccessResponse(message="Prompts restored to defaults")
