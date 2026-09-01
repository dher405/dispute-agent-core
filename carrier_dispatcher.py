import os
import sys
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Dict, Any, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

from letter_generator import StatutoryDemandGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "claims@disputeagent.com")

CARRIER_LEGAL_REGISTRY = {
    # Airlines
    "united airlines": "legal-escalations@united.com",
    "united": "legal-escalations@united.com",
    "delta air lines": "ticketrefunds@delta.com",
    "delta": "ticketrefunds@delta.com",
    "american airlines": "passenger.refunds@aa.com",
    "american": "passenger.refunds@aa.com",
    "southwest airlines": "southwest.claims@wnco.com",
    "southwest": "southwest.claims@wnco.com",
    "british airways": "passenger.claims@ba.com",
    # Regional ISPs & Telecoms
    "xfinity": "subpoena_legal_services@comcast.com",
    "comcast": "subpoena_legal_services@comcast.com",
    "at&t": "regulatorycomplaints@att.com",
    "spectrum": "regulatory.notice@charter.com",
    "charter": "regulatory.notice@charter.com",
    "centurylink": "escalations@lumen.com",
    "lumen": "escalations@lumen.com"
}

DEFAULT_DESK_EMAIL = "claims-notice@disputeagent.com"


def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is required.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def resolve_carrier_email(carrier_name: Optional[str]) -> str:
    if not carrier_name:
        return DEFAULT_DESK_EMAIL
    
    clean_name = carrier_name.strip().lower()
    for key, email in CARRIER_LEGAL_REGISTRY.items():
        if key in clean_name:
            return email
    return DEFAULT_DESK_EMAIL


def build_email_body(claim: Dict[str, Any]) -> str:
    vertical = claim.get("vertical", "flight_disruption")
    claimant = claim.get("claimant_name", "Authorized Client")
    incident_ref = claim.get("incident_identifier") or claim.get("pnr") or claim.get("account_number") or "N/A"
    amount = float(claim.get("estimated_compensation") or claim.get("recovery_amount") or 0.0)
    framework = claim.get("regulatory_framework", "Applicable Statutory Consumer Protections")

    return f"""ATTN: Office of the General Counsel / Regulatory Compliance Desk
RESPONDENT ENTITY: {claim.get('carrier_name', 'Carrier/Utility')}

RE: FORMAL STATUTORY NOTICE OF CLAIM & LIQUIDATED SETTLEMENT DEMAND
Claim Reference ID: {claim.get('id')}
Claimant Name: {claimant}
Incident Reference / Account: {incident_ref}
Statutory Basis: {framework}
Liquidated Demand Amount: ${amount:.2f} USD

To Legal Operations & Claims Management,

Dispute Agent Platform represents the aforementioned claimant pursuant to verified digital agency authorization.

A statutory violation and/or non-excludable operational failure has been documented under {framework}. The accompanying formal demand package, containing legal citations, disruption findings, and digital verification signatures, is attached to this transmission in PDF format.

MANDATE TIMELINE:
Pursuant to mandatory consumer protection standards, response and liquidated tender are required within fourteen (14) business days of this notice. Failure to credit or remediate this matter will result in immediate escalation to the appropriate oversight agency (US DOT Office of Aviation Consumer Protection / State Public Utilities Commission / FCC Enforcement Bureau).

Remit settlement updates or requests for supplemental accounting directly to:
Dispute Recovery Operations
Email: {FROM_EMAIL}
Portal Case Reference: https://dispute-admin.onrender.com/?claim_id={claim.get('id')}

Respectfully submitted,
Dispute Agent Automated Recovery Platform
Regulatory Dispatch Gateway
"""


def dispatch_demand_email(claim: Dict[str, Any], pdf_bytes: bytes) -> Tuple[bool, str]:
    recipient_email = resolve_carrier_email(claim.get("carrier_name"))
    subject = f"LEGAL NOTICE: Formal Statutory Demand - Ref {claim.get('id')} - [{claim.get('carrier_name')}]"

    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = recipient_email
    msg["Subject"] = subject

    body_text = build_email_body(claim)
    msg.attach(MIMEText(body_text, "plain"))

    filename = f"Statutory_Demand_{claim.get('id')}.pdf"
    pdf_attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(pdf_attachment)

    if not SMTP_USER or not SMTP_PASS:
        logger.warning(f"[DRY-RUN] SMTP credentials not set. Mock dispatch to '{recipient_email}' for Claim {claim.get('id')}.")
        return True, f"Dry-run simulation delivered to {recipient_email}"

    try:
        logger.info(f"Connecting to SMTP server {SMTP_HOST}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [recipient_email], msg.as_string())
        logger.info(f"[SUCCESS] Demand PDF dispatched to {recipient_email} for Claim {claim.get('id')}")
        return True, f"Dispatched via SMTP to {recipient_email}"
    except Exception as e:
        logger.error(f"[ERROR] Failed to send email via SMTP: {e}")
        return False, str(e)


def process_opted_in_claims() -> int:
    conn = get_db_connection()
    dispatched_count = 0

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS id,
                    vertical,
                    carrier_name,
                    incident_identifier,
                    account_number,
                    outage_duration_hours,
                    tier_speed_tier,
                    estimated_compensation,
                    recovery_amount,
                    regulatory_framework,
                    ai_reasoning,
                    status,
                    claimant_name,
                    claimant_email,
                    claimant_phone,
                    claimant_address,
                    pnr,
                    incident_date,
                    digital_signature,
                    created_at
                FROM leads
                WHERE status = 'opted_in'
                ORDER BY created_at ASC;
            """)
            opted_in_leads = cur.fetchall()

            if not opted_in_leads:
                logger.info("No claims currently in 'opted_in' queue requiring carrier dispatch.")
                return 0

            for lead in opted_in_leads:
                lead_id = lead["id"]
                pdf_bytes = StatutoryDemandGenerator.generate_pdf(lead)
                success, note = dispatch_demand_email(lead, pdf_bytes)

                if success:
                    cur.execute("""
                        UPDATE leads
                        SET status = 'dispatched',
                            updated_at = NOW()
                        WHERE id::text = %s;
                    """, (lead_id,))
                    
                    cur.execute("""
                        INSERT INTO carrier_inbound_events (
                            lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, (
                        lead_id,
                        lead.get("carrier_name") or "Unknown Carrier",
                        lead.get("vertical") or "flight_disruption",
                        "demand_dispatched",
                        0.00,
                        f"PDF demand served to legal desk. Note: {note}",
                        psycopg2.extras.Json({"dispatch_method": "smtp", "note": note})
                    ))
                    conn.commit()
                    dispatched_count += 1
    finally:
        conn.close()

    return dispatched_count


if __name__ == "__main__":
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not configured in local environment.")
        sys.exit(1)
    dispatched = process_opted_in_claims()
    logger.info(f"Carrier dispatch run complete. Total served: {dispatched}")
