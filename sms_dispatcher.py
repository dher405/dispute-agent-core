import os
import sys
import logging
from typing import Dict, Any, Optional, Tuple
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
TRACKING_PORTAL_BASE = os.getenv("TRACKING_PORTAL_BASE", "https://dispute-admin.onrender.com")


def get_db_connection():
    """Establishes thread-safe database connection to dispute_db_f372."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is required.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def format_currency(val: Any) -> str:
    """Safely formats decimal/float into currency string."""
    try:
        return f"${float(val):.2f}"
    except (ValueError, TypeError):
        return "$0.00"


def generate_sms_message(lead: Dict[str, Any], event_type: str) -> str:
    """Builds statutory, conversion-oriented SMS copy by event and vertical."""
    lead_id = lead.get("id")
    claimant = (lead.get("claimant_name") or "Claimant").split()[0]
    carrier = lead.get("carrier_name") or "the provider"
    vertical = lead.get("vertical", "flight_disruption")
    tracking_url = f"{TRACKING_PORTAL_BASE}/?claim_id={lead_id}"
    
    if event_type == "opt_in_confirmation":
        incident_ref = lead.get("incident_identifier") or lead.get("pnr") or lead.get("account_number") or "your incident"
        return (
            f"Dispute Agent: Hi {claimant}, your claim against {carrier} ({incident_ref}) is confirmed and authorized. "
            f"Our recovery engine is serving formal demand packages. Track real-time status here: {tracking_url}"
        )

    elif event_type == "settlement_alert":
        recovery = format_currency(lead.get("recovery_amount") or 0.0)
        fee = format_currency(lead.get("fee_collected") or 0.0)
        net_payout = format_currency(float(lead.get("recovery_amount") or 0.0) - float(lead.get("fee_collected") or 0.0))
        
        return (
            f"Dispute Agent: Great news {claimant}! Your {carrier} statutory claim has settled for {recovery}. "
            f"Net disbursement: {net_payout} (after 25% fee: {fee}). View settlement accounting: {tracking_url}"
        )

    elif event_type == "demand_dispatched":
        return (
            f"Dispute Agent: Formal statutory demand served to {carrier} legal desk under {lead.get('regulatory_framework', 'applicable statutes')}. "
            f"Statutory compliance window: 14 business days. Track progress: {tracking_url}"
        )

    return f"Dispute Agent: Status update on your claim {lead_id}: {tracking_url}"


def send_sms(to_phone: str, body: str) -> Tuple[bool, str]:
    """
    Dispatches SMS using Twilio REST API.
    Gracefully falls back to mock logger when credentials are not configured.
    """
    if not to_phone:
        return False, "Recipient phone number missing"

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning(f"[DRY-RUN] Twilio credentials not fully set. Simulating SMS to {to_phone}:\n>>> {body}")
        return True, "Mock SMS delivered (Twilio dry-run mode)"

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        message = client.messages.create(
            body=body,
            from_=TWILIO_PHONE_NUMBER,
            to=to_phone.strip()
        )
        logger.info(f"[SUCCESS] SMS SID {message.sid} dispatched to {to_phone}")
        return True, message.sid
    except Exception as e:
        logger.error(f"[ERROR] Twilio dispatch failed for {to_phone}: {e}")
        return False, str(e)


def notify_claim_event(lead_id: str, event_type: str) -> bool:
    """Fetches lead metadata by ID, builds contextual message, and sends alert."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS id,
                    vertical,
                    carrier_name,
                    incident_identifier,
                    account_number,
                    estimated_compensation,
                    recovery_amount,
                    fee_collected,
                    regulatory_framework,
                    claimant_name,
                    claimant_phone,
                    status
                FROM leads
                WHERE id::text = %s;
            """, (lead_id,))
            lead = cur.fetchone()

        if not lead:
            logger.error(f"Lead ID {lead_id} not found.")
            return False

        phone = lead.get("claimant_phone")
        if not phone:
            logger.info(f"Lead {lead_id} has no phone number on file. Skipping SMS dispatch.")
            return False

        body = generate_sms_message(lead, event_type)
        success, sid_or_err = send_sms(phone, body)
        return success
    finally:
        conn.close()


if __name__ == "__main__":
    logger.info("==========================================================")
    logger.info("   Starting SMS Dispatcher Local Verification Run         ")
    logger.info("==========================================================")

    # Smoke Test Sample Simulation
    sample_lead = {
        "id": "acb3eafa-db3b-40fb-a155-d4a5f9f606b6",
        "vertical": "isp_outage",
        "carrier_name": "Xfinity",
        "incident_identifier": "TKT-DEN-994812",
        "regulatory_framework": "State PUC Utility Tariffs",
        "claimant_name": "David Herron",
        "claimant_phone": "+13035550199",
        "recovery_amount": 85.50,
        "fee_collected": 21.38
    }

    print("\n--- Testing Opt-In Message ---")
    opt_in_msg = generate_sms_message(sample_lead, "opt_in_confirmation")
    print(opt_in_msg)

    print("\n--- Testing Settlement Alert Message ---")
    settle_msg = generate_sms_message(sample_lead, "settlement_alert")
    print(settle_msg)

    print("\n--- Running Dry-Run Dispatch ---")
    success, resp = send_sms(sample_lead["claimant_phone"], settle_msg)
    print(f"Dispatch Result: {success} | Info: {resp}")
