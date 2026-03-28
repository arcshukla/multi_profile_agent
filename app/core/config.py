"""
config.py
---------
Central configuration loaded from environment variables + .env file.

All settings live here. No os.getenv() calls should appear outside this file.
Import `settings` wherever configuration is needed.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from .constants import REGISTRY_FILE

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Base paths  (all relative so the app works anywhere — local or HF Spaces)
# ---------------------------------------------------------------------------
BASE_DIR       = Path(__file__).resolve().parent.parent.parent  # project root
PROFILES_DIR   = BASE_DIR / "profiles"
SYSTEM_DIR     = BASE_DIR / "system"
LOGS_DIR       = BASE_DIR / "logs"
STATIC_DIR     = BASE_DIR / "static"
TEMPLATES_DIR  = BASE_DIR / "templates"
DEFAULTS_DIR   = BASE_DIR / "app" / "defaults"

# Ensure critical dirs exist at import time
for _d in [PROFILES_DIR, SYSTEM_DIR, LOGS_DIR, STATIC_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


class Settings:
    """
    Application-wide settings.

    Read once at startup; immutable at runtime.
    All values have sensible defaults so the app starts without a .env file.
    """

    # ── LLM ──────────────────────────────────────────────────────────────────
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_API_KEY: str  = os.getenv("OPENROUTER_API_KEY", "")
    AI_MODEL: str            = os.getenv("AI_MODEL", "openai/gpt-4o-mini")

    # ── Server ────────────────────────────────────────────────────────────────
    APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "7860"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # ── Profile system ────────────────────────────────────────────────────────
    PROFILES_REGISTRY_FILE: Path = SYSTEM_DIR / REGISTRY_FILE
    INDEX_HISTORY_FILE: Path     = SYSTEM_DIR / "index_history.log"

    # ── Billing / audit ───────────────────────────────────────────────────────
    TOKEN_LEDGER_FILE: Path    = SYSTEM_DIR / "token_ledger.jsonl"
    BILLING_ARCHIVE_DIR: Path  = SYSTEM_DIR / "billing_archive"

    # ── RAG / indexing ────────────────────────────────────────────────────────
    PROFILE_CACHE_MINUTES: int   = int(os.getenv("PROFILE_CACHE_MINUTES", "20"))
    FORCE_REINGEST: bool         = os.getenv("FORCE_PROFILE_REINGEST", "").lower() in ("true", "1", "yes")
    CHUNK_SIZE: int              = int(os.getenv("CHUNK_SIZE", "1024"))
    CHUNK_OVERLAP: int           = int(os.getenv("CHUNK_OVERLAP", "128"))
    RAG_TOP_K: int               = int(os.getenv("RAG_TOP_K", "4"))

    # ── Authentication ────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID:     str       = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str       = os.getenv("GOOGLE_CLIENT_SECRET", "")
    SESSION_SECRET_KEY:   str       = os.getenv("SESSION_SECRET_KEY", "change-me-in-production")
    ADMIN_EMAILS:         list[str] = [
        e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
    ]

    # ── Support ───────────────────────────────────────────────────────────────
    SUPPORT_EMAIL: str = os.getenv("SUPPORT_EMAIL", "support@aiprofile.app")

    # ── Billing / UPI ─────────────────────────────────────────────────────────
    UPI_VPA:               str   = os.getenv("UPI_VPA", "")
    UPI_PAYEE_NAME:        str   = os.getenv("UPI_PAYEE_NAME", "AI Profile Platform")
    PLATFORM_FEE_INR:      float = float(os.getenv("PLATFORM_FEE_INR", "10"))
    BILLING_INTERVAL_DAYS: int   = int(os.getenv("BILLING_INTERVAL_DAYS", "30"))
    BILLING_FILE:          Path  = SYSTEM_DIR / "billing.json"

    # ── Notifications (optional) ──────────────────────────────────────────────
    PUSHOVER_USER_KEY: str  = os.getenv("PUSHOVER_USER_KEY", "")
    PUSHOVER_API_TOKEN: str = os.getenv("PUSHOVER_API_TOKEN", "")
    PUSHOVER_URL: str = os.getenv("PUSHOVER_URL", "https://api.pushover.net/1/messages.json")

    # ── HuggingFace ───────────────────────────────────────────────────────────
    IS_HF_SPACE: bool = "HF_SPACE_ID" in os.environ
    IS_LOCAL: bool    = os.getenv("IS_LOCAL", "FALSE").strip().upper() == "TRUE"

    # ── HF Dataset sync (persistence on HF Spaces) ────────────────────────────
    # Set HF_STORAGE_REPO to e.g. "your-username/profile-storage" (private dataset repo)
    # HF_TOKEN is auto-injected on HF Spaces; set manually if needed
    HF_STORAGE_REPO:             str = os.getenv("HF_STORAGE_REPO", "")
    HF_TOKEN:                    str = os.getenv("HF_TOKEN", "")
    HF_LOG_SYNC_INTERVAL_MINUTES: int = int(os.getenv("HF_LOG_SYNC_INTERVAL_MINUTES", "5"))

    # ── Paths (as strings for convenience) ───────────────────────────────────
    BASE_DIR_STR:      str = str(BASE_DIR)
    PROFILES_DIR_STR:  str = str(PROFILES_DIR)
    SYSTEM_DIR_STR:    str = str(SYSTEM_DIR)
    LOGS_DIR_STR:      str = str(LOGS_DIR)


settings = Settings()
