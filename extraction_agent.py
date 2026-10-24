"""
Extraction Agent
Uses Claude's vision to extract structured invoice data.
Handles 6 invoice categories with category-specific schemas.
"""

import os
import json
import re
import uuid
import logging
from datetime import datetime
import httpx
import anthropic

logger = logging.getLogger(__name__)

# ── Category schemas ──────────────────────────────────────────────────────────
CATEGORY_SCHEMAS = {
    "HR": {
        "fields": [
            "invoice_id", "invoice_date", "due_date", "vendor_name",
            "vendor_address", "client_name", "client_address",
            "service_type", "consultant_name", "billing_rate",
            "total_hours", "subtotal", "gst_rate", "gst_amount",
            "total_amount", "currency", "payment_terms", "bank_details",
            "line_items"
        ],
        "keywords": ["recruitment", "hr consult", "placement", "staffing", "talent", "payroll"]
    },
    "Software": {
        "fields": [
            "invoice_id", "invoice_date", "due_date", "vendor_name",
            "vendor_address", "client_name", "client_address",
            "software_name", "license_type", "license_count",
            "license_period", "unit_price", "subtotal",
            "gst_rate", "gst_amount", "total_amount", "currency",
            "payment_terms", "bank_details", "line_items"
        ],
        "keywords": ["license", "software", "saas", "subscription", "microsoft", "adobe", "server"]
    },
    "Canteen": {
        "fields": [
            "invoice_id", "invoice_date", "vendor_name",
            "vendor_address", "client_name", "meal_type",
            "number_of_employees", "per_head_cost",
            "subtotal", "gst_rate", "gst_amount",
            "total_amount", "currency", "payment_terms", "line_items"
        ],
        "keywords": ["food", "canteen", "meal", "catering", "lunch", "dinner", "breakfast", "tiffin"]
    },
    "Admin": {
        "fields": [
            "invoice_id", "invoice_date", "due_date", "vendor_name",
            "vendor_address", "client_name", "work_description",
            "work_category", "labour_cost", "material_cost",
            "subtotal", "gst_rate", "gst_amount",
            "total_amount", "currency", "payment_terms", "line_items"
        ],
        "keywords": ["ad-hoc", "adhoc", "maintenance", "repair", "plumbing", "electrical",
                     "furniture", "carpentry", "painting", "civil"]
    },
    "Housekeeping": {
        "fields": [
            "invoice_id", "invoice_date", "due_date", "vendor_name",
            "vendor_address", "client_name", "service_description",
            "number_of_staff", "service_days", "rate_per_day",
            "consumables_cost", "subtotal", "gst_rate", "gst_amount",
            "total_amount", "currency", "payment_terms", "line_items"
        ],
        "keywords": ["housekeep", "cleaning", "sanitation", "hygiene", "janitorial", "sweeping"]
    },
    "Others": {
        "fields": [
            "invoice_id", "invoice_date", "due_date", "vendor_name",
            "vendor_address", "client_name", "service_description",
            "subtotal", "tax_amount", "total_amount", "currency",
            "payment_terms", "line_items"
        ],
        "keywords": ["cab", "taxi", "transport", "travel", "courier", "logistics"]
    }
}

EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data extraction specialist for a corporate back-office finance system.

Your responsibilities:
1. Extract ALL financial data from invoice images/PDFs with maximum accuracy
2. Classify the invoice into exactly one category: HR, Software, Canteen, Admin, Housekeeping, Others
3. Normalize all amounts to INR (convert if foreign currency using approximate rates)
4. Extract line items as structured arrays
5. Parse dates into ISO format (YYYY-MM-DD)
6. Return ONLY valid JSON — no markdown, no explanations

Classification logic:
- HR: recruitment, staffing, consultancy, placement, talent
- Software: software licenses, SaaS, subscriptions, IT products
- Canteen: food, meals, catering, tiffin services
- Admin: ad-hoc work, repairs, maintenance, civil work
- Housekeeping: cleaning, sanitation, janitorial services
- Others: transport, cab, courier, anything unclassified

CRITICAL RULES:
- Never fabricate data — use null for missing fields
- Extract exact amounts as numbers (no currency symbols)
- Preserve original invoice ID exactly as shown
- For foreign currency amounts, convert to INR and note original amount
- Return structured JSON matching the category schema

Responsible AI Notice: Extract only invoice-related financial data. Do not infer personal information beyond what is explicitly on the document."""


class ExtractionAgent:
    """Agent 1: Vision-based invoice data extraction."""

    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            http_client=httpx.Client(verify=False, timeout=60.0)
        )
        self.model = "claude-sonnet-4-20250514"

    def _detect_category(self, filename: str) -> str:
        """Pre-classify from filename as hint."""
        fname = filename.lower()
        if any(k in fname for k in ["hr", "talent", "recruit", "consult"]):
            return "HR"
        if any(k in fname for k in ["software", "license", "saas"]):
            return "Software"
        if any(k in fname for k in ["food", "canteen", "meal"]):
            return "Canteen"
        if any(k in fname for k in ["adhoc", "ad_hoc", "ad-hoc", "maintenance"]):
            return "Admin"
        if any(k in fname for k in ["house", "housekeep", "cleaning"]):
            return "Housekeeping"
        return "Others"

    def extract(self, b64_data: str, media_type: str, filename: str, batch_id: str) -> dict:
        """Extract structured data from invoice image/pdf."""
        hint_category = self._detect_category(filename)
        schema = CATEGORY_SCHEMAS[hint_category]

        prompt = f"""Extract all data from this invoice image and return a JSON object.

Filename hint suggests category: {hint_category}
Expected fields for this category: {json.dumps(schema['fields'])}

Return this exact JSON structure:
{{
  "category": "<HR|Software|Canteen|Admin|Housekeeping|Others>",
  "confidence": <0.0-1.0>,
  "extracted_data": {{
    "invoice_id": "<exact invoice number from document>",
    "invoice_date": "<YYYY-MM-DD>",
    "due_date": "<YYYY-MM-DD or null>",
    "vendor_name": "<vendor/supplier company name>",
    "vendor_address": "<full address>",
    "vendor_contact": "<phone/email if present>",
    "client_name": "<bill-to company name>",
    "client_address": "<bill-to address>",
    "currency": "<INR|USD|etc>",
    "subtotal": <number>,
    "tax_rate": <percentage number>,
    "tax_amount": <number>,
    "total_amount": <number>,
    "amount_in_inr": <total converted to INR>,
    "payment_terms": "<e.g. Net 30>",
    "payment_method": "<bank transfer|UPI|cheque|etc>",
    "bank_details": "<account details if present>",
    "line_items": [
      {{"description": "...", "quantity": ..., "unit_price": ..., "amount": ...}}
    ],
    "category_specific": {{
      // Add all category-specific fields here
    }},
    "raw_notes": "<any other relevant info>"
  }}
}}

Important: classify correctly regardless of filename hint. If it's clearly a cab invoice, set category to Others."""

        content = []

        # Add file content
        if media_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data}
            })
        else:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data}
            })
        content.append({"type": "text", "text": prompt})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}]
            )

            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            parsed = json.loads(raw)

            # Inject metadata
            parsed["filename"] = filename
            parsed["batch_id"] = batch_id
            parsed["processed_at"] = datetime.utcnow().isoformat()
            parsed["erp_status"] = "PENDING"

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error for {filename}: {e}\nRaw: {raw[:500]}")
            return self._fallback_record(filename, batch_id, f"JSON parse error: {e}")
        except Exception as e:
            logger.error(f"Extraction failed for {filename}: {e}")
            return self._fallback_record(filename, batch_id, str(e))

    def _fallback_record(self, filename: str, batch_id: str, error: str) -> dict:
        """Return a minimal record when extraction fails."""
        return {
            "category": "Others",
            "confidence": 0.0,
            "extraction_error": error,
            "filename": filename,
            "batch_id": batch_id,
            "processed_at": datetime.utcnow().isoformat(),
            "erp_status": "ERROR",
            "extracted_data": {
                "invoice_id": f"EXTRACT-ERR-{uuid.uuid4().hex[:6].upper()}",
                "vendor_name": "Unknown",
                "client_name": "Unknown",
                "total_amount": 0,
                "amount_in_inr": 0,
                "line_items": []
            }
        }


