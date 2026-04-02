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
            logger.info("Pushover: IS_LOCAL=true — skipping actual push notification")
            return

        if not settings.PUSHOVER_API_TOKEN:
            logger.warning("Pushover: PUSHOVER_API_TOKEN not set — notification NOT sent")
            return
        if not settings.PUSHOVER_USER_KEY:
            logger.warning("Pushover: PUSHOVER_USER_KEY not set — notification NOT sent")
            return
        if not settings.PUSHOVER_URL:
            logger.warning("Pushover: PUSHOVER_URL not set — notification NOT sent")
            return

        try:
            resp = requests.post(
                settings.PUSHOVER_URL,
                data={
                    "token":   settings.PUSHOVER_API_TOKEN,
                    "user":    settings.PUSHOVER_USER_KEY,
                    "message": message,
                },
                timeout=5,
            )
            if resp.ok:
                logger.info("Pushover: notification sent (status=%d)", resp.status_code)
            else:
                logger.warning(
                    "Pushover: API returned error (status=%d, body=%s)",
                    resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.warning("Pushover: send failed: %s", e)
