"""
email_template_service.py
--------------------------
Manages named email templates editable by admins.

Storage: system/email_templates.json
  Falls back to built-in defaults when the file does not exist.
  Each template has a plain-text body and an HTML body.
  Admins may edit both bodies; placeholder variables ({...}) must be preserved.

Currently defined templates
---------------------------
  unanswered_question — sent to a profile owner when a visitor asks an
                        unanswered question and the owner has opted in.

Available placeholders (all templates)
--------------------------------------
  {owner_name}  — owner's display name
  {question}    — the question the visitor asked
  {session_id}  — chat session identifier
  {slug}        — profile slug
  {chat_url}    — public URL for the owner's chat page
  {owner_url}   — URL for the owner's portal (preferences / docs)
"""

import json

from app.core.config import SYSTEM_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_STORE = SYSTEM_DIR / "email_templates.json"

# ── Built-in defaults ─────────────────────────────────────────────────────────

_DEFAULTS: dict[str, dict] = {
    "unanswered_question": {
        "name":        "Unanswered Question",
        "description": (
            "Sent to the profile owner when a visitor asks a question the AI could not answer "
            "and the owner has opted in to email notifications. "
            "Available placeholders: {owner_name}, {question}, {session_id}, {slug}, "
            "{chat_url}, {owner_url}."
        ),
        "subject": "Unanswered question on your AI profile",
        "body_text": """\
Hi {owner_name},

A visitor asked a question your AI profile could not answer.

Question: {question}

Session : {session_id}
Profile : {slug}

Try your profile: {chat_url}
Manage your profile: {owner_url}

Tip: consider adding content to your documents to cover this topic.

— AI Profile Platform\
""",
        "body_html": """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e4e7;">

        <!-- Header -->
        <tr>
          <td style="background:#4f46e5;padding:24px 32px;">
            <p style="margin:0;font-size:18px;font-weight:600;color:#ffffff;">AI Profile Platform</p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:32px;">
            <p style="margin:0 0 6px;font-size:15px;color:#374151;">Hi {owner_name},</p>
            <p style="margin:0 0 24px;font-size:14px;color:#6b7280;">
              A visitor asked a question your AI profile couldn't answer.
            </p>

            <!-- Question block -->
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="background:#f9fafb;border-left:4px solid #4f46e5;border-radius:4px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Question asked</p>
                  <p style="margin:6px 0 0;font-size:15px;color:#111827;">{question}</p>
                </td>
              </tr>
            </table>

            <!-- Meta -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;border-top:1px solid #f3f4f6;padding-top:16px;">
              <tr>
                <td style="font-size:13px;color:#9ca3af;padding-bottom:5px;">
                  <strong style="color:#374151;">Profile:</strong>&nbsp;{slug}
                </td>
              </tr>
              <tr>
                <td style="font-size:13px;color:#9ca3af;">
                  <strong style="color:#374151;">Session:</strong>&nbsp;<span style="font-family:monospace;">{session_id}</span>
                </td>
              </tr>
            </table>

            <!-- Tip -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">
              <tr>
                <td style="background:#eff6ff;border-radius:8px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;color:#1e40af;">
                    <strong>Tip:</strong> Add content to your profile documents to help your AI answer questions like this in future.
                  </p>
                </td>
              </tr>
            </table>

            <!-- CTAs -->
            <p style="margin:28px 0 0;text-align:center;">
              <a href="{chat_url}"
                 style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;
                        font-size:14px;font-weight:600;padding:10px 24px;border-radius:8px;margin-right:8px;">
                Try Your Profile
              </a>
              <a href="{owner_url}"
                 style="display:inline-block;background:#f3f4f6;color:#374151;text-decoration:none;
                        font-size:14px;font-weight:600;padding:10px 24px;border-radius:8px;">
                Owner Portal
              </a>
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #f3f4f6;">
            <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
              You received this because you opted in to unanswered-question alerts.<br>
              Manage your preferences at <a href="{owner_url}" style="color:#6366f1;">owner preferences</a>.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>\
""",
    },
}


class EmailTemplateService:
    """
    Load and save admin-editable email templates.

    Admins may change subject, body_text, and body_html for each named template.
    Placeholder variables (e.g. {question}) must be preserved for the sending
    code to substitute them correctly.
    """

    def get_templates(self) -> dict[str, dict]:
        """
        Return all templates.
        Falls back to built-in defaults if the store file is absent or corrupt.
        """
        if not _STORE.exists():
            return _copy(_DEFAULTS)

        try:
            raw = json.loads(_STORE.read_text(encoding="utf-8"))
            merged = _copy(_DEFAULTS)
            for key, val in raw.items():
                if key in merged and isinstance(val, dict):
                    for field in ("subject", "body_text", "body_html"):
                        if field in val:
                            merged[key][field] = val[field]
            return merged
        except Exception as e:
            logger.warning("Failed to read email_templates.json — using defaults: %s", e)
            return _copy(_DEFAULTS)

    def get(self, name: str) -> dict | None:
        """Return a single template by name, or None if not found."""
        return self.get_templates().get(name)

    def update_template(self, name: str, subject: str, body_text: str, body_html: str) -> bool:
        """
        Update one template's subject, body_text, and body_html and persist.
        Returns True on success, False if name is unknown.
        """
        if name not in _DEFAULTS:
            logger.warning("Unknown email template name: '%s'", name)
            return False

        templates = self.get_templates()
        templates[name]["subject"]   = subject
        templates[name]["body_text"] = body_text
        templates[name]["body_html"] = body_html
        return self._save(templates)

    def restore_defaults(self, name: str | None = None) -> bool:
        """
        Restore templates to defaults.
        If name is given, restores only that template; otherwise restores all.
        """
        if name:
            if name not in _DEFAULTS:
                logger.warning("Unknown email template name for restore: '%s'", name)
                return False
            templates = self.get_templates()
            templates[name]["subject"]   = _DEFAULTS[name]["subject"]
            templates[name]["body_text"] = _DEFAULTS[name]["body_text"]
            templates[name]["body_html"] = _DEFAULTS[name]["body_html"]
            result = self._save(templates)
            if result:
                logger.info("Email template '%s' restored to default", name)
            return result
        else:
            try:
                if _STORE.exists():
                    _STORE.unlink()
                logger.info("All email templates restored to defaults")
                return True
            except Exception as e:
                logger.error("Failed to restore email template defaults: %s", e)
                return False

    def _save(self, templates: dict) -> bool:
        try:
            payload = {
                k: {
                    "subject":   v["subject"],
                    "body_text": v["body_text"],
                    "body_html": v["body_html"],
                }
                for k, v in templates.items()
            }
            _STORE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Email templates saved to %s", _STORE)
            return True
        except Exception as e:
            logger.error("Failed to save email templates: %s", e)
            return False


def _copy(d: dict) -> dict:
    return {k: dict(v) for k, v in d.items()}


# Singleton
email_template_service = EmailTemplateService()
