"""
preferences_service.py
----------------------
Load and save per-profile owner preferences.

Storage: profiles/{slug}/config/preferences.json

Schema:
  {
    "notify_unanswered_email": false
  }
"""

import json

from app.core.config import PROFILES_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class PreferencesService:

    def get(self, slug: str) -> dict:
        """Load preferences, returning defaults if file absent."""
        path = PROFILES_DIR / slug / "config" / "preferences.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"notify_unanswered_email": False}
        except Exception as e:
            logger.error("Failed to load preferences for %s: %s", slug, e)
            return {"notify_unanswered_email": False}

    def save(self, slug: str, prefs: dict) -> None:
        path = PROFILES_DIR / slug / "config" / "preferences.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        from app.storage.hf_sync import hf_sync
        hf_sync.push_file(path)
        logger.info("Preferences saved for slug=%s", slug)


preferences_service = PreferencesService()
