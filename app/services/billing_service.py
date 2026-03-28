"""
billing_service.py
------------------
Manages billing tiers, invoices, and UPI/QR code generation.

Storage:
  system/billing.json  — billing entries keyed by slug
  static/qr/           — QR PNG images, one per invoice

FREE profiles are never written to billing.json.
_get_entry() returns a default free BillingEntry for any missing slug.

Thread-safety: a single module-level lock guards all reads and writes.
"""

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

from app.core.config import STATIC_DIR, settings
from app.core.logging_config import get_logger
from app.models.billing_models import (
    BillingEntry, BillingStatusResponse, BillingTier, Invoice, InvoiceStatus,
)
from app.storage.hf_sync import hf_sync

logger = get_logger(__name__)

_STORE   = settings.BILLING_FILE
_QR_DIR  = STATIC_DIR / "qr"
_LOCK    = threading.Lock()

# Graceful degrade if Pillow / qrcode not installed
try:
    import qrcode as _qrcode
    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False
    logger.warning("qrcode[pil] not installed — QR images will not be generated")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class BillingService:
    """
    CRUD on billing.json + UPI/QR helpers.
    All public methods are thread-safe.
    """

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(_STORE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, data: dict) -> None:
        _STORE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        hf_sync.push_file(_STORE)

    def _get_entry(self, slug: str, data: dict) -> BillingEntry:
        raw = data.get(slug)
        if raw is None:
            return BillingEntry(slug=slug)
        return BillingEntry(**raw)

    def _set_entry(self, entry: BillingEntry, data: dict) -> None:
        data[entry.slug] = entry.model_dump()

    # ── UPI / QR ──────────────────────────────────────────────────────────────

    def _build_upi_uri(self, amount: float, invoice_id: str) -> str:
        vpa  = settings.UPI_VPA
        name = quote(settings.UPI_PAYEE_NAME, safe="")
        note = quote("Platform Fee", safe="")
        return (
            f"upi://pay?pa={vpa}&pn={name}"
            f"&am={amount:.2f}&cu=INR"
            f"&tn={note}&tr={invoice_id}"
        )

    def _generate_qr(self, invoice_id: str, upi_uri: str) -> str:
        """
        Save a QR PNG to static/qr/{invoice_id}.png.
        Returns the relative path string (e.g. "qr/inv_abc.png").
        If qrcode/Pillow is unavailable, returns the path string anyway
        (the file won't exist, the serve endpoint will re-attempt).
        """
        qr_path = f"qr/{invoice_id}.png"
        if not _QR_AVAILABLE:
            return qr_path
        try:
            _QR_DIR.mkdir(parents=True, exist_ok=True)
            img = _qrcode.make(upi_uri)
            img.save(str(_QR_DIR / f"{invoice_id}.png"))
        except Exception as exc:
            logger.error("QR generation failed for %s: %s", invoice_id, exc)
        return qr_path

    # ── Public read ───────────────────────────────────────────────────────────

    def get_entry(self, slug: str) -> BillingEntry:
        with _LOCK:
            return self._get_entry(slug, self._load())

    def get_status(self, slug: str) -> BillingStatusResponse:
        entry   = self.get_entry(slug)
        today   = date.today().isoformat()
        pending = [
            inv for inv in entry.invoices
            if inv.status == InvoiceStatus.PENDING
        ]
        billing_due = any(inv.due_date <= today for inv in pending)
        # latest invoice = most recently created
        latest = entry.invoices[-1] if entry.invoices else None
        return BillingStatusResponse(
            slug=slug,
            tier=entry.tier,
            billing_due=billing_due,
            latest_invoice=latest,
        )

    def is_billing_due(self, slug: str) -> bool:
        return self.get_status(slug).billing_due

    # ── Public write ──────────────────────────────────────────────────────────

    def set_tier(
        self,
        slug:        str,
        new_tier:    BillingTier,
        admin_email: str,
    ) -> BillingEntry:
        """
        Change billing tier for a profile.
        Upgrading from FREE automatically creates the first invoice.
        Downgrading to FREE does NOT cancel open invoices (audit trail preserved).
        """
        with _LOCK:
            data  = self._load()
            entry = self._get_entry(slug, data)

            if entry.tier == new_tier:
                return entry  # no-op

            was_free   = entry.tier == BillingTier.FREE
            entry.tier            = new_tier
            entry.tier_changed_at = _utcnow()
            entry.tier_changed_by = admin_email

            self._set_entry(entry, data)
            self._save(data)

        logger.info("Billing tier for %s changed to %s by %s", slug, new_tier, admin_email)

        # Auto-create first invoice when upgrading from free (outside the lock
        # because create_invoice() acquires its own lock)
        if was_free and new_tier != BillingTier.FREE:
            self.create_invoice(slug)

        return self.get_entry(slug)

    def create_invoice(
        self,
        slug:         str,
        amount:       Optional[float] = None,
        period_start: Optional[str]   = None,
        period_end:   Optional[str]   = None,
    ) -> Invoice:
        """
        Create a new invoice for the profile (rolling period).
        Raises ValueError if a PENDING invoice already exists.
        Raises ValueError if UPI_VPA is not configured.
        """
        if not settings.UPI_VPA:
            raise ValueError(
                "UPI_VPA is not configured. Set the UPI_VPA environment variable."
            )

        amount       = amount or settings.PLATFORM_FEE_INR
        today        = date.today()
        period_start = period_start or today.isoformat()
        period_end   = period_end   or (today + timedelta(days=settings.BILLING_INTERVAL_DAYS)).isoformat()
        due_date     = period_end

        with _LOCK:
            data  = self._load()
            entry = self._get_entry(slug, data)

            # Guard: no duplicate open invoices
            open_inv = [i for i in entry.invoices if i.status == InvoiceStatus.PENDING]
            if open_inv:
                raise ValueError("An open invoice already exists for this profile.")

            invoice_id = "inv_" + uuid4().hex[:8]
            upi_uri    = self._build_upi_uri(amount, invoice_id)
            qr_path    = self._generate_qr(invoice_id, upi_uri)

            invoice = Invoice(
                id           = invoice_id,
                amount       = amount,
                period_start = period_start,
                period_end   = period_end,
                due_date     = due_date,
                upi_uri      = upi_uri,
                qr_path      = qr_path,
                created_at   = _utcnow(),
            )
            entry.invoices.append(invoice)
            self._set_entry(entry, data)
            self._save(data)

        logger.info("Invoice %s created for %s (₹%.2f)", invoice_id, slug, amount)
        return invoice

    def confirm_payment(
        self,
        slug:        str,
        invoice_id:  str,
        admin_email: str,
    ) -> Invoice:
        """
        Mark an invoice as paid.
        Raises ValueError if the invoice is not found or already confirmed.
        """
        with _LOCK:
            data  = self._load()
            entry = self._get_entry(slug, data)

            inv = next((i for i in entry.invoices if i.id == invoice_id), None)
            if inv is None:
                raise ValueError(f"Invoice {invoice_id!r} not found for profile {slug!r}.")
            if inv.status == InvoiceStatus.PAID:
                raise ValueError(f"Invoice {invoice_id!r} is already confirmed as paid.")

            inv.status       = InvoiceStatus.PAID
            inv.paid_at      = _utcnow()
            inv.confirmed_by = admin_email

            self._set_entry(entry, data)
            self._save(data)

        logger.info("Invoice %s confirmed paid by %s", invoice_id, admin_email)
        return inv

    def regenerate_qr(self, slug: str, invoice_id: str) -> Optional[str]:
        """
        Regenerate a missing QR PNG from the stored UPI URI.
        Returns the qr_path if successful, None otherwise.
        Used when QR files are lost (e.g. HF Spaces restart).
        """
        entry = self.get_entry(slug)
        inv   = next((i for i in entry.invoices if i.id == invoice_id), None)
        if inv is None:
            return None
        self._generate_qr(invoice_id, inv.upi_uri)
        return inv.qr_path


billing_service = BillingService()
