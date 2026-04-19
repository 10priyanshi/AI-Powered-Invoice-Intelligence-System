"""
Chat Agent — Fixed
Answers questions about ERP invoices and general back-office finance ops.
Enforces topic scope — refuses out-of-scope queries (Responsible AI).

Fix: _build_context crashed on amount formatting when value was a raw
     string/None from JSON. All amount handling now goes through _to_float().
"""

import re
import os
import logging
import httpx
import anthropic

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are FinBot — a specialist AI assistant for back-office invoice processing and finance operations at a corporate company.

## YOUR ROLE
You help finance teams and back-office staff with:
1. Questions about processed invoices (amounts, vendors, status, validations)
2. General accounts payable and receivable processes
3. Invoice processing workflows and ERP submission guidelines
4. GST, taxation, and compliance questions (Indian context)
5. Vendor management and payment terms
6. Expense categories: HR, Software, Canteen, Admin, Housekeeping, Transport
7. Audit procedures and financial controls
8. Budget tracking and spend analysis
9. Document handling and archival policies

## HOW TO ANSWER INVOICE QUERIES
When the user asks about invoices (e.g. "list invoices above 5000", "show pending"), use the INVOICE DATA CONTEXT provided at the end of the message.
- Always respond with a clear, structured list when listing invoices
- Show: Invoice ID, Vendor, Amount (₹), Category, ERP Status
- Filter correctly based on the user's criteria (amount threshold, status, category, etc.)
- If no invoices match the filter, say so explicitly
- Amounts are in INR unless stated otherwise

## STRICT SCOPE RULES (Responsible AI)
You MUST REFUSE questions that are:
- Personal advice unrelated to finance/back-office
- Legal advice beyond invoice compliance
- Politics, religion, controversial social topics
- Technical programming help unrelated to finance systems
- Medical or psychological queries

When refusing: "I'm FinBot, specialized for back-office finance. I can't help with [topic], but happy to assist with invoices, expenses, or financial queries."

## TONE
Professional but approachable. Finance terms are fine — the user is a finance professional.
Be precise with numbers. Always show amounts as ₹ INR."""


class ChatAgent:
    """Agent 4: Conversational back-office assistant."""

    def __init__(self, store):
        self.store = store
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            http_client=httpx.Client(verify=False, timeout=60.0)
        )
        self.model = "claude-sonnet-4-20250514"

    def respond(self, user_message: str, session_id: str, history: list) -> dict:
        """Generate a contextual response to the user's query."""

        # Always build full invoice context for any finance-related query
        invoice_context = self._build_context(user_message)

        # Build conversation history (last 8 turns)
        messages = []
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant"):
                messages.append({"role": h["role"], "content": str(h["content"])})

        # Enrich user message with live invoice data
        if invoice_context:
            enriched = (
                f"{user_message}\n\n"
                f"[LIVE INVOICE DATA — use this to answer the question accurately]\n"
                f"{invoice_context}"
            )
        else:
            enriched = user_message

        messages.append({"role": "user", "content": enriched})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=CHAT_SYSTEM_PROMPT,
                messages=messages
            )

            answer = response.content[0].text.strip()
            return {
                "response": answer,
                "session_id": session_id,
                "sources": self._extract_invoice_ids(answer),
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens
                }
            }

        except anthropic.APIStatusError as e:
            logger.error(f"Anthropic API error [{e.status_code}]: {e.message}")
            return self._error_response(session_id, f"API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            logger.error(f"Anthropic connection error: {e}")
            return self._error_response(session_id, "Connection to AI service failed. Check your network/API key.")
        except Exception as e:
            logger.error(f"Chat agent unexpected error: {e}", exc_info=True)
            return self._error_response(session_id, str(e))

    # ── Context builder ───────────────────────────────────────────────────────

    def _build_context(self, query: str) -> str:
        """
        Build structured invoice context injected into every finance-related query.
        Pulls ALL invoices from store (not just submitted) so FinBot can answer
        questions about pending/failed invoices too.
        """
        # Broad keyword check — include most finance-related queries
        invoice_keywords = [
            "invoice", "vendor", "amount", "total", "payment", "status",
            "gst", "tax", "bill", "pending", "approved", "audit", "erp",
            "submit", "validate", "batch", "category", "list", "show",
            "more than", "above", "below", "less than", "5k", "10k",
            "lakh", "thousand", "rupee", "₹", "inr", "hr", "software",
            "canteen", "housekeeping", "cab", "admin", "spend", "budget"
        ]

        if not any(kw in query.lower() for kw in invoice_keywords):
            return ""

        try:
            invoices = self.store.list_all()
        except Exception as e:
            logger.error(f"Store list_all error: {e}")
            return "Error reading invoice data from store."

        if not invoices:
            return "No invoices have been processed yet. The system is empty."

        # ── Build rich context ────────────────────────────────────────────────
        lines = [
            f"TOTAL INVOICES IN SYSTEM: {len(invoices)}",
            f"SUBMITTED TO ERP: {sum(1 for i in invoices if i.get('erp_status') == 'SUBMITTED')}",
            f"PENDING/REVIEW: {sum(1 for i in invoices if i.get('erp_status') in ('PENDING','REVIEW','READY','FAILED'))}",
            "",
            "ALL INVOICES (use these to answer filtering questions):",
        ]

        total_spend = 0.0
        for inv in invoices:
            d = inv.get("extracted_data", {})

            # Safe amount extraction — this was the crash point
            amount = self._to_float(d.get("amount_in_inr") or d.get("total_amount"))
            if amount is None:
                amount = 0.0
            total_spend += amount

            lines.append(
                f"  • ID={d.get('invoice_id','?')} "
                f"| Vendor={d.get('vendor_name','?')} "
                f"| Amount=₹{amount:,.2f} "
                f"| Category={inv.get('category','?')} "
                f"| Date={d.get('invoice_date','?')} "
                f"| ERP={inv.get('erp_status','?')} "
                f"| Validation={inv.get('validation',{}).get('overall_status','?')}"
            )

        lines.append("")
        lines.append(f"TOTAL SPEND ACROSS ALL INVOICES: ₹{total_spend:,.2f}")

        # Highlight audit-required
        audit = [
            inv.get("extracted_data", {}).get("invoice_id")
            for inv in invoices
            if self._to_float(
                inv.get("extracted_data", {}).get("amount_in_inr") or
                inv.get("extracted_data", {}).get("total_amount")
            ) or 0 > 100_000
        ]
        if audit:
            lines.append(f"AUDIT REQUIRED (>₹1L): {', '.join(str(x) for x in audit)}")

        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_float(val) -> float | None:
        """Safely convert any amount value to float."""
        if val is None:
            return None
        try:
            cleaned = re.sub(r"[₹$,\s]", "", str(val))
            return float(cleaned) if cleaned else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_invoice_ids(text: str) -> list:
        """Extract invoice IDs mentioned in the AI response for source tagging."""
        pattern = r'\b([A-Z]{2,4}-[\d][A-Z0-9\-]*[\dA-Z]|INV-\d+|\d{4}[-]\d{3,})\b'
        return list(set(re.findall(pattern, text)))

    @staticmethod
    def _error_response(session_id: str, error_msg: str) -> dict:
        return {
            "response": (
                "I'm having trouble connecting to the AI service right now. "
                f"Details: {error_msg}\n\n"
                "Please check that your ANTHROPIC_API_KEY is set correctly in the .env file."
            ),
            "session_id": session_id,
            "error": error_msg,
            "sources": []
        }
