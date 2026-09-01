import io
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
)

class StatutoryDemandGenerator:
    @staticmethod
    def _build_header_footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#1E293B"))
        canvas.rect(0, 772, 612, 20, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(54, 36, "CONFIDENTIAL & PRIVILEGED LEGAL DEMAND — PURSUANT TO STATUTORY MANDATES")
        canvas.drawRightString(558, 36, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        canvas.restoreState()

    @classmethod
    def generate_pdf(cls, claim_data: Dict[str, Any], output_path: Optional[str] = None) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            output_path if output_path else buffer,
            pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54
        )
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle('DocTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=colors.HexColor('#0F172A'))
        subtitle_style = ParagraphStyle('DocSub', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9.5, leading=12, textColor=colors.HexColor('#2563EB'))
        meta_label_style = ParagraphStyle('MetaLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'))
        meta_val_style = ParagraphStyle('MetaVal', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#0F172A'))
        body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=colors.HexColor('#1E293B'))
        quote_style = ParagraphStyle('Quote', parent=styles['Normal'], fontName='Helvetica-Oblique', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'))

        elements = []
        vertical = claim_data.get("vertical", "flight_disruption")
        titles = {
            "flight_disruption": "STATUTORY NOTICE OF AIR TRAVEL DISRUPTION LIQUIDATED COMPENSATION",
            "isp_outage": "FORMAL TARIFF NON-COMPLIANCE NOTICE & SERVICE CREDIT DEMAND",
            "security_deposit": "STATUTORY SECURITY DEPOSIT PENALTY RETURN DEMAND",
            "class_action": "REPRESENTATION OF QUALIFIED CLAIMANT RESTITUTION DEMAND"
        }
        elements.append(Paragraph(titles.get(vertical, "FORMAL STATUTORY COMPENSATION DEMAND"), title_style))
        elements.append(Paragraph(f"DEMAND ISSUED VIA DISPUTE AGENT PLATFORM — REF: {claim_data.get('id', 'N/A')}", subtitle_style))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0F172A"), spaceBefore=3, spaceAfter=10))

        col1_data = [
            [Paragraph("<b>TO (RESPONDENT):</b>", meta_label_style), Paragraph(str(claim_data.get("carrier_name", "Target Entity Legal Desk")), meta_val_style)],
            [Paragraph("<b>INCIDENT REF:</b>", meta_label_style), Paragraph(str(claim_data.get("incident_identifier") or claim_data.get("pnr") or claim_data.get("account_number") or "N/A"), meta_val_style)],
            [Paragraph("<b>INCIDENT DATE:</b>", meta_label_style), Paragraph(str(claim_data.get("incident_date") or datetime.utcnow().strftime('%Y-%m-%d')), meta_val_style)],
        ]
        col2_data = [
            [Paragraph("<b>CLAIMANT:</b>", meta_label_style), Paragraph(str(claim_data.get("claimant_name", "Authorized Client")), meta_val_style)],
            [Paragraph("<b>CONTACT EMAIL:</b>", meta_label_style), Paragraph(str(claim_data.get("claimant_email", "N/A")), meta_val_style)],
            [Paragraph("<b>LEGAL BASIS:</b>", meta_label_style), Paragraph(str(claim_data.get("regulatory_framework", "Statutory Tariff")), meta_val_style)],
        ]
        meta_table = Table([[Table(col1_data, colWidths=[85, 155]), Table(col2_data, colWidths=[85, 175])]], colWidths=[245, 255])
        meta_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 10))

        amount_demanded = Decimal(str(claim_data.get("estimated_compensation") or claim_data.get("recovery_amount") or 0.00))
        if vertical == "flight_disruption":
            elements.append(Paragraph(f"This notice constitutes formal statutory demand under <b>{claim_data.get('regulatory_framework', 'applicable aviation rules')}</b> regarding flight <b>{claim_data.get('incident_identifier', 'N/A')}</b> (PNR: {claim_data.get('pnr', 'N/A')}). Mandatory monetary restitution is required without voucher substitutions.", body_style))
        elif vertical == "isp_outage":
            elements.append(Paragraph(f"This demand is served under <b>{claim_data.get('regulatory_framework', 'State Utility Tariffs')}</b> for sustained unscheduled service outages (><b>{claim_data.get('outage_duration_hours', '24+')} hours</b>) on account <b>{claim_data.get('account_number') or 'N/A'}</b>.", body_style))
        else:
            elements.append(Paragraph(f"Demand is served pursuant to mandatory statutory obligations under <b>{claim_data.get('regulatory_framework', 'Statutory Consumer Codes')}</b>.", body_style))

        elements.append(Spacer(1, 8))
        elements.append(Paragraph("<b>Statutory Legal Findings & Disruption Analysis:</b>", meta_label_style))
        reasoning = claim_data.get("ai_reasoning") or "Disruption confirmed via operational carrier telemetry."
        f_table = Table([[Paragraph(reasoning, quote_style)]], colWidths=[500])
        f_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#94A3B8")),
            ('PADDING', (0,0), (-1,-1), 6)
        ]))
        elements.append(f_table)
        elements.append(Spacer(1, 10))

        calc_rows = [
            [Paragraph("<b>Demand Component</b>", meta_label_style), Paragraph("<b>Statutory Reference</b>", meta_label_style), Paragraph("<b>Amount (USD)</b>", meta_label_style)],
            [Paragraph("Liquidated Statutory Restitution", meta_val_style), Paragraph(str(claim_data.get("regulatory_framework", "Mandated Tariff")), meta_val_style), Paragraph(f"${amount_demanded:.2f}", meta_val_style)],
            [Paragraph("<b>TOTAL LIQUIDATED DEMAND</b>", meta_label_style), Paragraph("<b>STRICT LIABILITY COMPLIANCE</b>", meta_label_style), Paragraph(f"<b>${amount_demanded:.2f}</b>", meta_label_style)]
        ]
        calc_table = Table(calc_rows, colWidths=[200, 180, 120])
        calc_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
            ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#F8FAFC")),
            ('ALIGN', (2,0), (2,-1), 'RIGHT'),
            ('PADDING', (0,0), (-1,-1), 4)
        ]))
        elements.append(calc_table)
        elements.append(Spacer(1, 10))

        sig_block = [
            Paragraph("<b>COMPLIANCE WINDOW:</b> Formal tender or response required within <b>14 business days</b>. Failure to remediate triggers regulatory enforcement escalation.", body_style),
            Spacer(1, 8),
            Paragraph(f"<b>Digital E-Signature:</b> <i>/s/ {claim_data.get('digital_signature') or claim_data.get('claimant_name', 'Authorized Claimant')}</i>", meta_label_style),
            Paragraph("Representative: Dispute Agent Automated Recovery Platform", meta_val_style),
            Paragraph(f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", meta_val_style)
        ]
        elements.append(KeepTogether(sig_block))
        doc.build(elements, onFirstPage=cls._build_header_footer, onLaterPages=cls._build_header_footer)
        if output_path:
            with open(output_path, "rb") as f:
                return f.read()
        return buffer.getvalue()
