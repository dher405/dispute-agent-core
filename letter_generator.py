import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def generate_demand_pdf(claim_data: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=54,
        leftMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=12
    )
    body_style = ParagraphStyle(
        "DocBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceAfter=8
    )
    bold_body = ParagraphStyle(
        "DocBodyBold",
        parent=body_style,
        fontName="Helvetica-Bold"
    )

    story = []

    story.append(Paragraph("FORMAL NOTICE OF STATUTORY CLAIM & REFUND DEMAND", title_style))
    story.append(Paragraph(f"Date: {datetime.utcnow().strftime('%B %d, %Y')}", body_style))
    story.append(Paragraph(f"Statutory Basis: {claim_data.get('governing_statute', 'DOT 14 CFR Part 260 / EU Reg 261/2004')}", bold_body))
    story.append(Spacer(1, 12))

    table_data = [
        [Paragraph("Claimant Name:", bold_body), Paragraph(claim_data.get("full_name") or "Authorized Passenger", body_style)],
        [Paragraph("Claimant Email:", bold_body), Paragraph(claim_data.get("email") or "On File", body_style)],
        [Paragraph("Incident / Flight Identifier:", bold_body), Paragraph(claim_data.get("incident_identifier") or "N/A", body_style)],
        [Paragraph("Claim Reference ID:", bold_body), Paragraph(str(claim_data.get("lead_id")), body_style)],
        [Paragraph("Demanded Statutory Compensation:", bold_body), Paragraph(f"${float(claim_data.get('estimated_recovery_amount') or 0.0):,.2f} USD", bold_body)]
    ]

    summary_table = Table(table_data, colWidths=[200, 304])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("Statement of Fact and Violation:", bold_body))
    story.append(Paragraph(claim_data.get("ai_reasoning") or "The flight experienced significant delay/cancellation exceeding statutory thresholds.", body_style))
    story.append(Spacer(1, 10))

    legal_text = (
        "Pursuant to applicable regulatory frameworks governing scheduled air transportation services, "
        "passengers are entitled to prompt cash reimbursement for significant schedule changes, cancellations, "
        "or qualifying delays. Demand is hereby formally tendered for direct remittance of the demanded amount."
    )
    story.append(Paragraph(legal_text, body_style))
    story.append(Spacer(1, 14))

    auth_text = (
        f"This claim is executed on behalf of the claimant pursuant to digital authorization and power of attorney "
        f"recorded on {claim_data.get('discovered_at', 'system record')}."
    )
    story.append(Paragraph(auth_text, body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
