"""
billing_models.py
-----------------
Pydantic models for the billing subsystem.

Phase 1: flat UPI/QR platform fee.
Phase 2 (future): configurable billing engine with multiple dimensions
  (token overage, chat message count, session count).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel


# ── Enums ──────────────────────────────────────────────────────────────────

class BillingTier(str, Enum):
    FREE             = "free"
    PAID_INDIVIDUAL  = "paid_individual"


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID    = "paid"
    OVERDUE = "overdue"


# ── Invoice ────────────────────────────────────────────────────────────────

class Invoice(BaseModel):
    id:           str            # "inv_{uuid8}"
    amount:       float          # INR
    currency:     str = "INR"
    period_start: str            # "YYYY-MM-DD"
    period_end:   str            # "YYYY-MM-DD"
    due_date:     str            # "YYYY-MM-DD" (= period_end for Phase 1)
    status:       InvoiceStatus = InvoiceStatus.PENDING
    upi_uri:      str            # full upi:// deep-link
    qr_path:      str            # relative to STATIC_DIR, e.g. "qr/inv_xxx.png"
    created_at:   str            # ISO UTC
    paid_at:      Optional[str] = None
    confirmed_by: Optional[str] = None   # admin email


# ── BillingEntry (per profile) ─────────────────────────────────────────────

class BillingEntry(BaseModel):
    slug:            str
    tier:            BillingTier = BillingTier.FREE
    tier_changed_at: Optional[str] = None
    tier_changed_by: Optional[str] = None
    invoices:        list[Invoice] = []


# ── API request / response shapes ─────────────────────────────────────────

class ChangeTierRequest(BaseModel):
    tier: BillingTier


class BillingStatusResponse(BaseModel):
    slug:           str
    tier:           BillingTier
    billing_due:    bool            # any PENDING invoice with due_date <= today
    latest_invoice: Optional[Invoice] = None
