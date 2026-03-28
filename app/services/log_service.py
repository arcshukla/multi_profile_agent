"""
log_service.py
--------------
Read log files for display in the Admin UI System tab.

Log files:
  logs/app.log
  logs/indexing.log
  logs/chat.log
  logs/profile_<slug>.log
"""

from pathlib import Path
from typing import Optional

from app.core.config import LOGS_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class LogService:

    def read_log(
        self,
        log_type: str,
        slug: Optional[str] = None,
        tail: int = 200,
        search: Optional[str] = None,
    ) -> dict:
        """
        Read a log file and return recent lines.

        Args:
            log_type: "app" | "indexing" | "chat" | "profile"
            slug:     Required when log_type == "profile"
            tail:     Number of lines from the end
            search:   Filter lines containing this string

        Returns:
            {"log_type": ..., "lines": [...], "total_lines": n}
        """
        path = self._resolve_path(log_type, slug)
        if path is None or not path.exists():
            return {"log_type": log_type, "slug": slug, "lines": [], "total_lines": 0}

        try:
            all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            logger.error("Failed to read log %s: %s", path, e)
            return {"log_type": log_type, "slug": slug, "lines": [], "total_lines": 0}

        if search:
            all_lines = [l for l in all_lines if search.lower() in l.lower()]

        total = len(all_lines)
        lines = all_lines[-tail:] if tail > 0 else all_lines

        return {
            "log_type": log_type,
            "slug": slug,
            "lines": lines,
            "total_lines": total,
        }

    def _resolve_path(self, log_type: str, slug: Optional[str]) -> Optional[Path]:
        mapping = {
            "app":      LOGS_DIR / "app.log",
            "indexing": LOGS_DIR / "indexing.log",
            "chat":     LOGS_DIR / "chat.log",
        }
        if log_type == "profile":
            if not slug:
                logger.warning("log_type='profile' requires a slug")
                return None
            return LOGS_DIR / f"profile_{slug}.log"
        return mapping.get(log_type)

    def list_profile_logs(self) -> list[str]:
        """Return slugs for which a profile log file exists."""
        slugs = []
        for f in LOGS_DIR.glob("profile_*.log"):
            slug = f.stem.replace("profile_", "", 1)
            if slug:
                slugs.append(slug)
        return sorted(slugs)


# Singleton
log_service = LogService()
