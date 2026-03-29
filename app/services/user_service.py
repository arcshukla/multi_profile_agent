"""
user_service.py
---------------
Manages the platform user registry: system/users.json.

Schema:
  {
    "email@example.com": {
      "role":       "owner" | "admin",
      "slug":       "profile-slug",   # only for role=owner
      "name":       "Display Name",
      "created_at": "2026-03-27T10:00:00Z"
    }
  }

Admin users whose emails appear in settings.ADMIN_EMAILS do NOT need
a record here — they are resolved automatically.  Adding them to the
file is supported but not required.

Constraints:
  - One owner per profile slug.
  - One profile slug per owner email.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings, SYSTEM_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_USERS_FILE = SYSTEM_DIR / "users.json"


class UserService:
    """Thread-safe CRUD for the platform user registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ── Read ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.error("Failed to load users.json: %s", e)
            return {}

    def _save(self, data: dict) -> None:
        from app.storage.hf_sync import hf_sync
        _USERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        hf_sync.push_file(_USERS_FILE)

    def list_users(self) -> dict[str, dict]:
        """Return all users from users.json (does not include ADMIN_EMAILS env entries)."""
        with self._lock:
            return dict(self._load())

    def get_user(self, email: str) -> Optional[dict]:
        """Return a single user record by email, or None."""
        with self._lock:
            return self._load().get(email.lower().strip())

    # ── Resolve session (called after Google OAuth callback) ──────────────────

    def resolve_session(self, email: str, google_name: str) -> Optional[dict]:
        """
        Build the session payload for a newly authenticated Google user.

        Lookup order:
          1. ADMIN_EMAILS env var  → role=admin
          2. users.json            → role as stored
          3. Neither               → None (access denied)

        Returns {"email", "name", "role", "slug"} or None.
        """
        email = email.lower().strip()

        # Bootstrap admin via env var (no file entry required)
        if email in [e.lower() for e in settings.ADMIN_EMAILS]:
            return {"email": email, "name": google_name, "role": "admin", "slug": None}

        record = self.get_user(email)
        if record:
            return {
                "email": email,
                "name":  record.get("name") or google_name,
                "role":  record.get("role", "owner"),
                "slug":  record.get("slug"),
            }

        logger.warning("Login attempt from unregistered email: %s", email)
        return None

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_user(
        self,
        email: str,
        name: str,
        role: str,
        slug: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Add or update a user.

        Returns (success, error_message).
        Enforces: one owner per slug.
        """
        email = email.lower().strip()
        if not email:
            return False, "Email is required."
        if role not in ("owner", "admin"):
            return False, f"Invalid role: {role!r}"
        if role == "owner" and not slug:
            return False, "A profile slug is required for the 'owner' role."

        with self._lock:
            data = self._load()

            # Check slug uniqueness for owners
            if role == "owner" and slug:
                for existing_email, rec in data.items():
                    if rec.get("slug") == slug and existing_email != email:
                        return False, f"Profile '{slug}' is already assigned to {existing_email}."

            data[email] = {
                "role":       role,
                "slug":       slug if role == "owner" else None,
                "name":       name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(data)

        logger.info("Added user %s (role=%s, slug=%s)", email, role, slug)
        return True, ""

    def remove_user(self, email: str) -> bool:
        """Remove a user by email. Returns True if removed, False if not found."""
        email = email.lower().strip()
        with self._lock:
            data = self._load()
            if email not in data:
                return False
            del data[email]
            self._save(data)

        logger.info("Removed user %s", email)
        return True


user_service = UserService()
