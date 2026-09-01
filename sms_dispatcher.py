import os
import logging
from typing import Dict, Any, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
TRACKING_BASE = os.getenv("TRACKING_PORTAL_BASE", "https://dispute-admin.onrender.com")

def notify_claim_event(lead_id: str, event_type: str) -> bool:
    if not DATABASE_URL:
        return False
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id::text = %s;", (lead_id,))
            lead = cur.fetchone()
        if not lead or not lead.get("claimant_phone"):
            return False

        phone = lead["claimant_phone"]
        name = (lead.get("claimant_name") or "Claimant").split()[0]
        carrier = lead.get("carrier_name") or "Provider"
        tracking_url = f"{TRACKING_BASE}/?claim_id={lead_id}"

        if event_type == "opt_in_confirmation":
            body = f"Dispute Agent: Hi {name}, claim against {carrier} is active and demands are served. Track live: {tracking_url}"
        elif event_type == "settlement_alert":
            rec = float(lead.get("recovery_amount") or 0.0)
            fee = float(lead.get("fee_collected") or 0.0)
            body = f"Dispute Agent: Success {name}! Claim settled for ${rec:.2f} (Net: ${rec - fee:.2f} after 25% fee). Details: {tracking_url}"
        else:
            body = f"Dispute Agent: Status update on claim {lead_id}: {tracking_url}"

        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
            logger.warning(f"[DRY-RUN SMS] To {phone}: {body}")
            return True

        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=body, from_=TWILIO_PHONE_NUMBER, to=phone.strip())
        return True
    except Exception as e:
        logger.error(f"SMS failed: {e}")
        return False
    finally:
        conn.close()
