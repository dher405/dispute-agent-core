import os
import sys
import time
import logging
import psycopg2
import json
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from dotenv import load_dotenv
from carrier_dispatcher import dispatch_demand_email
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [ESCALATION] - %(message)s")
logger = logging.getLogger("EscalationWorker")

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_setting(key: str, default: str = "") -> str:
    """Fetch setting from system_settings table."""
    try:
        conn = get_db_connection()
        if not conn:
            return os.getenv(key, default)
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
            res = cur.fetchone()
        conn.close()
        if res and res.get("value"):
            return decrypt_value(res["value"].strip())
    except Exception as e:
        logger.debug(f"Failed to fetch setting {key}: {e}")
    return os.getenv(key, default)

def send_escalation_email(lead, subject_line: str, body: str) -> bool:
    """Send an escalation email via SMTP."""
    try:
        from carrier_dispatcher import resolve_carrier_email
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = get_setting("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(get_setting("SMTP_PORT", "587"))
        smtp_user = get_setting("SMTP_USER", "")
        smtp_pass = get_setting("SMTP_PASS", "")
        from_email = get_setting("FROM_EMAIL", "claims@disputeagent.com")

        recipient = resolve_carrier_email(lead.get("carrier_name"))

        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = recipient
        msg["Subject"] = subject_line

        msg.attach(MIMEText(body, "plain"))

        if not smtp_user or not smtp_pass:
            logger.warning(f"[DRY-RUN] Escalation email would be sent to {recipient}: {subject_line}")
            return True

        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [recipient], msg.as_string())

        logger.info(f"Escalation email sent to {recipient} for lead {lead.get('id')}")
        return True

    except Exception as e:
        logger.error(f"Failed to send escalation email: {e}")
        return False

def run_escalation_cycle():
    """Check dispatched leads and apply escalation cadence."""
    conn = get_db_connection()
    if not conn:
        logger.error("Database connection failed")
        return

    try:
        with conn.cursor() as cur:
            # Find leads that have been dispatched for 7+ days
            cur.execute("""
                SELECT
                    id::text, carrier_name, claimant_name, incident_identifier,
                    estimated_compensation, escalation_stage,
                    EXTRACT(DAY FROM NOW() - updated_at) as days_since_dispatch
                FROM leads
                WHERE status = 'dispatched'
                AND (escalation_stage IS NULL OR escalation_stage != 'regulatory_21d')
                ORDER BY updated_at ASC;
            """)
            leads = cur.fetchall()

        for lead in leads:
            days = int(lead.get("days_since_dispatch", 0))
            current_stage = lead.get("escalation_stage")
            lead_id = lead.get("id")

            try:
                if days >= 21 and (not current_stage or current_stage != "regulatory_21d"):
                    # 21-day regulatory escalation: raise alert for human review
                    logger.info(f"Lead {lead_id} requires regulatory escalation (21+ days)")

                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO system_alerts (lead_id, alert_type, message, created_at)
                            VALUES (%s, %s, %s, NOW())
                        """, (
                            lead_id,
                            "regulatory_escalation",
                            f"Lead {lead_id} ({lead.get('carrier_name')}) has been dispatched for 21+ days. "
                            f"Manual regulatory escalation required."
                        ))
                        cur.execute("""
                            UPDATE leads SET escalation_stage = %s WHERE id::text = %s;
                        """, ("regulatory_21d", lead_id))
                    conn.commit()

                elif days >= 14 and (not current_stage or current_stage == "reminder_7d"):
                    # 14-day final notice
                    logger.info(f"Lead {lead_id} sending final notice (14 days)")

                    subject = f"FINAL NOTICE: Statutory Claim Demand - Ref {lead_id}"
                    body = f"""
FINAL NOTICE OF STATUTORY CLAIM

Claim Reference: {lead_id}
Claimant: {lead.get('claimant_name')}
Carrier: {lead.get('carrier_name')}
Statutory Demand Amount: ${lead.get('estimated_compensation', 0):.2f}

This is our final notice before escalation to regulatory authorities.
Response is required within 5 business days.
"""
                    send_escalation_email(lead, subject, body)

                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE leads SET escalation_stage = %s WHERE id::text = %s;
                        """, ("final_notice_14d", lead_id))
                    conn.commit()

                elif days >= 7 and (not current_stage or current_stage == ""):
                    # 7-day reminder
                    logger.info(f"Lead {lead_id} sending reminder (7 days)")

                    subject = f"REMINDER: Statutory Claim Demand - Ref {lead_id}"
                    body = f"""
REMINDER: PENDING STATUTORY CLAIM DEMAND

Claim Reference: {lead_id}
Claimant: {lead.get('claimant_name')}
Carrier: {lead.get('carrier_name')}
Statutory Demand Amount: ${lead.get('estimated_compensation', 0):.2f}

This is a reminder that the formal statutory demand was served 7 days ago.
We await your response within 14 business days of original service.
"""
                    send_escalation_email(lead, subject, body)

                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE leads SET escalation_stage = %s WHERE id::text = %s;
                        """, ("reminder_7d", lead_id))
                    conn.commit()

            except Exception as e:
                logger.error(f"Error processing escalation for lead {lead_id}: {e}")

    except Exception as e:
        logger.error(f"Escalation cycle error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_escalation_cycle()
        sys.exit(0)
    while True:
        run_escalation_cycle()
        time.sleep(3600)  # Check every hour
