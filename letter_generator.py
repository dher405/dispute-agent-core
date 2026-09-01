import os
import io
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether
)

class StatutoryDemandGenerator:
    """
    Generates standardized, legally structured formal demand letters across
    multiple statutory verticals (Aviation, Telecom/Utilities, Tenancy, and Restitution).
    """

    @staticmethod
    def _build_header_footer(canvas, doc):
        canvas.saveState()
        # Header accent bar
        canvas.setFillColor(colors.HexColor("#1E293B"))
        canvas.rect(0, 772, 612, 20, fill=1, stroke=0)
        
        # Footer
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(54, 36, "CONFIDENTIAL & PRIVILEGED LEGAL DEMAND — PURSUANT TO STATUTORY CITATION")
        canvas.drawRightString(558, 36, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        canvas.restoreState()

    @classmethod
    def generate_pdf(cls, claim_data: Dict[str, Any], output_path: Optional[str] = None) -> bytes:
        """
        Builds and returns the PDF binary for a claim. If output_path is provided, writes to disk.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            output_path if output_path else buffer,
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54
        )

        styles = getSampleStyleSheet()
        
        # Typography Styles
        title_style = ParagraphStyle(
            'DocTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=20,
            textColor=colors.HexColor('#0F172A')
        )
        subtitle_style = ParagraphStyle(
            'DocSub',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            leading=13,
            textColor=colors.HexColor('#2563EB')
        )
        meta_label_style = ParagraphStyle(
            'MetaLabel',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#334155')
        )
        meta_val_style = ParagraphStyle(
            'MetaVal',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#0F172A')
        )
        body_style = ParagraphStyle(
            'Body',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#1E293B')
        )
        quote_style = ParagraphStyle(
            'StatutoryBlock',
            parent=styles['Normal'],
            fontName='Helvetica-Oblique',
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor('#334155')
        )

        elements = []

        # 1. Header Banner & Title
        vertical = claim_data.get("vertical", "flight_disruption")
        vertical_titles = {
            "flight_disruption": "STATUTORY NOTICE OF AIR TRAVEL DISRUPTION COMPENSATION",
            "isp_outage": "FORMAL TARIFF NON-COMPLIANCE NOTICE & SERVICE CREDIT DEMAND",
            "security_deposit": "STATUTORY SECURITY DEPOSIT RETURN DEMAND & PENALTY NOTICE",
            "class_action": "REPRESENTATION OF QUALIFIED CLAIMANT RESTITUTION DEMAND"
        }
        
        title_text = vertical_titles.get(vertical, "FORMAL STATUTORY COMPENSATION DEMAND")
        elements.append(Paragraph(title_text, title_style))
        elements.append(Paragraph(f"DEMAND ISSUED VIA DISPUTE AGENT RECOVERY ENGINE — REF ID: {claim_data.get('id', 'N/A')}", subtitle_style))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0F172A"), spaceBefore=2, spaceAfter=12))

        # 2. Metadata Grid (Respondent, Claimant, Case Attributes)
        col1_data = [
            [Paragraph("<b>TO (RESPONDENT):</b>", meta_label_style), Paragraph(str(claim_data.get("carrier_name", "Target Entity Legal Affairs Desk")), meta_val_style)],
            [Paragraph("<b>INCIDENT REF:</b>", meta_label_style), Paragraph(str(claim_data.get("incident_identifier") or claim_data.get("pnr") or claim_data.get("account_number") or "N/A"), meta_val_style)],
            [Paragraph("<b>INCIDENT DATE:</b>", meta_label_style), Paragraph(str(claim_data.get("incident_date") or datetime.utcnow().strftime('%Y-%m-%d')), meta_val_style)],
        ]
        
        col2_data = [
            [Paragraph("<b>CLAIMANT:</b>", meta_label_style), Paragraph(str(claim_data.get("claimant_name", "Authorized Client")), meta_val_style)],
            [Paragraph("<b>CONTACT EMAIL:</b>", meta_label_style), Paragraph(str(claim_data.get("claimant_email", "N/A")), meta_val_style)],
            [Paragraph("<b>STATUTORY BASIS:</b>", meta_label_style), Paragraph(str(claim_data.get("regulatory_framework", "Consumer Protection Tariff")), meta_val_style)],
        ]

        meta_table = Table(
            [
                [
                    Table(col1_data, colWidths=[90, 150]),
                    Table(col2_data, colWidths=[90, 160])
                ]
            ],
            colWidths=[245, 255]
        )
        meta_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ]))
        
        elements.append(meta_table)
        elements.append(Spacer(1, 14))

        # 3. Dynamic Statutory Demand Language by Vertical
        amount_demanded = Decimal(str(claim_data.get("estimated_compensation") or claim_data.get("recovery_amount") or 0.00))

        if vertical == "flight_disruption":
            elements.append(Paragraph(
                f"This letter serves as a formal statutory notice under <b>{claim_data.get('regulatory_framework', 'applicable aviation standards')}</b>. "
                f"The claimant, <b>{claim_data.get('claimant_name', 'Passenger')}</b>, booked travel under reservation <b>{claim_data.get('pnr', 'N/A')}</b> "
                f"on flight <b>{claim_data.get('incident_identifier', 'N/A')}</b>, which experienced a major non-excludable operational disruption.",
                body_style
            ))
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                "Under binding aviation consumer protection mandates (including US DOT 14 CFR Part 260 and/or UK/EU Regulation 261/2004), "
                "carriers are legally obligated to issue monetary compensation directly to passengers without undue friction or mandatory travel voucher substitutions.",
                body_style
            ))

        elif vertical == "isp_outage":
            elements.append(Paragraph(
                f"This letter serves as a formal demand for statutory bill credits and service reliability disruption penalties under "
                f"<b>{claim_data.get('regulatory_framework', 'State Public Utility Commission Tariffs')}</b> against <b>{claim_data.get('carrier_name', 'ISP')}</b>.",
                body_style
            ))
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                f"The subscriber (Account / Ref: <b>{claim_data.get('account_number') or claim_data.get('incident_identifier') or 'N/A'}</b>) "
                f"experienced continuous unscheduled service disruption exceeding statutory minimum thresholds (<b>{claim_data.get('outage_duration_hours', '24+')} hours</b>). "
                f"Carrier failure to automatically credit accounts in accordance with public utility tariff filings constitutes an actionable SLA breach.",
                body_style
            ))

        elif vertical == "security_deposit":
            elements.append(Paragraph(
                f"Formal statutory demand is hereby made under state tenancy laws (including statutory itemization deadlines) "
                f"for the immediate release and treble penalty recovery of security deposit funds withheld without required statutory itemization.",
                body_style
            ))

        else:
            elements.append(Paragraph(
                f"Formal statutory demand for restitution and settlement distribution under <b>{claim_data.get('regulatory_framework', 'Applicable Restitution Framework')}</b>.",
                body_style
            ))

        elements.append(Spacer(1, 10))

        # 4. AI Statutory Breakdown Box
        reasoning_text = claim_data.get("ai_reasoning") or "Disruption confirmed via public telemetry and carrier operational record."
        elements.append(Paragraph("<b>Statutory Legal Findings & Disruption Analysis:</b>", meta_label_style))
        elements.append(Spacer(1, 4))
        
        findings_table = Table([[Paragraph(reasoning_text, quote_style)]], colWidths=[500])
        findings_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#94A3B8")),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        elements.append(findings_table)
        elements.append(Spacer(1, 14))

        # 5. Liquidated Valuation / Settlement Table
        elements.append(Paragraph("<b>Itemized Statutory Settlement Demand:</b>", meta_label_style))
        elements.append(Spacer(1, 4))
        
        calc_rows = [
            [Paragraph("<b>Claim Component</b>", meta_label_style), Paragraph("<b>Statutory Reference</b>", meta_label_style), Paragraph("<b>Demand (USD)</b>", meta_label_style)],
            [Paragraph("Primary Statutory Disruption Compensation", meta_val_style), Paragraph(str(claim_data.get("regulatory_framework", "Mandated Tariff")), meta_val_style), Paragraph(f"${amount_demanded:.2f}", meta_val_style)],
            [Paragraph("<b>TOTAL LIQUIDATED AMOUNT DEMANDED</b>", meta_label_style), Paragraph("<b>STRICT LIABILITY COMPLIANCE</b>", meta_label_style), Paragraph(f"<b>${amount_demanded:.2f}</b>", meta_label_style)]
        ]
        
        calc_table = Table(calc_rows, colWidths=[200, 180, 120])
        calc_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
            ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#F8FAFC")),
            ('ALIGN', (2,0), (2,-1), 'RIGHT'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(calc_table)
        elements.append(Spacer(1, 14))

        # 6. Compliance Mandate & Digital Authorization Signature
        sig_block = [
            Paragraph("<b>COMPLIANCE WINDOW:</b> Pursuant to statutory mandates, payment or formal response must be tendered within <b>fourteen (14) business days</b>. Failure to remit will trigger formal escalation to regulatory agency enforcement desks.", body_style),
            Spacer(1, 12),
            Paragraph(f"<b>Digital E-Signature Authorization:</b> <i>/s/ {claim_data.get('digital_signature') or claim_data.get('claimant_name', 'Authorized Claimant')}</i>", meta_label_style),
            Paragraph(f"Authorized Representative: Dispute Agent Automated Recovery Platform", meta_val_style),
            Paragraph(f"Verification Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", meta_val_style)
        ]
        elements.append(KeepTogether(sig_block))

        doc.build(elements, onFirstPage=cls._build_header_footer, onLaterPages=cls._build_header_footer)
        
        if output_path:
            with open(output_path, "rb") as f:
                return f.read()
        return buffer.getvalue()


# --- CLI Smoke Test Routine ---
if __name__ == "__main__":
    print("[+] Running letter_generator.py smoke tests...")
    
    # 1. Test Airline Disruption Demand
    flight_sample = {
        "id": "c71a39f1-9452-4cf3-8e7c-882d9a918a12",
        "vertical": "flight_disruption",
        "carrier_name": "United Airlines, Inc.",
        "incident_identifier": "UA 949",
        "pnr": "K82X9Q",
        "incident_date": "2026-08-28",
        "claimant_name": "David S. Herron",
        "claimant_email": "dave@example.com",
        "estimated_compensation": 650.00,
        "regulatory_framework": "US DOT 14 CFR Part 260 / EU261",
        "ai_reasoning": "Flight UA 949 experienced a 6hr 12min arrival delay resulting from non-weather technical aircraft substitution. Falls squarely within DOT automatic full-refund and statutory compensation standards.",
        "digital_signature": "David S. Herron"
    }
    
    pdf_bytes_flight = StatutoryDemandGenerator.generate_pdf(flight_sample, output_path="sample_flight_demand.pdf")
    print(f"[✓] Generated sample_flight_demand.pdf ({len(pdf_bytes_flight)} bytes)")

    # 2. Test Regional ISP Outage Demand
    isp_sample = {
        "id": "acb3eafa-db3b-40fb-a155-d4a5f9f606b6",
        "vertical": "isp_outage",
        "carrier_name": "Comcast / Xfinity",
        "incident_identifier": "TKT-DEN-994812",
        "account_number": "8497-10-9281920",
        "outage_duration_hours": 38.5,
        "incident_date": "2026-09-01",
        "claimant_name": "David S. Herron",
        "claimant_email": "dave@example.com",
        "estimated_compensation": 85.50,
        "regulatory_framework": "State PUC Utility Tariffs & FCC Broadband Consumer Protection Rules",
        "ai_reasoning": "Sustained fiber node outage documented for 38.5 hours in Littleton/Denver metro area. Carrier failed to apply proactive daily prorated credit as required under PUC Rule 21.",
        "digital_signature": "David S. Herron"
    }

    pdf_bytes_isp = StatutoryDemandGenerator.generate_pdf(isp_sample, output_path="sample_isp_demand.pdf")
    print(f"[✓] Generated sample_isp_demand.pdf ({len(pdf_bytes_isp)} bytes)")
