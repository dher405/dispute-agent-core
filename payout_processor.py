import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Tuple, Optional
from decimal import Decimal
from dotenv import load_dotenv
from crypto import decrypt_value

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_setting(key: str, default: str = "") -> str:
    """Fetch setting from system_settings table."""
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

def execute_payout(lead_id: str, admin_username: str, stripe_customer_token: Optional[str] = None) -> Tuple[bool, str]:
    """
    Execute a real settlement payout via Stripe (or mock if not configured).

    THIS FUNCTION MUST ONLY BE CALLED EXPLICITLY BY A HUMAN ADMIN.
    It processes actual money transfer.

    Args:
        lead_id: The settled lead UUID
        admin_username: The admin user who authorized the payout
        stripe_customer_token: Optional Stripe token override

    Returns:
        (success: bool, message: str)
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, claimant_name, claimant_email, recovery_amount, status
                FROM leads WHERE id::text = %s;
            """, (lead_id,))
            lead = cur.fetchone()
        conn.close()

        if not lead:
            return False, f"Lead {lead_id} not found"

        if lead["status"] != "settled":
            return False, f"Lead {lead_id} is not in settled status (current: {lead['status']})"

        amount = Decimal(str(lead.get("recovery_amount") or 0))
        if amount <= 0:
            return False, f"Invalid payout amount: ${amount}"

        stripe_key = get_setting("STRIPE_SECRET_KEY")
        if not stripe_key:
            logger.warning(f"[DRY-RUN] Payout not executed: Stripe key not configured. Would pay ${amount} to {lead['claimant_email']}")
            return True, f"Dry-run: Payout of ${amount} would be sent to {lead['claimant_email']}"

        # Real Stripe payout
        try:
            import stripe
            stripe.api_key = stripe_key

            # Create a payout (requires a connected Stripe account with transfers enabled)
            payout = stripe.Payout.create(
                amount=int(amount * 100),  # Convert to cents
                currency="usd",
                description=f"Settlement payout for claim {lead_id} - {lead['claimant_name']}",
                metadata={"lead_id": lead_id, "admin": admin_username}
            )

            payout_id = payout.id
            logger.info(f"Payout {payout_id} executed for lead {lead_id}: ${amount} to {lead['claimant_email']}")

            # Record the payout in the audit log
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO system_audit_logs (service_name, event_category, log_level, message, lead_id)
                    VALUES (%s, %s, %s, %s, %s);
                """, (
                    "payout_processor",
                    "PAYOUT_EXECUTED",
                    "INFO",
                    f"Stripe payout {payout_id} executed for ${amount} by {admin_username}",
                    lead_id
                ))
            conn.commit()
            conn.close()

            return True, f"Payout {payout_id} executed successfully for ${amount}"

        except Exception as e:
            logger.error(f"Stripe payout failed for lead {lead_id}: {e}")
            return False, f"Stripe error: {str(e)}"

    except Exception as e:
        logger.error(f"Payout processor error: {e}")
        return False, f"Payout error: {str(e)}"
