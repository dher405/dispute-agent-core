import os
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
logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

CARRIER_LEGAL_REGISTRY = {
    "united": "legal-escalations@united.com",
    "delta": "ticketrefunds@delta.com",
    "american": "passenger.refunds@aa.com",
    "southwest": "southwest.claims@wnco.com",
    "british": "passenger.claims@ba.com",
    "xfinity": "subpoena_legal_services@comcast.com",
    "comcast": "subpoena_legal_services@comcast.com",
    "at&t": "regulatorycomplaints@att.com",
    "spectrum": "regulatory.notice@charter.com",
    "charter": "regulatory.notice@charter.com",
    "centurylink": "escalations@lumen.com",
    "lumen": "escalations@lumen.com"
}
DEFAULT_DESK_EMAIL = "claims-notice@disputeagent.com"

def get_setting(key: str, default: str = "") -> str:
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
            res = cur.fetchone()
        conn.close()
        if res and res.get("value"):
            return res["value"].strip()
    except Exception:
        pass
    return os.getenv(key, default)

def resolve_carrier_email(carrier_name: Optional[str]) -> str:
    if not carrier_name:
        return DEFAULT_DESK_EMAIL
    clean = carrier_name.strip().lower()
    for key, email in CARRIER_LEGAL_REGISTRY.items():
        if key in clean:
            return email
    return DEFAULT_DESK_EMAIL

def dispatch_demand_email(claim: Dict[str, Any], pdf_bytes: bytes) -> Tuple[bool, str]:
    smtp_host = get_setting("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(get_setting("SMTP_PORT", "587"))
    smtp_user = get_setting("SMTP_USER", "")
    smtp_pass = get_setting("SMTP_PASS", "")
    from_email = get_setting("FROM_EMAIL", "claims@disputeagent.com")

    recipient = resolve_carrier_email(claim.get("carrier_name"))
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = recipient
    msg["Subject"] = f"LEGAL NOTICE: Formal Statutory Demand - Ref {claim.get('id')} - [{claim.get('carrier_name')}]"

    amount = float(claim.get("estimated_compensation") or claim.get("recovery_amount") or 0.0)
    body = f"""ATTN: Office of the General Counsel / Regulatory Compliance Desk
RESPONDENT ENTITY: {claim.get('carrier_name', 'Carrier/Utility')}

RE: FORMAL STATUTORY NOTICE OF CLAIM & LIQUIDATED SETTLEMENT DEMAND
Claim Reference ID: {claim.get('id')}
Claimant Name: {claim.get('claimant_name', 'Authorized Client')}
Incident Reference / Account: {claim.get('incident_identifier') or claim.get('account_number') or 'N/A'}
Statutory Basis: {claim.get('regulatory_framework', 'Consumer Protection Mandates')}
Liquidated Demand Amount: ${amount:.2f} USD

To Legal Operations & Claims Management,

Dispute Agent Platform represents the aforementioned claimant pursuant to verified digital agency authorization.
The formal demand package containing disruption findings, statutory citations, and verification signatures is attached in PDF format.

MANDATE TIMELINE: Response or tender required within 14 business days.
Portal Case Reference: https://dispute-admin.onrender.com/?claim_id={claim.get('id')}
"""
    msg.attach(MIMEText(body, "plain"))
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=f"Statutory_Demand_{claim.get('id')}.pdf")
    msg.attach(attachment)

    if not smtp_user or not smtp_pass:
        logger.warning(f"[DRY-RUN] SMTP credentials not set. Simulating dispatch to {recipient} for Claim {claim.get('id')}")
        return True, f"Dry-run simulation delivered to {recipient}"

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [recipient], msg.as_string())
        return True, f"Dispatched via SMTP to {recipient}"
    except Exception as e:
        logger.error(f"[ERROR] SMTP error: {e}")
        return False, str(e)
