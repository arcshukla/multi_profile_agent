"""
profile_service.py
------------------
Business logic layer for profile management.

Sits between the API routes and the storage layer.
Coordinates: ProfileRegistryStore + ProfileFileStorage + IndexService + PromptService.

Rules:
  - API routes call service methods, not storage directly
  - Service methods validate, orchestrate, and log
  - Storage is only touched through the storage classes
"""

from pathlib import Path
from typing import Optional

from app.core.constants import STATUS_ENABLED, STATUS_DISABLED, STATUS_DELETED
from app.core.logging_config import get_logger, get_profile_logger
from app.models.profile_models import ProfileEntry, CreateProfileRequest, ProfileResponse
from app.storage.profile_registry import profile_registry
from app.storage.file_storage import ProfileFileStorage
from app.utils.slug_utils import slugify, unique_slug
from app.services.prompt_service import prompt_service

logger = get_logger(__name__)


class ProfileService:
    """
    Profile lifecycle management.

    Operations:
      create, get, list, update status, delete, soft-delete
    """

    # ── Read ──────────────────────────────────────────────────────────────────

    def list_profiles(
        self,
        status_filter: Optional[str] = None,
        name_filter: Optional[str] = None,
        slug_filter: Optional[str] = None,
    ) -> list[ProfileResponse]:
        """Return all profiles, optionally filtered. Enriches with runtime data."""
        entries = profile_registry.get_all()

        if status_filter:
            entries = [e for e in entries if e.status == status_filter]
        if name_filter:
            entries = [e for e in entries if name_filter.lower() in e.name.lower()]
        if slug_filter:
            entries = [e for e in entries if slug_filter.lower() in e.slug_name.lower()]

        return [self._enrich(e) for e in entries]

    def get_profile(self, slug: str) -> Optional[ProfileResponse]:
        entry = profile_registry.get_by_slug(slug)
        if not entry:
            return None
        return self._enrich(entry)

    def get_entry(self, slug: str) -> Optional[ProfileEntry]:
        return profile_registry.get_by_slug(slug)

    def profile_exists(self, slug: str) -> bool:
        return profile_registry.exists(slug)

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_profile(self, req: CreateProfileRequest) -> ProfileResponse:
        """
        Create a new profile:
          1. Derive slug from name
          2. Validate uniqueness
          3. Create filesystem structure
          4. Register in profiles.json
          5. Write default prompts
        """
        slug = unique_slug(req.name, [e.slug_name for e in profile_registry.get_all()])
        if not slug:
            raise ValueError(f"Cannot derive a valid slug from name: '{req.name}'")

        base_folder = f"profiles/{slug}"
        entry = ProfileEntry(
            name=req.name,
            slug_name=slug,
            status=req.status,
            base_folder=base_folder,
        )

        # Create filesystem directories
        fs = ProfileFileStorage(slug)
        fs.create_directories()

        # Write default prompts file
        prompt_service.ensure_prompts_file(slug)

        # Register in profiles.json
        profile_registry.add(entry)

        plog = get_profile_logger(slug)
        plog.info("Profile created | name='%s' | slug='%s'", req.name, slug)
        logger.info("Profile created: '%s' (%s)", req.name, slug)

        return self._enrich(entry)

    def update_status(self, slug: str, status: str) -> Optional[ProfileResponse]:
        """Enable or disable a profile."""
        if status not in (STATUS_ENABLED, STATUS_DISABLED):
            raise ValueError(f"Invalid status: {status}")

        entry = profile_registry.update(slug, status=status)
        if not entry:
            return None

        get_profile_logger(slug).info("Status updated: %s", status)
        return self._enrich(entry)

    def soft_delete(self, slug: str) -> bool:
        """Mark profile as deleted (keeps files, removes from active list)."""
        success = profile_registry.set_status(slug, STATUS_DELETED)
        if success:
            get_profile_logger(slug).info("Profile soft-deleted")
        return success

    def hard_delete(self, slug: str) -> bool:
        """
        Permanently delete profile:
          1. Archive billing data to system/billing_archive/{slug}/
          2. Remove from registry
          3. Delete filesystem (docs, chroma, config)
        """
        # Archive billing data before any deletion
        self._archive_billing_data(slug)

        # Remove from registry first
        removed = profile_registry.delete(slug)
        if not removed:
            return False

        # Evict engine cache
        try:
            from app.services.index_service import index_service
            index_service.evict_engine(slug)
        except Exception:
            pass

        # Delete filesystem
        fs = ProfileFileStorage(slug)
        fs.delete_all()

        get_profile_logger(slug).info("Profile permanently deleted")
        logger.info("Profile hard-deleted: '%s'", slug)
        return True

    def _archive_billing_data(self, slug: str) -> None:
        """
        Copy billing-critical records to system/billing_archive/{slug}/ before deletion.
        Failures are logged but never block the deletion.
        """
        import json
        import shutil
        from datetime import datetime, timezone
        from app.core.config import settings
        from app.services.token_service import token_service

        archive_dir = settings.BILLING_ARCHIVE_DIR / slug
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)

            # 1. Token usage snapshot
            usage = token_service.get_profile(slug)
            snapshot = {
                "slug":        slug,
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "token_usage": usage,
                "ledger_entries": token_service.get_ledger(slug=slug),
            }
            (archive_dir / "billing_snapshot.json").write_text(
                json.dumps(snapshot, indent=2), encoding="utf-8"
            )

            # 2. Chat events (per-profile analytics)
            chat_src = ProfileFileStorage(slug).chat_events_path
            if chat_src.exists():
                shutil.copy2(chat_src, archive_dir / "chat_events.jsonl")

            logger.info("Billing archive created for '%s' at %s", slug, archive_dir)
        except Exception as e:
            logger.error("Failed to archive billing data for '%s': %s", slug, e)

    def restore_deleted(self, slug: str) -> Optional[ProfileResponse]:
        """Re-enable a soft-deleted profile."""
        entry = profile_registry.get_by_slug(slug)
        if not entry or entry.status != STATUS_DELETED:
            return None
        updated = profile_registry.update(slug, status=STATUS_ENABLED)
        if updated:
            get_profile_logger(slug).info("Profile restored from deleted state")
        return self._enrich(updated) if updated else None

    # ── Enrichment ────────────────────────────────────────────────────────────

    def _enrich(self, entry: ProfileEntry) -> ProfileResponse:
        """
        Augment a registry entry with runtime data:
          - has_photo (filesystem check)
          - document_count
          - chunk_count (ChromaDB)
          - last_indexed (from index_history.log)
        """
        from app.services.index_service import index_service

        fs = ProfileFileStorage(entry.slug_name)
        status_info = index_service.get_status(entry.slug_name)

        has_photo = fs.has_photo()
        photo_ts  = int(fs.photo_path.stat().st_mtime) if has_photo else 0
        return ProfileResponse(
            name=entry.name,
            slug=entry.slug_name,
            status=entry.status,
            base_folder=entry.base_folder,
            has_photo=has_photo,
            photo_ts=photo_ts,
            document_count=status_info.get("document_count", 0),
            chunk_count=status_info.get("chunk_count", 0),
            last_indexed=status_info.get("last_indexed"),
        )


# Singleton
profile_service = ProfileService()