"""
Validation Agent — Fixed
Applies 8 business rule validators to extracted invoice data.

Key fixes:
- V2 Date: Only hard-fails on NEW invoices (is_edit=False).
           On re-validation after edit, date outside range is a WARNING
           so user can correct other fields without being blocked by a
           date they may have already fixed.
- V5 Uniqueness: Checks _submitted_ IDs only (not all stored IDs).
                 Same invoice can be re-validated many times without
                 triggering a false duplicate error.
"""

import re
import logging
from datetime import datetime, date
from typing import Any

logger = logging.getLogger(__name__)

AUDIT_THRESHOLD_INR = 100_000
MIN_INVOICE_DATE    = date(2026, 1, 1)
MAX_FUTURE_DAYS     = 365
GST_VALID_RATES     = {0, 5, 12, 18, 28}
REQUIRED_FIELDS     = [
    "invoice_id", "invoice_date", "vendor_name",
    "client_name", "total_amount"
]


class ValidationAgent:
    """Agent 2: Business rule validation engine."""

    def __init__(self, store):
        self.store = store

    # ── Public ────────────────────────────────────────────────────────────────

    def validate(self, invoice: dict, batch_id: str, is_edit: bool = False) -> dict:
        """
        Run all validators and attach results.

        Args:
            invoice  : invoice dict (with extracted_data)
            batch_id : current batch identifier
            is_edit  : True when called after a user edits fields.
                       Relaxes certain hard-fail rules to warnings so
                       users can fix issues iteratively.
        """
        data   = invoice.get("extracted_data", {})
        record_id = invoice.get("record_id")          # None on first upload
        checks = []

        checks.append(self._v1_required_fields(data))
        checks.append(self._v2_date_range(data, is_edit=is_edit))
        checks.append(self._v3_amount_threshold(data))
        checks.append(self._v4_vendor_client_same(data))
        checks.append(self._v5_invoice_id_unique(data, record_id))
        checks.append(self._v6_amount_arithmetic(data))
        checks.append(self._v7_gst_rate(data))
        checks.append(self._v8_numeric_sanity(data))

        statuses = [c["status"] for c in checks]
        if "FAILED" in statuses:
            overall = "FAILED"
        elif "WARNING" in statuses:
            overall = "WARNING"
        else:
            overall = "PASSED"

        invoice["validation"] = {
            "overall_status": overall,
            "checks": checks,
            "validated_at": datetime.utcnow().isoformat(),
            "total_checks": len(checks),
            "passed":   statuses.count("PASSED"),
            "warnings": statuses.count("WARNING"),
            "failed":   statuses.count("FAILED"),
        }

        # Only update erp_status if not already SUBMITTED
        if invoice.get("erp_status") != "SUBMITTED":
            if overall == "PASSED":
                invoice["erp_status"] = "READY"
            elif overall == "WARNING":
                invoice["erp_status"] = "REVIEW"
            else:
                invoice["erp_status"] = "FAILED"

        return invoice

    # ── Validators ────────────────────────────────────────────────────────────

    def _v1_required_fields(self, data: dict) -> dict:
        """V1: All required fields must be present and non-empty."""
        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, "")).strip()]
        if missing:
            return self._fail("V1", "Required Fields Check",
                              f"Missing required fields: {', '.join(missing)}", "critical")
        return self._pass("V1", "Required Fields Check", "All required fields present")

    def _v2_date_range(self, data: dict, is_edit: bool = False) -> dict:
        """
        V2: Invoice date must be >= Jan 2026.

        On first upload  → FAILED  (strict, blocks submission)
        On edit/re-validate → WARNING (user already saw the error;
                              don't prevent fixing other fields)
        """
        raw_date = str(data.get("invoice_date", "")).strip()
        if not raw_date:
            return self._fail("V2", "Date Range Check", "Invoice date is missing", "high")

        try:
            inv_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return self._fail("V2", "Date Range Check",
                              f"Cannot parse date '{raw_date}' — use YYYY-MM-DD format", "high")

        today = date.today()

        if inv_date < MIN_INVOICE_DATE:
            msg = (
                f"Invoice date {inv_date} is before {MIN_INVOICE_DATE}. "
                "Please update the date to Jan 2026 or later."
            )
            # After an edit, downgrade to WARNING so other fields can still be saved
            return (self._warn if is_edit else self._fail)(
                "V2", "Date Range Check", msg, "high"
            )

        if (inv_date - today).days > MAX_FUTURE_DAYS:
            return self._warn("V2", "Date Range Check",
                              f"Invoice date {inv_date} is more than {MAX_FUTURE_DAYS} days ahead — verify.",
                              "medium")

        return self._pass("V2", "Date Range Check", f"Invoice date {inv_date} is valid")

    def _v3_amount_threshold(self, data: dict) -> dict:
        """V3: Amounts > ₹1,00,000 require audit — cannot be submitted directly."""
        amount = self._to_float(data.get("amount_in_inr") or data.get("total_amount"))
        if amount is None:
            return self._fail("V3", "Amount Threshold Check",
                              "Total amount is missing or unreadable", "critical")
        if amount <= 0:
            return self._fail("V3", "Amount Threshold Check",
                              f"Amount ₹{amount:.2f} must be a positive number", "critical")
        if amount > AUDIT_THRESHOLD_INR:
            return self._fail(
                "V3", "Amount Threshold Check",
                f"Amount ₹{amount:,.2f} exceeds the ₹1,00,000 audit threshold. "
                "This invoice must go through the AUDIT PROCESS — escalate to Finance Controller.",
                "critical"
            )
        return self._pass("V3", "Amount Threshold Check",
                          f"Amount ₹{amount:,.2f} is within the acceptable limit")

    def _v4_vendor_client_same(self, data: dict) -> dict:
        """V4: Vendor and client must be different entities."""
        vendor = self._normalise(data.get("vendor_name", ""))
        client = self._normalise(data.get("client_name", ""))

        if not vendor or not client:
            return self._warn("V4", "Vendor-Client Check",
                              "Cannot verify — vendor or client name is missing", "medium")

        if vendor == client or self._similarity(vendor, client) > 0.7:
            return self._fail(
                "V4", "Vendor-Client Check",
                f"Vendor '{data.get('vendor_name')}' and client '{data.get('client_name')}' "
                "appear to be the same entity. Self-billing is not permitted.", "critical"
            )
        return self._pass("V4", "Vendor-Client Check", "Vendor and client are distinct entities")

    def _v5_invoice_id_unique(self, data: dict, record_id: str) -> dict:
        """
        V5: Invoice ID must not already be SUBMITTED to ERP.

        This check looks only at the _submitted_ids index in the store,
        which is populated only when a user clicks 'Submit to ERP'.
        Therefore:
        - Uploading the same file twice → no duplicate error (both are unsubmitted)
        - Re-validating after editing   → no duplicate error (still unsubmitted)
        - Trying to submit an ID that was already submitted → FAILED
        """
        inv_id = str(data.get("invoice_id", "")).strip()
        if not inv_id or inv_id.upper().startswith("EXTRACT-ERR"):
            return self._warn("V5", "Invoice ID Uniqueness",
                              "Invoice ID is missing or auto-generated — uniqueness cannot be verified",
                              "medium")

        if self.store.invoice_id_submitted(inv_id):
            return self._fail(
                "V5", "Invoice ID Uniqueness",
                f"Invoice ID '{inv_id}' has already been submitted to ERP. "
                "Duplicate submissions are not allowed.", "critical"
            )
        return self._pass("V5", "Invoice ID Uniqueness",
                          f"Invoice ID '{inv_id}' is unique and not yet submitted")

    def _v6_amount_arithmetic(self, data: dict) -> dict:
        """V6: Subtotal + Tax should approximately equal Total (±2% tolerance)."""
        subtotal = self._to_float(data.get("subtotal"))
        tax      = self._to_float(data.get("tax_amount"))
        total    = self._to_float(data.get("total_amount"))

        if subtotal is None or total is None:
            return self._warn("V6", "Amount Arithmetic Check",
                              "Cannot verify arithmetic — subtotal or total is missing", "low")

        expected  = (subtotal or 0) + (tax or 0)
        tolerance = max(total * 0.02, 1.0)   # 2% or ₹1 minimum

        if total > 0 and abs(expected - total) > tolerance:
            return self._warn(
                "V6", "Amount Arithmetic Check",
                f"Subtotal({subtotal}) + Tax({tax}) = {expected:.2f} but Total = {total:.2f}. "
                f"Difference: ₹{abs(expected - total):.2f}. Please verify line items.", "medium"
            )
        return self._pass("V6", "Amount Arithmetic Check",
                          f"Arithmetic verified: {subtotal} + {tax} ≈ {total}")

    def _v7_gst_rate(self, data: dict) -> dict:
        """V7: GST rate should be a standard Indian slab (INR invoices only)."""
        currency = str(data.get("currency", "INR")).upper()
        if currency not in ("INR", ""):
            return self._pass("V7", "GST Rate Validation",
                              f"Non-INR currency ({currency}) — GST check skipped")

        rate = self._to_float(data.get("tax_rate") or data.get("gst_rate"))
        if rate is None:
            return self._warn("V7", "GST Rate Validation",
                              "GST/tax rate not found on invoice", "low")

        if rate not in GST_VALID_RATES:
            return self._warn(
                "V7", "GST Rate Validation",
                f"GST rate {rate}% is not a standard slab (0%, 5%, 12%, 18%, 28%). "
                "Verify the applicable rate for this service category.", "medium"
            )
        return self._pass("V7", "GST Rate Validation",
                          f"GST rate {rate}% is a valid standard slab")

    def _v8_numeric_sanity(self, data: dict) -> dict:
        """V8: All monetary amounts and quantities must be positive numbers."""
        issues = []
        for field in ("subtotal", "total_amount", "tax_amount", "amount_in_inr"):
            val = self._to_float(data.get(field))
            if val is not None and val < 0:
                issues.append(f"{field} is negative (₹{val})")

        for i, item in enumerate(data.get("line_items", []), 1):
            for sub in ("quantity", "unit_price", "amount"):
                val = self._to_float(item.get(sub))
                if val is not None and val < 0:
                    issues.append(f"Line {i} {sub} is negative ({val})")

        if issues:
            return self._fail("V8", "Numeric Sanity Check",
                              "Negative values found: " + "; ".join(issues), "high")
        return self._pass("V8", "Numeric Sanity Check",
                          "All numeric values are valid positive numbers")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_float(val: Any):
        if val is None:
            return None
        try:
            cleaned = re.sub(r"[₹$,\s]", "", str(val))
            return float(cleaned) if cleaned else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalise(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        tokens_a, tokens_b = set(a.split()), set(b.split())
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))

    @staticmethod
    def _pass(vid, name, msg):
        return {"id": vid, "name": name, "status": "PASSED",  "message": msg}

    @staticmethod
    def _warn(vid, name, msg, severity="medium"):
        return {"id": vid, "name": name, "status": "WARNING", "message": msg, "severity": severity}

    @staticmethod
    def _fail(vid, name, msg, severity="high"):
        return {"id": vid, "name": name, "status": "FAILED",  "message": msg, "severity": severity}
