"""
profile_models.py
-----------------
Pydantic models for profile data.
Used for validation in API routes and service layers.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


def _slugify(value: str) -> str:
    """Convert name to URL-safe slug: 'Archana Shukla' → 'archana-shukla'."""
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value


# ---------------------------------------------------------------------------
# Registry models  (stored in profiles.json)
# ---------------------------------------------------------------------------

class ProfileEntry(BaseModel):
    """A single entry in the profile registry (profiles.json)."""
    name: str
    slug_name: str = Field(alias="slugName")
    status: str = "enabled"
    base_folder: str

    class Config:
        populate_by_name = True

    @field_validator("slug_name", mode="before")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        # Allow empty; will be derived from name if missing
        return v or ""


class ProfileRegistry(BaseModel):
    """Root structure of profiles.json."""
    profiles: list[ProfileEntry] = []


# ---------------------------------------------------------------------------
# Request / Response models  (API layer)
# ---------------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    """Payload for POST /api/profiles."""
    name: str = Field(..., min_length=2, max_length=120)
    status: str = "enabled"

    @property
    def slug(self) -> str:
        return _slugify(self.name)


class UpdateProfileRequest(BaseModel):
    """Payload for PATCH /api/profiles/{slug}."""
    name: Optional[str] = None
    status: Optional[str] = None


class ProfileResponse(BaseModel):
    """API response shape for a profile."""
    name: str
    slug: str
    status: str
    base_folder: str
    has_photo: bool = False
    photo_ts: int = 0          # file modification timestamp for cache-busting
    document_count: int = 0
    chunk_count: int = 0
    last_indexed: Optional[str] = None   # ISO timestamp or None


class ProfileListResponse(BaseModel):
    """Paginated list of profiles."""
    profiles: list[ProfileResponse]
    total: int
    page: int
    page_size: int
