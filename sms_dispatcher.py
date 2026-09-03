import os
import logging
from typing import Dict, Any, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from crypto import decrypt_value

load_dotenv()
logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")
TRACKING_BASE = os.getenv("TRACKING_PORTAL_BASE", "https://dispute-admin.onrender.com")

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_setting(key: str, default: str = "") -> str:
    """Fetches setting value from system_settings DB table, fallback to env."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
            res = cur.fetchone()
        conn.close()
        if res and res.get("value"):
            return decrypt_value(res["value"].strip())
    except Exception:
        pass
    return os.getenv(key, default)

def send_sms(to_phone: str, body: str) -> Tuple[bool, str]:
    sid = get_setting("TWILIO_ACCOUNT_SID")
    token = get_setting("TWILIO_AUTH_TOKEN")
    from_num = get_setting("TWILIO_PHONE_NUMBER")

    if not sid or not token or not from_num:
        logger.warning(f"[DRY-RUN SMS] To {to_phone}: {body}")
        return True, "Mock SMS delivered (Twilio dry-run mode)"

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        msg = client.messages.create(body=body, from_=from_num, to=to_phone.strip())
        logger.info(f"[SUCCESS] SMS SID {msg.sid} dispatched to {to_phone}")
        return True, msg.sid
    except Exception as e:
        logger.error(f"[ERROR] Twilio dispatch failed for {to_phone}: {e}")
        return False, str(e)

def notify_claim_event(lead_id: str, event_type: str) -> bool:
    """
    Item 16 (mandatory human-in-the-loop gate): this NO LONGER sends SMS directly.
    It composes the message and hands it to outreach_gateway.enqueue_outreach(), which
    only writes a 'pending_approval' row to outreach_queue. A human admin must click
    "Approve & Send" in the app.py Outreach Approval Queue tab before this message is
    actually transmitted via Twilio.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id::text = %s;", (lead_id,))
            lead = cur.fetchone()
        conn.close()

        if not lead or not lead.get("claimant_phone"):
            return False

        phone = lead["claimant_phone"]
        name = (lead.get("claimant_name") or "Claimant").split()[0]
        carrier = lead.get("carrier_name") or "Provider"
        tracking_url = f"{TRACKING_BASE}/?claim_id={lead_id}"

        if event_type == "opt_in_confirmation":
            body = (
                f"Dispute Agent: Hi {name}, your claim against {carrier} is active and authorized. "
                f"We are compiling and serving your formal demand package. Track live: {tracking_url}"
            )
        elif event_type == "settlement_alert":
            rec = float(lead.get("recovery_amount") or 0.0)
            fee = float(lead.get("fee_collected") or 0.0)
            net = rec - fee
            body = (
                f"Dispute Agent: Great news {name}! Your {carrier} dispute has settled for ${rec:.2f}. "
                f"Net payout to you: ${net:.2f} (after 25% fee: ${fee:.2f}). View accounting: {tracking_url}"
            )
        else:
            body = f"Dispute Agent: Status update on claim {lead_id}: {tracking_url}"

        from outreach_gateway import enqueue_outreach
        enqueue_outreach(lead_id, "sms", phone, body)
        logger.info(f"[SMS QUEUED FOR APPROVAL] lead={lead_id} event={event_type} -- awaiting human approval, not yet sent.")
        return True
    except Exception as e:
        logger.error(f"Error queuing claim event notification: {e}")
        return False
