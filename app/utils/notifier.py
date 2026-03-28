"""
notifier.py
-----------
High-level notification helpers built on top of PushoverService.

Adapted from the root-level notifier.py to use this project's conventions
and extended with profile-platform–specific notification types.
"""

from app.utils.pushover_service import PushoverService


class Notifier:

    def __init__(self):
        self.provider = PushoverService()

    # ── Original notification types ────────────────────────────────────────────

    def notify_lead(self, name: str, email: str, session_id: str = "") -> None:
        msg = f"Lead captured [{session_id}]\n{name}\n{email}"
        self.provider.send(msg)

    def notify_unknown(self, question: str, session_id: str = "") -> None:
        msg = f"Unknown question [{session_id}]\n{question}"
        self.provider.send(msg)

    def notify_error(self, error_type: str, details: str, session_id: str = "") -> None:
        msg = f"External error [{session_id}]: {error_type}\n{details}"
        self.provider.send(msg)

    # ── Platform-specific notifications ───────────────────────────────────────

    def notify_new_registration(self, name: str, email: str, slug: str) -> None:
        """Notify admin when a new profile is self-registered (status: disabled)."""
        msg = (
            f"New profile registration\n"
            f"Name:  {name}\n"
            f"Email: {email}\n"
            f"URL:   /chat/{slug}\n"
            f"Status: disabled — review in admin panel."
        )
        self.provider.send(msg)


# Module-level singleton — import and call directly
notifier = Notifier()
