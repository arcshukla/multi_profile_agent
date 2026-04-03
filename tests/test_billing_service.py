"""
test_billing_service.py
-----------------------
Unit tests for BillingService.

Covers: tier changes, invoice creation, payment confirmation, QR generation degradation,
backup rotation, duplicate invoice guard, missing UPI_VPA guard.
"""

import pytest

from app.models.billing_models import BillingTier, InvoiceStatus
from app.services.billing_service import BillingService


@pytest.fixture
def svc():
    return BillingService()


# ── Tier defaults ─────────────────────────────────────────────────────────────

def test_default_entry_is_free(svc, isolate_data_dirs):
    entry = svc.get_entry("new-slug")
    assert entry.tier == BillingTier.FREE
    assert entry.invoices == []


# ── Tier change ───────────────────────────────────────────────────────────────

def test_set_tier_basic(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")

    entry = svc.set_tier("slug1", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    assert entry.tier == BillingTier.PAID_INDIVIDUAL


def test_set_tier_noop_when_same(svc, isolate_data_dirs):
    entry = svc.get_entry("slug2")
    assert entry.tier == BillingTier.FREE
    # Setting FREE → FREE is a no-op
    entry2 = svc.set_tier("slug2", BillingTier.FREE, "admin@example.com")
    assert entry2.tier == BillingTier.FREE
    assert entry2.invoices == []


# ── Invoice creation ──────────────────────────────────────────────────────────

def test_create_invoice_requires_upi_vpa(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "")
    with pytest.raises(ValueError, match="UPI_VPA"):
        svc.create_invoice("slug3")


def test_create_invoice_success(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")
    # First upgrade to PAID so billing is active
    svc.set_tier("slug4", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    # set_tier already created one invoice; confirm it then create a second
    entry = svc.get_entry("slug4")
    if entry.invoices:
        svc.confirm_payment("slug4", entry.invoices[0].id, "admin@example.com")

    invoice = svc.create_invoice("slug4", amount=10.0)
    assert invoice.id.startswith("inv_")
    assert invoice.amount == 10.0
    assert invoice.status == InvoiceStatus.PENDING


def test_create_duplicate_invoice_rejected(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")
    svc.set_tier("slug5", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    # set_tier already created one PENDING invoice — creating another should fail
    with pytest.raises(ValueError, match="open invoice"):
        svc.create_invoice("slug5", amount=10.0)


# ── Confirm payment ───────────────────────────────────────────────────────────

def test_confirm_payment(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")
    svc.set_tier("slug6", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    entry = svc.get_entry("slug6")
    inv_id = entry.invoices[0].id

    confirmed = svc.confirm_payment("slug6", inv_id, "admin@example.com")
    assert confirmed.status == InvoiceStatus.PAID
    assert confirmed.confirmed_by == "admin@example.com"


def test_confirm_already_paid_raises(svc, isolate_data_dirs, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")
    svc.set_tier("slug7", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    entry = svc.get_entry("slug7")
    inv_id = entry.invoices[0].id
    svc.confirm_payment("slug7", inv_id, "admin@example.com")

    with pytest.raises(ValueError, match="already confirmed"):
        svc.confirm_payment("slug7", inv_id, "admin@example.com")


def test_confirm_missing_invoice_raises(svc, isolate_data_dirs):
    with pytest.raises(ValueError, match="not found"):
        svc.confirm_payment("slug8", "inv_nonexistent", "admin@example.com")


# ── QR degradation ───────────────────────────────────────────────────────────

def test_generate_qr_returns_path_without_pillow(svc, isolate_data_dirs, monkeypatch):
    """When qrcode is unavailable, _generate_qr should return path string silently."""
    monkeypatch.setattr("app.services.billing_service._QR_AVAILABLE", False)
    path = svc._generate_qr("inv_test", "upi://pay?pa=test@upi")
    assert path == "qr/inv_test.png"


# ── Billing status ────────────────────────────────────────────────────────────

def test_billing_not_due_for_free_tier(svc, isolate_data_dirs):
    assert svc.is_billing_due("free-slug") is False


# ── Backup rotation ───────────────────────────────────────────────────────────

def test_backup_rotation_creates_bak1(svc, isolate_data_dirs, monkeypatch):
    """After two writes, billing.bak1.json must exist."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPI_VPA", "test@upi")
    svc.set_tier("bak-slug", BillingTier.PAID_INDIVIDUAL, "admin@example.com")
    svc.set_tier("bak-slug", BillingTier.FREE, "admin@example.com")  # triggers second _save
    bak1 = isolate_data_dirs / "system" / "billing.bak1.json"
    assert bak1.exists(), "billing.bak1.json should have been created by rotation"
