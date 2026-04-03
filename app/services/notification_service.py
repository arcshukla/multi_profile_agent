"""
notification_service.py
-----------------------
Centralised notification dispatch for all platform events.

Coordinates:
  - Pushover  — real-time push notifications to the platform admin
  - SendGrid  — email to profile owners for opt-in events

Chat service and registration flow use this service instead of calling
Pushover / SendGrid / email templates directly.

Dispatch rules:
  notify_lead              → Pushover only  (admin alert)
  notify_unknown_question  → Pushover  +  optional owner email (opt-in via prefs)
  notify_llm_error         → Pushover only  (admin alert)
  notify_new_registration  → Pushover only  (admin alert)
"""

from app.core.config import settings as _settings
from app.core.logging_config import get_logger
from app.utils.pushover_service import PushoverService
from app.utils.sendgrid_service import sendgrid_service

logger = get_logger(__name__)


class NotificationService:
    """
    Stateless notification dispatcher.

    PushoverService is instantiated once at construction time.
    Per-profile dependencies (preferences, user, email template) are imported
    lazily inside _maybe_email_owner to avoid circular imports at module load.
    """

    def __init__(self) -> None:
        self._pushover = PushoverService()

    # ── Public API ────────────────────────────────────────────────────────────

    def notify_lead(self, name: str, email: str, session_id: str = "") -> None:
        """Push notification when a visitor provides their contact details."""
        self._pushover.send(f"Lead captured [{session_id}]\n{name}\n{email}")

    def notify_unknown_question(
        self,
        question:   str,
        session_id: str = "",
        slug:       str = "",
    ) -> None:
        """
        Push notification + optional owner email for an unanswered question.

        Owner email is sent only when:
          1. slug is provided
          2. owner record exists and has opted in via preferences
        """
        self._pushover.send(f"Unknown question [{session_id}]\n{question}")
        if slug:
            self._maybe_email_owner(slug=slug, question=question, session_id=session_id)

    def notify_llm_error(
        self, error_type: str, details: str, session_id: str = ""
    ) -> None:
        """Push notification when the LLM API fails during a chat turn."""
        self._pushover.send(f"External error [{session_id}]: {error_type}\n{details}")

    def notify_new_registration(self, name: str, email: str, slug: str) -> None:
        """Push notification when a new owner self-registers (status: disabled)."""
        self._pushover.send(
            f"New profile registration\n"
            f"Name:  {name}\n"
            f"Email: {email}\n"
            f"URL:   /chat/{slug}\n"
            f"Status: disabled — review in admin panel."
        )

    def notify_donation_confirmed(
        self,
        slug:         str,
        donation_id:  str,
        amount:       float,
        confirmed_at: str,
    ) -> None:
        """
        Sent when admin confirms receipt of a voluntary donation from a free-tier owner.

        Actions:
          1. Pushover alert to platform admin (unconditional).
          2. Thank-you email to the profile owner via SendGrid.
          3. Marks email_sent=True on the donation record (idempotency).

        All failures are logged at ERROR and never propagate — the admin
        confirm flow must not break if email delivery fails.
        """
        logger.warning(
            "DONATION_CONFIRMED | slug=%s | donation=%s | amount=%.2f",
            slug, donation_id, amount,
        )
        self._pushover.send(
            f"Donation confirmed\n"
            f"Profile : {slug}\n"
            f"Amount  : \u20b9{amount:.2f}\n"
            f"Ref     : {donation_id}"
        )

        try:
            from app.services.user_service import user_service          # noqa: PLC0415
            from app.services.email_template_service import email_template_service  # noqa: PLC0415
            from app.services.billing_service import billing_service    # noqa: PLC0415

            owner = user_service.get_user_by_slug(slug)
            if not owner:
                logger.warning(
                    "DONATION_CONFIRMED | slug=%s | no owner record — thank-you email NOT sent",
                    slug,
                )
                return

            app_url = _settings.APP_URL.rstrip("/")
            vars_ = {
                "owner_name":   owner.name or owner.email,
                "amount":       f"{amount:.2f}",
                "donation_id":  donation_id,
                "confirmed_at": confirmed_at[:10],
                "support_email": _settings.SUPPORT_EMAIL,
                "owner_url":    f"{app_url}/owner/billing",
            }
            tmpl = email_template_service.get("donation_received")
            logger.warning(
                "DONATION_EMAIL | slug=%s | owner=%s | donation=%s | amount=%.2f",
                slug, owner.email, donation_id, amount,
            )
            sendgrid_service.send(
                to_email  = owner.email,
                subject   = tmpl["subject"].format(**vars_),
                body_text = tmpl["body_text"].format(**vars_),
                body_html = tmpl["body_html"].format(**vars_),
            )
            billing_service.mark_donation_email_sent(slug, donation_id)
        except Exception as e:
            logger.error(
                "Failed to send donation thank-you email | slug=%s | donation=%s: %s",
                slug, donation_id, e, exc_info=True,
            )

    # ── Private ───────────────────────────────────────────────────────────────

    def _maybe_email_owner(self, slug: str, question: str, session_id: str) -> None:
        """
        Email the profile owner about an unanswered question if they opted in.
        All failures are logged and never propagate — the chat flow must not break.
        """
        # Lazy imports to avoid circular dependency at module load time
        from app.services.preferences_service import preferences_service
        from app.services.user_service import user_service
        from app.services.email_template_service import email_template_service

        try:
            if not preferences_service.get(slug).get("notify_unanswered_email"):
                return

            owner = user_service.get_user_by_slug(slug)
            if not owner:
                logger.warning(
                    "OWNER_NOTIFY_UNANSWERED | slug=%s | no owner record found — email NOT sent",
                    slug,
                )
                return

            app_url = _settings.APP_URL.rstrip("/")
            vars_ = {
                "owner_name": owner.name or owner.email,
                "question":   question,
                "session_id": session_id,
                "slug":       slug,
                "chat_url":   f"{app_url}/chat/{slug}",
                "owner_url":  f"{app_url}/owner/preferences",
            }
            tmpl = email_template_service.get("unanswered_question")
            logger.warning(
                "OWNER_NOTIFY_UNANSWERED | slug=%s | owner=%s | question=%s",
                slug, owner.email, question,
            )
            sendgrid_service.send(
                to_email  = owner.email,
                subject   = tmpl["subject"].format(**vars_),
                body_text = tmpl["body_text"].format(**vars_),
                body_html = tmpl["body_html"].format(**vars_),
            )
        except Exception as e:
            logger.error(
                "Failed to send owner unanswered-question email | slug=%s: %s", slug, e,
                exc_info=True,
            )


# Singleton
notification_service = NotificationService()
