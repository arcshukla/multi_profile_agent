"""
sendgrid_service.py
-------------------
Thin wrapper around the SendGrid Web API v3 (mail/send endpoint).

Env vars (set in .env or HF Secrets):
    SENDGRID_API_KEY    — SendGrid API key (starts with SG.)
    SENDGRID_FROM_EMAIL — verified sender address (e.g. noreply@aiprofile.app)
    SENDGRID_URL        — (optional) override the default SendGrid API endpoint
    IS_LOCAL            — when TRUE, logs the message but skips the actual send
"""

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class SendGridService:

    def send(self, to_email: str, subject: str, body_text: str, body_html: str | None = None) -> None:
        """
        Send an email via SendGrid.

        body_text is always required (plain-text fallback).
        body_html is optional; when provided, recipients with HTML-capable clients
        will see the richer version.

        Silently skips (with a log warning) if:
          - IS_LOCAL is True
          - SENDGRID_API_KEY is not configured
          - SENDGRID_FROM_EMAIL is not configured
        """
        logger.info("SendGrid: to=%s subject=%s", to_email, subject)

        if settings.IS_LOCAL:
            logger.info("SendGrid: IS_LOCAL=true — skipping actual email send")
            return

        if not settings.SENDGRID_API_KEY:
            logger.warning("SendGrid: SENDGRID_API_KEY not set — email NOT sent to %s", to_email)
            return
        if not settings.SENDGRID_FROM_EMAIL:
            logger.warning("SendGrid: SENDGRID_FROM_EMAIL not set — email NOT sent to %s", to_email)
            return

        content = [{"type": "text/plain", "value": body_text}]
        if body_html:
            content.append({"type": "text/html", "value": body_html})

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from":             {"email": settings.SENDGRID_FROM_EMAIL},
            "subject":          subject,
            "content":          content,
        }

        try:
            resp = requests.post(
                settings.SENDGRID_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
                    "Content-Type":  "application/json",
                },
                timeout=10,
            )
            if resp.status_code in (200, 202):
                logger.info("SendGrid: email sent to %s (status=%d)", to_email, resp.status_code)
            else:
                logger.warning(
                    "SendGrid: API returned error (status=%d, body=%s)",
                    resp.status_code, resp.text[:300],
                )
        except Exception as e:
            logger.warning("SendGrid: send failed: %s", e)


# Module-level singleton
sendgrid_service = SendGridService()
