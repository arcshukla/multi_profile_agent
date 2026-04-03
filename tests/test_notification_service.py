"""
test_notification_service.py
-----------------------------
Unit tests for NotificationService.

Verifies:
  - Pushover is called with the right message shape for each notification type
  - Owner email is sent when the owner has opted in via preferences
  - Owner email is NOT sent when opted out
  - Owner email is NOT sent when no owner record exists
  - SendGrid failures do not propagate (fire-and-forget)
"""

from unittest.mock import MagicMock, patch, call
import pytest

from app.services.notification_service import NotificationService


@pytest.fixture
def svc(monkeypatch):
    """NotificationService with Pushover replaced by a MagicMock."""
    service = NotificationService()
    mock_pushover = MagicMock()
    monkeypatch.setattr(service, "_pushover", mock_pushover)
    return service, mock_pushover


# ── notify_lead ───────────────────────────────────────────────────────────────

def test_notify_lead_pushover_called(svc):
    service, mock_push = svc
    service.notify_lead(name="Alice", email="alice@example.com", session_id="ses1")
    mock_push.send.assert_called_once()
    msg = mock_push.send.call_args[0][0]
    assert "alice@example.com" in msg
    assert "ses1" in msg


# ── notify_unknown_question ───────────────────────────────────────────────────

def test_notify_unknown_question_pushover_called(svc):
    service, mock_push = svc
    service.notify_unknown_question(question="What is your salary?", session_id="ses2")
    mock_push.send.assert_called_once()
    msg = mock_push.send.call_args[0][0]
    assert "salary" in msg
    assert "ses2" in msg


def test_notify_unknown_sends_owner_email_when_opted_in(svc, monkeypatch, isolate_data_dirs):
    service, mock_push = svc

    # Stub preferences → opted in
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = {"notify_unanswered_email": True, "chat_history_limit": 10}
    monkeypatch.setattr("app.services.notification_service.notification_service", service)

    # Stub user_service → returns owner record
    mock_user = MagicMock()
    mock_user.email = "owner@example.com"
    mock_user.name  = "Owner"
    mock_user_svc   = MagicMock()
    mock_user_svc.get_user_by_slug.return_value = mock_user

    # Stub email_template_service
    mock_tmpl_svc = MagicMock()
    mock_tmpl_svc.get.return_value = {
        "subject":   "Q: {question}",
        "body_text": "{question} from {owner_name}",
        "body_html": "<p>{question}</p>",
    }

    # Stub sendgrid
    mock_sg = MagicMock()

    with patch("app.services.notification_service.sendgrid_service", mock_sg), \
         patch("app.services.preferences_service.preferences_service", mock_prefs), \
         patch("app.services.user_service.user_service", mock_user_svc), \
         patch("app.services.email_template_service.email_template_service", mock_tmpl_svc):
        service._maybe_email_owner(
            slug="test-slug", question="What is your salary?", session_id="ses3"
        )

    mock_sg.send.assert_called_once()
    _, kwargs = mock_sg.send.call_args
    assert kwargs["to_email"] == "owner@example.com"


def test_notify_unknown_no_owner_email_when_opted_out(svc, monkeypatch, isolate_data_dirs):
    service, mock_push = svc

    mock_prefs = MagicMock()
    mock_prefs.get.return_value = {"notify_unanswered_email": False, "chat_history_limit": 10}
    mock_sg = MagicMock()

    with patch("app.services.preferences_service.preferences_service", mock_prefs), \
         patch("app.services.notification_service.sendgrid_service", mock_sg):
        service._maybe_email_owner(
            slug="slug-opted-out", question="A question", session_id=""
        )

    mock_sg.send.assert_not_called()


def test_notify_unknown_no_email_when_owner_missing(svc, monkeypatch, isolate_data_dirs):
    service, mock_push = svc

    mock_prefs = MagicMock()
    mock_prefs.get.return_value = {"notify_unanswered_email": True, "chat_history_limit": 10}
    mock_user_svc = MagicMock()
    mock_user_svc.get_user_by_slug.return_value = None
    mock_sg = MagicMock()

    with patch("app.services.preferences_service.preferences_service", mock_prefs), \
         patch("app.services.user_service.user_service", mock_user_svc), \
         patch("app.services.notification_service.sendgrid_service", mock_sg):
        service._maybe_email_owner(
            slug="slug-no-owner", question="A question", session_id=""
        )

    mock_sg.send.assert_not_called()


def test_maybe_email_owner_swallows_sendgrid_exception(svc, monkeypatch, isolate_data_dirs):
    """SendGrid failure must never propagate — chat flow must not break."""
    service, _ = svc

    mock_prefs = MagicMock()
    mock_prefs.get.return_value = {"notify_unanswered_email": True, "chat_history_limit": 10}
    mock_user = MagicMock()
    mock_user.email = "owner@example.com"
    mock_user.name  = "Owner"
    mock_user_svc   = MagicMock()
    mock_user_svc.get_user_by_slug.return_value = mock_user
    mock_tmpl_svc = MagicMock()
    mock_tmpl_svc.get.return_value = {
        "subject":   "Q: {question}",
        "body_text": "{question} from {owner_name}",
        "body_html": "<p>{question}</p>",
    }
    mock_sg = MagicMock()
    mock_sg.send.side_effect = RuntimeError("SendGrid down")

    # Should not raise
    with patch("app.services.preferences_service.preferences_service", mock_prefs), \
         patch("app.services.user_service.user_service", mock_user_svc), \
         patch("app.services.email_template_service.email_template_service", mock_tmpl_svc), \
         patch("app.services.notification_service.sendgrid_service", mock_sg):
        service._maybe_email_owner(
            slug="slug-sg-fail", question="Will this break?", session_id=""
        )


# ── notify_llm_error ──────────────────────────────────────────────────────────

def test_notify_llm_error(svc):
    service, mock_push = svc
    service.notify_llm_error("LLM call failed", "quota exceeded", "ses4")
    mock_push.send.assert_called_once()
    msg = mock_push.send.call_args[0][0]
    assert "quota" in msg


# ── notify_new_registration ───────────────────────────────────────────────────

def test_notify_new_registration(svc):
    service, mock_push = svc
    service.notify_new_registration(name="New Owner", email="new@example.com", slug="new-owner")
    mock_push.send.assert_called_once()
    msg = mock_push.send.call_args[0][0]
    assert "new@example.com" in msg
    assert "disabled" in msg.lower()
