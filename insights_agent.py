"""
Insights Agent
Generates finance-officer-level analytics from processed invoices.
Exports to PDF and Excel.
"""

import io
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class InsightsAgent:
    """Agent 3: Analytics, insights, and export."""

    def __init__(self, store):
        self.store = store

    def generate_insights(self) -> dict:
        """Compute dashboard metrics from all stored invoices."""
        # Dashboard only reflects invoices that have been submitted to ERP.
        # Pending, failed, or in-review invoices are not shown until submitted.
        invoices = self.store.list_all(status_filter="SUBMITTED")
        if not invoices:
            return self._empty_insights()

        total_spend = 0.0
        category_spend = defaultdict(float)
        category_count = defaultdict(int)
        monthly_spend = defaultdict(float)
        vendor_spend = defaultdict(float)
        status_counts = defaultdict(int)
        audit_invoices = []
        recent_invoices = []
        failed_validations = []

        for inv in invoices:
            data = inv.get("extracted_data", {})
            cat = inv.get("category", "Others")
            amount = self._to_float(data.get("amount_in_inr") or data.get("total_amount")) or 0
            vendor = data.get("vendor_name", "Unknown")
            inv_date = data.get("invoice_date", "")
            erp_status = inv.get("erp_status", "PENDING")
            val_status = inv.get("validation", {}).get("overall_status", "UNKNOWN")

            total_spend += amount
            category_spend[cat] += amount
            category_count[cat] += 1
            vendor_spend[vendor] += amount
            status_counts[erp_status] += 1

            # Monthly grouping
            if inv_date and len(str(inv_date)) >= 7:
                month_key = str(inv_date)[:7]
                monthly_spend[month_key] += amount

            # Flag audit items
            if amount > 100_000:
                audit_invoices.append({
                    "invoice_id": data.get("invoice_id"),
                    "vendor": vendor,
                    "amount": amount,
                    "category": cat
                })

            # Validation failures
            if val_status == "FAILED":
                failed_validations.append({
                    "invoice_id": data.get("invoice_id"),
                    "vendor": vendor,
                    "failed_checks": [
                        c["name"] for c in inv.get("validation", {}).get("checks", [])
                        if c["status"] == "FAILED"
                    ]
                })

        # Recent 10
        sorted_inv = sorted(invoices, key=lambda x: x.get("processed_at", ""), reverse=True)
        recent_invoices = [
            {
                "invoice_id": i.get("extracted_data", {}).get("invoice_id"),
                "vendor": i.get("extracted_data", {}).get("vendor_name"),
                "amount": self._to_float(
                    i.get("extracted_data", {}).get("amount_in_inr") or
                    i.get("extracted_data", {}).get("total_amount")
                ),
                "category": i.get("category"),
                "status": i.get("erp_status"),
                "date": i.get("extracted_data", {}).get("invoice_date"),
                "validation_status": i.get("validation", {}).get("overall_status")
            }
            for i in sorted_inv[:10]
        ]

        # Top vendors
        top_vendors = sorted(vendor_spend.items(), key=lambda x: x[1], reverse=True)[:8]

        # Monthly sorted
        monthly_sorted = sorted(monthly_spend.items())

        return {
            "summary": {
                "total_invoices": len(invoices),
                "total_spend_inr": round(total_spend, 2),
                "avg_invoice_value": round(total_spend / len(invoices), 2) if invoices else 0,
                "audit_required": len(audit_invoices),
                "validation_failures": len(failed_validations),
                "ready_for_erp": status_counts.get("READY", 0),
                "pending_review": status_counts.get("REVIEW", 0)
            },
            "category_breakdown": [
                {
                    "category": cat,
                    "total": round(amount, 2),
                    "count": category_count[cat],
                    "percentage": round(amount / total_spend * 100, 1) if total_spend else 0
                }
                for cat, amount in sorted(category_spend.items(), key=lambda x: x[1], reverse=True)
            ],
            "monthly_trend": [
                {"month": m, "total": round(v, 2)}
                for m, v in monthly_sorted
            ],
            "top_vendors": [
                {"vendor": v, "total": round(a, 2)}
                for v, a in top_vendors
            ],
            "status_distribution": dict(status_counts),
            "audit_required_list": audit_invoices[:10],
            "failed_validations": failed_validations[:10],
            "recent_invoices": recent_invoices,
            "generated_at": datetime.utcnow().isoformat()
        }

    def export(self, fmt: str, export_dir: Path) -> str:
        """Export insights data as PDF or Excel."""
        insights = self.generate_insights()
        invoices = self.store.list_all(status_filter="SUBMITTED")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "excel":
            return self._export_excel(insights, invoices, export_dir, ts)
        elif fmt == "pdf":
            return self._export_pdf(insights, invoices, export_dir, ts)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

    def _export_excel(self, insights: dict, invoices: list, export_dir: Path, ts: str) -> str:
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.chart import BarChart, Reference, PieChart
        except ImportError:
            raise RuntimeError("openpyxl not installed. Run: pip install openpyxl")

        wb = openpyxl.Workbook()

        # ── Summary Sheet ──────────────────────────────────────────────────────
        ws = wb.active
        ws.title = "Summary"
        header_font = Font(bold=True, color="FFFFFF", size=12)
        header_fill = PatternFill("solid", fgColor="1A3C6B")
        sub_fill = PatternFill("solid", fgColor="2E6DB4")
        thin = Side(border_style="thin", color="CCCCCC")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        ws["A1"] = "INVOICE AI PROCESSING SYSTEM — FINANCE DASHBOARD"
        ws["A1"].font = Font(bold=True, size=16, color="1A3C6B")
        ws["A2"] = f"Generated: {datetime.now().strftime('%d-%b-%Y %H:%M')}"
        ws["A2"].font = Font(italic=True, color="666666")

        ws.append([])
        ws.append(["SUMMARY METRICS"])
        ws["A4"].font = Font(bold=True, size=13)

        summary = insights["summary"]
        metrics = [
            ("Total Invoices Processed", summary["total_invoices"]),
            ("Total Spend (INR)", f"₹{summary['total_spend_inr']:,.2f}"),
            ("Average Invoice Value", f"₹{summary['avg_invoice_value']:,.2f}"),
            ("Ready for ERP", summary["ready_for_erp"]),
            ("Pending Review", summary["pending_review"]),
            ("Requires Audit (>1L)", summary["audit_required"]),
            ("Validation Failures", summary["validation_failures"]),
        ]
        for label, value in metrics:
            row = [label, value]
            ws.append(row)
            r = ws.max_row
            ws[f"A{r}"].font = Font(bold=False)
            ws[f"B{r}"].alignment = Alignment(horizontal="right")

        # ── Category Sheet ─────────────────────────────────────────────────────
        ws2 = wb.create_sheet("Category Breakdown")
        headers = ["Category", "Total Spend (INR)", "Invoice Count", "% of Total"]
        ws2.append(headers)
        for cell in ws2[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border

        for row in insights["category_breakdown"]:
            ws2.append([
                row["category"], row["total"],
                row["count"], row["percentage"]
            ])

        # Bar chart
        chart = BarChart()
        chart.title = "Spend by Category"
        chart.y_axis.title = "Amount (INR)"
        data_ref = Reference(ws2, min_col=2, min_row=1,
                             max_row=1 + len(insights["category_breakdown"]))
        cats_ref = Reference(ws2, min_col=1, min_row=2,
                             max_row=1 + len(insights["category_breakdown"]))
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.shape = 4
        ws2.add_chart(chart, "F2")

        # ── All Invoices Sheet ─────────────────────────────────────────────────
        ws3 = wb.create_sheet("All Invoices")
        inv_headers = [
            "Invoice ID", "Category", "Vendor", "Client",
            "Invoice Date", "Total (INR)", "GST", "Status",
            "Validation", "Batch ID", "Filename"
        ]
        ws3.append(inv_headers)
        for cell in ws3[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border

        for inv in invoices:
            d = inv.get("extracted_data", {})
            amount = self._to_float(d.get("amount_in_inr") or d.get("total_amount")) or 0
            tax = self._to_float(d.get("tax_amount")) or 0
            ws3.append([
                d.get("invoice_id", ""),
                inv.get("category", ""),
                d.get("vendor_name", ""),
                d.get("client_name", ""),
                d.get("invoice_date", ""),
                round(amount, 2),
                round(tax, 2),
                inv.get("erp_status", ""),
                inv.get("validation", {}).get("overall_status", ""),
                inv.get("batch_id", ""),
                inv.get("filename", "")
            ])
            # Color-code status
            r = ws3.max_row
            status = inv.get("erp_status", "")
            color = {"READY": "C6EFCE", "FAILED": "FFC7CE", "REVIEW": "FFEB9C"}.get(status, "FFFFFF")
            ws3[f"H{r}"].fill = PatternFill("solid", fgColor=color)

        # Auto-fit columns
        for ws_obj in [ws, ws2, ws3]:
            for col in ws_obj.columns:
                max_len = max((len(str(c.value)) for c in col if c.value), default=0)
                ws_obj.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 40)

        filepath = export_dir / f"InvoiceAI_Report_{ts}.xlsx"
        wb.save(str(filepath))
        logger.info(f"Excel exported: {filepath}")
        return str(filepath)

    def _export_pdf(self, insights: dict, invoices: list, export_dir: Path, ts: str) -> str:
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                            Paragraph, Spacer, HRFlowable)
            from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        except ImportError:
            raise RuntimeError("reportlab not installed. Run: pip install reportlab")

        filepath = export_dir / f"InvoiceAI_Report_{ts}.pdf"
        doc = SimpleDocTemplate(str(filepath), pagesize=A4,
                                rightMargin=1.5*cm, leftMargin=1.5*cm,
                                topMargin=1.5*cm, bottomMargin=1.5*cm)
        styles = getSampleStyleSheet()
        elements = []

        NAVY = colors.HexColor("#1A3C6B")
        BLUE = colors.HexColor("#2E6DB4")

        title_style = ParagraphStyle("Title", parent=styles["Title"],
                                     textColor=NAVY, fontSize=20, spaceAfter=6)
        sub_style = ParagraphStyle("Sub", parent=styles["Normal"],
                                   textColor=colors.grey, fontSize=9, spaceAfter=12)
        h2_style = ParagraphStyle("H2", parent=styles["Heading2"],
                                  textColor=BLUE, fontSize=13, spaceBefore=12)

        elements.append(Paragraph("Invoice AI Processing System", title_style))
        elements.append(Paragraph(f"Finance Dashboard Report — Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", sub_style))
        elements.append(HRFlowable(width="100%", thickness=2, color=NAVY))
        elements.append(Spacer(1, 0.4*cm))

        # Summary table
        elements.append(Paragraph("Executive Summary", h2_style))
        s = insights["summary"]
        summary_data = [
            ["Metric", "Value"],
            ["Total Invoices", str(s["total_invoices"])],
            ["Total Spend (INR)", f"₹{s['total_spend_inr']:,.2f}"],
            ["Average Invoice Value", f"₹{s['avg_invoice_value']:,.2f}"],
            ["Ready for ERP", str(s["ready_for_erp"])],
            ["Pending Review", str(s["pending_review"])],
            ["Requires Audit (>₹1L)", str(s["audit_required"])],
            ["Validation Failures", str(s["validation_failures"])]
        ]
        t = Table(summary_data, colWidths=[8*cm, 8*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F4FA")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.5*cm))

        # Category breakdown
        elements.append(Paragraph("Spend by Category", h2_style))
        cat_data = [["Category", "Total Spend (INR)", "Count", "% of Total"]]
        for row in insights["category_breakdown"]:
            cat_data.append([
                row["category"],
                f"₹{row['total']:,.2f}",
                str(row["count"]),
                f"{row['percentage']}%"
            ])
        ct = Table(cat_data, colWidths=[5*cm, 5*cm, 3*cm, 3.5*cm])
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF3FB")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]))
        elements.append(ct)
        elements.append(Spacer(1, 0.5*cm))

        # Audit list
        if insights.get("audit_required_list"):
            elements.append(Paragraph("⚠ Invoices Requiring Audit (>₹1,00,000)", h2_style))
            aud_data = [["Invoice ID", "Vendor", "Amount (INR)", "Category"]]
            for a in insights["audit_required_list"]:
                aud_data.append([
                    str(a.get("invoice_id", "")),
                    str(a.get("vendor", "")),
                    f"₹{a.get('amount', 0):,.2f}",
                    str(a.get("category", ""))
                ])
            at = Table(aud_data, colWidths=[4*cm, 6*cm, 4*cm, 3*cm])
            at.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#C0392B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FDEDEC"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ]))
            elements.append(at)

        doc.build(elements)
        logger.info(f"PDF exported: {filepath}")
        return str(filepath)

    @staticmethod
    def _to_float(val) -> float | None:
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").replace("₹", "").replace("$", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _empty_insights() -> dict:
        return {
            "summary": {
                "total_invoices": 0, "total_spend_inr": 0,
                "avg_invoice_value": 0, "audit_required": 0,
                "validation_failures": 0, "ready_for_erp": 0, "pending_review": 0
            },
            "category_breakdown": [],
            "monthly_trend": [],
            "top_vendors": [],
            "status_distribution": {},
            "audit_required_list": [],
            "failed_validations": [],
            "recent_invoices": [],
            "generated_at": datetime.utcnow().isoformat()
        }
