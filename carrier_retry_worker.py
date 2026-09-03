import os
import sys
import time
import logging
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
from letter_generator import StatutoryDemandGenerator
from carrier_dispatcher import dispatch_demand_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def run_retry_cycle():
    if not DATABASE_URL: return
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM leads 
                WHERE status IN ('opted_in', 'dispatch_failed') 
                  AND COALESCE(dispatch_attempts, 0) < 5 
                  AND (next_dispatch_retry_at IS NULL OR next_dispatch_retry_at <= NOW())
                LIMIT 20;
            """)
            stalled = cur.fetchall()

            for lead in stalled:
                lead_id = str(lead["id"])
                attempts = (lead.get("dispatch_attempts") or 0) + 1
                pdf = StatutoryDemandGenerator.generate_pdf(lead)
                success, note = dispatch_demand_email(lead, pdf)
                if success:
                    cur.execute("UPDATE leads SET status='dispatched', dispatch_attempts=%s, last_dispatch_error=NULL, updated_at=NOW() WHERE id::text=%s;", (attempts, lead_id))
                    logger.info(f"Retry successful for {lead_id}")
                else:
                    nxt = datetime.utcnow() + timedelta(minutes=2 ** attempts)
                    st = "dispatch_failed" if attempts >= 5 else lead["status"]
                    cur.execute("UPDATE leads SET status=%s, dispatch_attempts=%s, last_dispatch_error=%s, next_dispatch_retry_at=%s, updated_at=NOW() WHERE id::text=%s;", (st, attempts, note, nxt, lead_id))

                    # Item 10: dead-letter-queue exhaustion -- once retries are exhausted the
                    # lead will never be picked up by this cycle's WHERE clause again
                    # (dispatch_attempts < 5), so raise a system_alerts row for human follow-up.
                    if attempts >= 5:
                        cur.execute("""
                            INSERT INTO system_alerts (lead_id, alert_type, message, created_at)
                            VALUES (%s, %s, %s, NOW())
                        """, (
                            lead_id,
                            "dispatch_dlq_exhausted",
                            f"Lead {lead_id} ({lead.get('carrier_name')}) failed demand-letter dispatch "
                            f"{attempts} times and has been moved to dispatch_failed. Last error: {note}. "
                            f"Manual dispatch required."
                        ))
                        logger.warning(f"Lead {lead_id} exhausted dispatch retries; alert raised for manual follow-up.")
                conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_retry_cycle()
        sys.exit(0)
    while True:
        run_retry_cycle()
        time.sleep(30)
