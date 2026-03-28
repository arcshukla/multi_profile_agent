"""
pushover_service.py
-------------------
Thin wrapper around the Pushover REST API.

Adapted from the root-level pushover_service.py to use this project's
config and logging conventions.

Env vars (set in .env):
    PUSHOVER_API_TOKEN  — your Pushover application token
    PUSHOVER_USER_KEY   — your Pushover user/group key
    PUSHOVER_URL        — (optional) override the default Pushover API URL
    IS_LOCAL            — when TRUE, logs the message but skips the actual push
"""

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

class PushoverService:

    def send(self, message: str) -> None:
        logger.info("Pushover: %s", message)

        if settings.IS_LOCAL:
            logger.info("IS_LOCAL=true — skipping actual push notification")
            return

        if not settings.PUSHOVER_API_TOKEN or not settings.PUSHOVER_USER_KEY or not settings.PUSHOVER_URL:
            logger.debug("Pushover not configured — skipping")
            return

        try:
            requests.post(
                settings._URL,
                data={
                    "token":   settings.PUSHOVER_API_TOKEN,
                    "user":    settings.PUSHOVER_USER_KEY,
                    "message": message,
                },
                timeout=5,
            )
        except Exception as e:
            logger.warning("Pushover send failed: %s", e)
