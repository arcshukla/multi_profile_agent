"""
payment_providers.py
--------------------
Payment provider abstraction for the donation system.

Today: UPI QR (upi_qr) — generates a QR code pointing to the platform's UPI VPA.

Future: Add Razorpay, Stripe, PayPal, etc. by:
  1. Writing a new XxxProvider class implementing the PaymentProvider protocol.
  2. Registering it: _PROVIDERS["xxx"] = XxxProvider()
  No other files need to change.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, TypedDict

logger = logging.getLogger(__name__)


# ── Result shape ──────────────────────────────────────────────────────────────

class ProviderResult(TypedDict):
    payment_mode:  str
    upi_uri:       Optional[str]   # populated by upi_qr only
    qr_path:       Optional[str]   # populated by upi_qr only
    provider_meta: dict            # arbitrary provider-specific data (razorpay order_id, etc.)


# ── Protocol (interface) ──────────────────────────────────────────────────────

class PaymentProvider(Protocol):
    mode: str

    def create_payment(self, record_id: str, amount: float, note: str) -> ProviderResult:
        """Generate or initiate a payment. Returns a ProviderResult."""
        ...


# ── UPI QR provider (Phase 1) ─────────────────────────────────────────────────

class UpiQrProvider:
    mode = "upi_qr"

    def create_payment(self, record_id: str, amount: float, note: str) -> ProviderResult:
        # Lazy imports to avoid circular dependency: billing_service imports this module
        from app.services.billing_service import _build_donation_upi_uri, _generate_qr  # noqa: PLC0415
        from app.core.config import settings  # noqa: PLC0415

        vpa  = settings.DONATION_UPI_VPA  or settings.UPI_VPA
        name = settings.DONATION_UPI_NAME or settings.UPI_PAYEE_NAME

        if not vpa:
            logger.warning("UpiQrProvider: no UPI VPA configured (DONATION_UPI_VPA / UPI_VPA) | record=%s", record_id)

        upi_uri = _build_donation_upi_uri(vpa, name, amount, record_id, note)
        qr_path = _generate_qr(record_id, upi_uri)

        logger.info("UpiQrProvider: QR generated | record=%s amount=%.2f qr=%s", record_id, amount, qr_path)
        return ProviderResult(
            payment_mode  = "upi_qr",
            upi_uri       = upi_uri,
            qr_path       = qr_path,
            provider_meta = {},
        )


# ── Registry ──────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, PaymentProvider] = {
    "upi_qr": UpiQrProvider(),
}


def get_provider(mode: str = "upi_qr") -> PaymentProvider:
    """Return the provider for the given mode. Raises ValueError for unknown modes."""
    if mode not in _PROVIDERS:
        raise ValueError(f"Unknown payment provider: {mode!r}. Available: {list(_PROVIDERS)}")
    return _PROVIDERS[mode]
