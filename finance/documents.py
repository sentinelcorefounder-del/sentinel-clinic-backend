from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(value, currency):
    return f"{currency} {value:,.2f}"


def render_bank_transfer_document(funding_request, receipt=False):
    billing = funding_request.billing_snapshot or {}
    customer = funding_request.customer_snapshot or {}
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=9, leading=13)
    title = "PAYMENT RECEIPT" if receipt else "PRO FORMA INVOICE / WALLET FUNDING REQUEST"
    reference = funding_request.receipt_reference if receipt else funding_request.request_reference
    amount = funding_request.received_amount if receipt else funding_request.requested_amount
    rows = [
        ["Document reference", escape(reference or "")],
        ["Issue date", funding_request.approved_at.date().isoformat() if receipt else funding_request.created_at.date().isoformat()],
        ["Customer", escape(customer.get("name", funding_request.wallet.organization.name))],
        ["Customer address", escape(customer.get("address", "") or "—")],
        ["Purpose", "Sentinel organisation wallet funding"],
        ["Amount received" if receipt else "Amount payable", _money(amount, funding_request.currency)],
    ]
    if receipt:
        rows.extend([
            ["Payment method", "Bank transfer"],
            ["Bank transaction reference", escape(funding_request.bank_transaction_reference or "")],
            ["Value date", funding_request.value_date.isoformat() if funding_request.value_date else "—"],
            ["Wallet credited", _money(funding_request.received_amount, funding_request.currency)],
            ["Status", "Paid and verified"],
        ])
    else:
        rows.extend([
            ["Valid until", funding_request.expires_at.date().isoformat() if funding_request.expires_at else "—"],
            ["Bank", escape(billing.get("bank_name", ""))],
            ["Account name", escape(billing.get("bank_account_name", ""))],
            ["Account number", escape(billing.get("bank_account_number", ""))],
            ["Transfer reference", escape(funding_request.request_reference)],
        ])
    table = Table(rows, colWidths=[55*mm, 105*mm])
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F1F5F9")),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("LEADING", (0,0), (-1,-1), 13),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    story = [
        Paragraph(escape(billing.get("trading_name", "Sentinel")), styles["Title"]),
        Paragraph(escape(billing.get("legal_entity_name", "Afriophthalmics")), styles["Heading2"]),
        Paragraph(escape(billing.get("registered_address", "") or ""), small), Spacer(1, 8),
        Paragraph(title, styles["Heading1"]), Spacer(1, 10), table, Spacer(1, 12),
    ]
    if not receipt:
        story.append(Paragraph(
            "This is a payment request for advance wallet funding and is not evidence of payment. "
            "The wallet is credited only after Sentinel verifies and approves receipt of funds.", small
        ))
        if billing.get("transfer_instructions"):
            story.extend([Spacer(1, 7), Paragraph(escape(billing["transfer_instructions"]), small)])
    else:
        story.append(Paragraph(
            f"This receipt confirms that {_money(amount, funding_request.currency)} was received and "
            "credited to the organisation wallet. It refers to funding request "
            f"{escape(funding_request.request_reference)}.", small
        ))
    footer = " | ".join(filter(None, [billing.get("finance_email"), billing.get("finance_phone")]))
    if footer:
        story.extend([Spacer(1, 12), Paragraph(escape(footer), small)])
    doc.build(story)
    return output.getvalue()
