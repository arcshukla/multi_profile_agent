"""
api/chat.py  —  Chat endpoints
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.constants import STATUS_ENABLED
from app.core.logging_config import get_logger
from app.models.api_models import ChatRequest, ChatResponse
from app.models.profile_models import ProfileEntry
from app.services.chat_service import chat_service
from app.services.profile_service import profile_service
from app.services.prompt_service import prompt_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/profiles/{slug}/chat", tags=["chat"])


# ── Shared dependency ──────────────────────────────────────────────────────────

def _require_enabled_profile(slug: str) -> ProfileEntry:
    """FastAPI dependency: resolve slug → enabled ProfileEntry or raise HTTP error."""
    entry = profile_service.get_entry(slug)
    if not entry:
        raise HTTPException(404, f"Profile '{slug}' not found")
    if entry.status != STATUS_ENABLED:
        raise HTTPException(403, f"Profile '{slug}' is not active")
    return entry


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
def chat(slug: str, req: ChatRequest,
         _entry: ProfileEntry = Depends(_require_enabled_profile)):
    logger.info("Chat request | slug=%s | len=%d", slug, len(req.message))
    resp = chat_service.chat(
        slug=slug,
        message=req.message,
        history=req.history,
        session_id=req.session_id,
    )
    logger.info("Chat response | slug=%s | tokens=%d calls=%d | followups=%d",
                slug, resp.tokens_used.total_tokens,
                resp.tokens_used.call_count, len(resp.followups))
    return resp


@router.get("/welcome")
def get_welcome(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    logger.debug("Welcome request | slug=%s", slug)
    return {
        "welcome":     chat_service.get_welcome_message(slug),
        "followups":   chat_service.get_initial_followups(slug),
        "placeholder": prompt_service.chat_placeholder(slug),
    }
