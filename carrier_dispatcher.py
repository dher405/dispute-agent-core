import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Dict, Any, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "claims@disputeagent.com")

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

def resolve_carrier_email(carrier_name: Optional[str]) -> str:
    if not carrier_name:
        return DEFAULT_DESK_EMAIL
    clean = carrier_name.strip().lower()
    for key, email in CARRIER_LEGAL_REGISTRY.items():
        if key in clean:
            return email
    return DEFAULT_DESK_EMAIL

def dispatch_demand_email(claim: Dict[str, Any], pdf_bytes: bytes) -> Tuple[bool, str]:
    recipient = resolve_carrier_email(claim.get("carrier_name"))
    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = recipient
    msg["Subject"] = f"LEGAL NOTICE: Formal Statutory Demand - Ref {claim.get('id')} - [{claim.get('carrier_name')}]"

    amount = float(claim.get("estimated_compensation") or claim.get("recovery_amount") or 0.0)
    body = f"""ATTN: Regulatory Compliance & Claims Legal Desk
RESPONDENT: {claim.get('carrier_name', 'Provider')}
CLAIM REF: {claim.get('id')}
CLAIMANT: {claim.get('claimant_name', 'Passenger/Subscriber')}
INCIDENT/ACCT: {claim.get('incident_identifier') or claim.get('account_number') or 'N/A'}
STATUTORY BASIS: {claim.get('regulatory_framework', 'Consumer Protection Mandate')}
DEMAND AMOUNT: ${amount:.2f} USD

Attached is the formal verified statutory demand package. Tender or response is required within 14 business days.
Portal Reference: https://dispute-admin.onrender.com/?claim_id={claim.get('id')}
"""
    msg.attach(MIMEText(body, "plain"))
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=f"Statutory_Demand_{claim.get('id')}.pdf")
    msg.attach(attachment)

    if not SMTP_USER or not SMTP_PASS:
        logger.warning(f"[DRY-RUN] Mock dispatch delivered to {recipient} for Claim {claim.get('id')}")
        return True, f"Dry-run simulation delivered to {recipient}"

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [recipient], msg.as_string())
        return True, f"Dispatched via SMTP to {recipient}"
    except Exception as e:
        return False, str(e)
