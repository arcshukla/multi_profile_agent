"""
profile_registry.py
-------------------
Low-level read/write access to profiles.json.

This is the ONLY place that touches profiles.json.
All other code goes through ProfileService, not this class directly.

Design decisions:
  - File is the source of truth for static metadata (name, slug, status, folder).
  - Runtime data (last_indexed, chunk_count) is NOT stored here — derived at runtime.
  - Write operations are atomic: write to .tmp then rename.
"""

import json
import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.storage.hf_sync import hf_sync
from app.models.profile_models import ProfileEntry, ProfileRegistry

logger = get_logger(__name__)

# File-level lock — prevents concurrent writes from corrupting the JSON
_lock = threading.Lock()


class ProfileRegistryStore:
    """
    CRUD interface over profiles.json.

    Responsibilities:
      - load() / save() the registry file
      - add / update / delete profile entries
      - slug uniqueness enforcement
    """

    def __init__(self, registry_path: Optional[Path] = None) -> None:
        self.path = registry_path or settings.PROFILES_REGISTRY_FILE

    # ── Read ─────────────────────────────────────────────────────────────────

    def load(self) -> ProfileRegistry:
        """Load and parse profiles.json. Returns empty registry if missing."""
        if not self.path.exists():
            logger.info("Registry file not found — returning empty registry: %s", self.path)
            return ProfileRegistry()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            registry = ProfileRegistry(**data)
            logger.debug("Loaded %d profiles from registry", len(registry.profiles))
            return registry
        except Exception as e:
            logger.error("Failed to parse registry file %s: %s", self.path, e)
            return ProfileRegistry()

    def get_all(self) -> list[ProfileEntry]:
        return self.load().profiles

    def get_by_slug(self, slug: str) -> Optional[ProfileEntry]:
        return next((p for p in self.get_all() if p.slug_name == slug), None)

    def exists(self, slug: str) -> bool:
        return self.get_by_slug(slug) is not None

    # ── Write ─────────────────────────────────────────────────────────────────

    def _save(self, registry: ProfileRegistry) -> None:
        """Atomic write — .tmp → rename to prevent partial writes."""
        tmp = self.path.with_suffix(".json.tmp")
        # Serialize using alias so JSON keys are camelCase (slugName, etc.)
        data = registry.model_dump(by_alias=True)
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        logger.debug("Registry saved (%d profiles)", len(registry.profiles))
        hf_sync.push_file(self.path)

    def add(self, entry: ProfileEntry) -> ProfileEntry:
        """Add a new profile. Raises ValueError if slug already exists."""
        with _lock:
            registry = self.load()
            if any(p.slug_name == entry.slug_name for p in registry.profiles):
                raise ValueError(f"Profile with slug '{entry.slug_name}' already exists")
            registry.profiles.append(entry)
            self._save(registry)
            logger.info("Registry: added profile '%s' (%s)", entry.name, entry.slug_name)
        return entry

    def update(self, slug: str, **kwargs) -> Optional[ProfileEntry]:
        """Update fields on an existing profile entry. Returns updated entry or None."""
        with _lock:
            registry = self.load()
            for i, p in enumerate(registry.profiles):
                if p.slug_name == slug:
                    updated = p.model_copy(update=kwargs)
                    registry.profiles[i] = updated
                    self._save(registry)
                    logger.info("Registry: updated profile '%s': %s", slug, kwargs)
                    return updated
            logger.warning("Registry: update failed — slug '%s' not found", slug)
        return None

    def delete(self, slug: str) -> bool:
        """Permanently remove a profile from the registry."""
        with _lock:
            registry = self.load()
            before = len(registry.profiles)
            registry.profiles = [p for p in registry.profiles if p.slug_name != slug]
            if len(registry.profiles) == before:
                logger.warning("Registry: delete failed — slug '%s' not found", slug)
                return False
            self._save(registry)
            logger.info("Registry: deleted profile '%s'", slug)
        return True

    def set_status(self, slug: str, status: str) -> bool:
        """Convenience wrapper to update just the status field."""
        result = self.update(slug, status=status)
        return result is not None


# Singleton instance — import and use directly
profile_registry = ProfileRegistryStore()
