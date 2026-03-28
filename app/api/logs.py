"""
api/logs.py  —  Log viewer endpoints
"""
from fastapi import APIRouter, Query
from typing import Optional
from app.services.log_service import log_service

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/{log_type}")
def read_log(
    log_type: str,
    slug: Optional[str] = Query(None),
    tail: int = Query(200, ge=1, le=2000),
    search: Optional[str] = Query(None),
):
    """
    Read log lines.
    log_type: app | indexing | chat | profile
    slug: required when log_type=profile
    """
    return log_service.read_log(log_type=log_type, slug=slug, tail=tail, search=search)


@router.get("")
def list_profile_logs():
    return {"profiles": log_service.list_profile_logs()}
