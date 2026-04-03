"""
test_user_service.py
--------------------
Unit tests for UserService — the owner registry stored in system/users.json.

Tests cover: add, get, list, update status, remove, resolve_session, slug uniqueness.
All file I/O is redirected to tmp_path via the conftest isolate_data_dirs fixture.
"""

import pytest

from app.core.constants import STATUS_ENABLED, STATUS_DISABLED, STATUS_DELETED
from app.services.user_service import UserService


@pytest.fixture
def svc():
    """Fresh UserService instance for each test (no singleton state leakage)."""
    return UserService()


# ── Add & get ─────────────────────────────────────────────────────────────────

def test_add_and_get_by_email(svc, isolate_data_dirs):
    ok, err = svc.add_user(email="alice@example.com", name="Alice", slug="alice", status=STATUS_ENABLED)
    assert ok is True
    assert not err   # empty string on success

    user = svc.get_user("alice@example.com")
    assert user is not None
    assert user.name == "Alice"
    assert user.slug == "alice"
    assert user.status == STATUS_ENABLED


def test_get_user_by_slug(svc, isolate_data_dirs):
    email = "bobbyslug@example.com"
    svc.add_user(email=email, name="Bob", slug="bob-unique", status=STATUS_ENABLED)
    user = svc.get_user_by_slug("bob-unique")
    assert user is not None
    assert user.email == email


def test_get_missing_user_returns_none(svc, isolate_data_dirs):
    assert svc.get_user("nobody@example.com") is None
    assert svc.get_user_by_slug("nobody") is None


# ── Duplicate guards ──────────────────────────────────────────────────────────

def test_duplicate_email_rejected(svc, isolate_data_dirs):
    svc.add_user(email="dup@example.com", name="First", slug="first", status=STATUS_ENABLED)
    ok, err = svc.add_user(email="dup@example.com", name="Second", slug="second", status=STATUS_ENABLED)
    assert ok is False
    assert err  # non-empty error message


def test_duplicate_slug_rejected(svc, isolate_data_dirs):
    svc.add_user(email="a@example.com", name="A", slug="same-slug", status=STATUS_ENABLED)
    ok, err = svc.add_user(email="b@example.com", name="B", slug="same-slug", status=STATUS_ENABLED)
    assert ok is False
    assert "slug" in (err or "").lower()


# ── Status updates ────────────────────────────────────────────────────────────

def test_update_status_disabled(svc, isolate_data_dirs):
    svc.add_user(email="c@example.com", name="C", slug="c-slug", status=STATUS_ENABLED)
    ok, err = svc.update_status("c-slug", STATUS_DISABLED)
    assert ok is True
    user = svc.get_user_by_slug("c-slug")
    assert user.status == STATUS_DISABLED


def test_update_status_deleted(svc, isolate_data_dirs):
    svc.add_user(email="d@example.com", name="D", slug="d-slug", status=STATUS_ENABLED)
    ok, _ = svc.update_status("d-slug", STATUS_DELETED)
    assert ok is True
    user = svc.get_user_by_slug("d-slug")
    assert user.status == STATUS_DELETED


def test_update_status_missing_slug_fails(svc, isolate_data_dirs):
    ok, _ = svc.update_status("ghost-slug", STATUS_DISABLED)
    assert ok is False


# ── Remove ────────────────────────────────────────────────────────────────────

def test_remove_user_by_slug(svc, isolate_data_dirs):
    svc.add_user(email="rem@example.com", name="Rem", slug="rem", status=STATUS_ENABLED)
    removed = svc.remove_user_by_slug("rem")
    assert removed is True
    assert svc.get_user_by_slug("rem") is None


def test_remove_nonexistent_returns_false(svc, isolate_data_dirs):
    assert svc.remove_user_by_slug("ghost") is False


# ── List ──────────────────────────────────────────────────────────────────────

def test_list_owners(svc, isolate_data_dirs):
    svc.add_user(email="e1@example.com", name="E1", slug="e1", status=STATUS_ENABLED)
    svc.add_user(email="e2@example.com", name="E2", slug="e2", status=STATUS_DISABLED)
    owners = svc.list_owners()
    slugs = [o.slug for o in owners]
    assert "e1" in slugs
    assert "e2" in slugs


# ── Session resolution ────────────────────────────────────────────────────────

def test_resolve_session_known_user(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    svc.add_user(email="sess@example.com", name="Sess", slug="sess", status=STATUS_ENABLED)
    result = svc.resolve_session("sess@example.com", "Sess")
    assert result is not None
    assert result["role"] == "owner"
    assert result["slug"] == "sess"


def test_resolve_session_admin_email(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["admin@example.com"])
    result = svc.resolve_session("admin@example.com", "Admin User")
    assert result is not None
    assert result["role"] == "admin"


def test_resolve_session_unknown_returns_none(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    result = svc.resolve_session("nobody@example.com", "Nobody")
    assert result is None


# ── Backup rotation ───────────────────────────────────────────────────────────

def test_backup_rotation_creates_bak1(svc, isolate_data_dirs):
    """After two saves, users.bak1.json must exist."""
    svc.add_user(email="bak@example.com", name="Bak", slug="bak", status=STATUS_ENABLED)
    svc.add_user(email="bak2@example.com", name="Bak2", slug="bak2", status=STATUS_ENABLED)
    from app.core.config import SYSTEM_DIR
    bak1 = SYSTEM_DIR / "users.bak1.json"
    assert bak1.exists(), "users.bak1.json should have been created by rotation"
