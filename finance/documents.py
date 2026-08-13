from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


NAVY = colors.HexColor("#123B5D")
BLUE = colors.HexColor("#176B9C")
PALE_BLUE = colors.HexColor("#EAF4F9")
SLATE = colors.HexColor("#475569")
LIGHT_BORDER = colors.HexColor("#CBD5E1")


class UnreliableDocumentSnapshot(ValueError):
    """Raised when an historic finance document cannot be rendered reliably."""


def _text(value):
    return escape(str(value or "").strip()).replace("\n", "<br/>")


def _money(value, currency):
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    return f"{str(currency or '').upper()} {amount:,.2f}".strip()


def _date(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


def validate_document_snapshot(funding_request, receipt=False):
    billing = funding_request.billing_snapshot or {}
    customer = funding_request.customer_snapshot or {}
    required_billing = ["legal_entity_name"]
    if not receipt:
        required_billing.extend(["bank_name", "bank_account_name", "bank_account_number"])
    missing = [name for name in required_billing if not str(billing.get(name) or "").strip()]
    if missing or not str(customer.get("name") or "").strip():
        raise UnreliableDocumentSnapshot(
            "This document cannot be rendered reliably because its historical billing or customer snapshot is incomplete."
        )


@dataclass
class FinanceDocumentBranding:
    billing: dict
    reference: str

    def __post_init__(self):
        base = getSampleStyleSheet()
        self.styles = {
            "body": ParagraphStyle("FinanceBody", parent=base["BodyText"], fontName="Helvetica", fontSize=9,
                                   leading=13, textColor=colors.HexColor("#1E293B"), spaceAfter=3),
            "small": ParagraphStyle("FinanceSmall", parent=base["BodyText"], fontName="Helvetica", fontSize=7.5,
                                    leading=10, textColor=SLATE),
            "label": ParagraphStyle("FinanceLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8,
                                    leading=11, textColor=NAVY),
            "title": ParagraphStyle("FinanceTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22,
                                    leading=26, textColor=NAVY, alignment=TA_RIGHT),
            "total": ParagraphStyle("FinanceTotal", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18,
                                    leading=22, textColor=NAVY, alignment=TA_RIGHT),
        }

    def paragraph(self, value, style="body"):
        return Paragraph(_text(value), self.styles[style])

    def logo_or_name(self):
        logo_path = Path(settings.BASE_DIR) / "assets" / "sentinel-logo.png"
        try:
            if logo_path.is_file():
                return Image(str(logo_path), width=42 * mm, height=18 * mm, kind="proportional")
        except Exception:
            pass
        return Paragraph("<b>Sentinel</b>", ParagraphStyle(
            "FinanceLogoFallback", parent=self.styles["body"], fontSize=18, leading=21, textColor=NAVY
        ))

    def issuer(self):
        lines = [self.billing.get("trading_name"), self.billing.get("legal_entity_name")]
        if self.billing.get("company_registration_number"):
            lines.append(f"Company registration: {self.billing['company_registration_number']}")
        lines.extend([
            self.billing.get("registered_address"), self.billing.get("finance_email"),
            self.billing.get("finance_phone"),
        ])
        return Paragraph("<br/>".join(_text(item) for item in lines if item), self.styles["small"])

    def header(self, title):
        table = Table(
            [[self.logo_or_name(), self.paragraph(title, "title")]],
            colWidths=[100 * mm, 70 * mm],
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ]))
        rule = Table([[""]], colWidths=[170 * mm], rowHeights=[1])
        rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, BLUE)]))
        return [table, Spacer(1, 5), self.issuer(), Spacer(1, 4), rule]

    def detail_table(self, rows):
        cells = [[self.paragraph(label, "label"), self.paragraph(value)] for label, value in rows if value not in (None, "")]
        table = Table(cells, colWidths=[52 * mm, 118 * mm], repeatRows=0, splitByRow=1)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), .35, LIGHT_BORDER), ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    def page_footer(self, canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(LIGHT_BORDER)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFillColor(SLATE)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(18 * mm, 9 * mm, f"Document reference: {self.reference}")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()


def _proforma_story(funding_request, brand, billing, customer):
    rows = [
        ("Proforma number", funding_request.request_reference),
        ("Funding-request reference", funding_request.request_reference),
        ("Issue date", _date(funding_request.created_at)),
        ("Valid until", _date(funding_request.expires_at)),
        ("Status", "Awaiting Payment"),
        ("Customer", customer.get("name")),
        ("Customer address", customer.get("address")),
        ("Currency", funding_request.currency),
    ]
    bank_rows = [
        ("Bank", billing.get("bank_name")), ("Account name", billing.get("bank_account_name")),
        ("Account number", billing.get("bank_account_number")),
        ("Bank / branch code", billing.get("bank_branch_code")),
        ("Transfer reference / narration", funding_request.request_reference),
    ]
    total = Table([[brand.paragraph("TOTAL DUE", "label"), brand.paragraph(
        _money(funding_request.requested_amount, funding_request.currency), "total"
    )]], colWidths=[65 * mm, 105 * mm])
    total.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE), ("BOX", (0, 0), (-1, -1), 1, BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story = [*brand.header("Proforma Invoice"), Spacer(1, 8), brand.detail_table(rows), Spacer(1, 10), total,
             Spacer(1, 12), brand.paragraph("Bank transfer details", "label"), Spacer(1, 3),
             brand.detail_table(bank_rows), Spacer(1, 10)]
    if billing.get("transfer_instructions"):
        story.extend([brand.paragraph("Transfer instructions", "label"), brand.paragraph(billing["transfer_instructions"]), Spacer(1, 6)])
    story.extend([
        brand.paragraph("This proforma is a payment request for advance wallet funding and is not proof of payment."),
        brand.paragraph("The wallet is credited only after Sentinel verifies and separately approves receipt of funds."),
        brand.paragraph("Do not include patient names, clinical information, or other sensitive information in the transfer narration.", "small"),
    ])
    return story


def _receipt_story(funding_request, brand, billing, customer):
    rows = [
        ("Receipt number", funding_request.receipt_reference),
        ("Receipt / credit date", _date(funding_request.approved_at)),
        ("Customer", customer.get("name")), ("Customer address", customer.get("address")),
        ("Funding-request reference", funding_request.request_reference),
        ("Bank transaction reference", funding_request.bank_transaction_reference),
        ("Payment method", "Bank transfer"), ("Value date", _date(funding_request.value_date)),
        ("Verification date", _date(funding_request.verified_at)),
        ("Approval / wallet credit date", _date(funding_request.approved_at)),
        ("Currency", funding_request.currency),
    ]
    total = Table([[brand.paragraph("AMOUNT RECEIVED", "label"), brand.paragraph(
        _money(funding_request.received_amount, funding_request.currency), "total"
    )]], colWidths=[65 * mm, 105 * mm])
    total.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE), ("BOX", (0, 0), (-1, -1), 1, BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return [
        *brand.header("Payment Receipt"), Spacer(1, 8), brand.detail_table(rows), Spacer(1, 10), total,
        Spacer(1, 12), brand.paragraph(
            f"Payment was received and {_money(funding_request.received_amount, funding_request.currency)} was credited "
            f"to the partner organisation wallet for funding request {funding_request.request_reference}."
        ),
        brand.paragraph("This receipt confirms partner wallet funding. It is not described or issued as a tax invoice.", "small"),
    ]


def build_bank_transfer_document_story(funding_request, receipt=False):
    """Build the shared ReportLab story separately so document content is unit-testable."""
    validate_document_snapshot(funding_request, receipt=receipt)
    billing = funding_request.billing_snapshot
    customer = funding_request.customer_snapshot
    reference = funding_request.receipt_reference if receipt else funding_request.request_reference
    if not reference:
        raise UnreliableDocumentSnapshot("This document does not have a stable historical reference.")
    brand = FinanceDocumentBranding(billing=billing, reference=reference)
    story = (_receipt_story if receipt else _proforma_story)(funding_request, brand, billing, customer)
    return brand, story


def render_bank_transfer_document(funding_request, receipt=False):
    brand, story = build_bank_transfer_document_story(funding_request, receipt=receipt)
    billing = funding_request.billing_snapshot
    output = BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title="Payment Receipt" if receipt else "Proforma Invoice",
        author=billing.get("legal_entity_name") or billing.get("trading_name") or "Sentinel",
    )
    doc.build(story, onFirstPage=brand.page_footer, onLaterPages=brand.page_footer)
    return output.getvalue()
