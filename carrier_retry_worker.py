import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

from letter_generator import StatutoryDemandGenerator
from carrier_dispatcher import dispatch_demand_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
MAX_DISPATCH_ATTEMPTS = int(os.getenv("MAX_DISPATCH_ATTEMPTS", "5"))
RETRY_POLL_INTERVAL_SECONDS = int(os.getenv("RETRY_POLL_INTERVAL_SECONDS", "30"))

# Exponential backoff base in minutes: attempt 1 -> 2m, attempt 2 -> 4m, attempt 3 -> 8m, etc.
BACKOFF_BASE_MINUTES = 2


def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def calculate_next_retry(attempts: int) -> datetime:
    """Computes exponential backoff delay."""
    delay_minutes = BACKOFF_BASE_MINUTES ** max(1, attempts)
    return datetime.utcnow() + timedelta(minutes=min(delay_minutes, 1440))


def fetch_stalled_or_failed_dispatches() -> List[Dict[str, Any]]:
    """
    Finds claims in 'opted_in' or 'dispatch_failed' status where
    retry limit has not been exceeded and backoff window has elapsed.
    """
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
                    COALESCE(dispatch_attempts, 0) AS dispatch_attempts,
                    last_dispatch_error,
                    next_dispatch_retry_at
                FROM leads
                WHERE status IN ('opted_in', 'dispatch_failed')
                  AND COALESCE(dispatch_attempts, 0) < %s
                  AND (next_dispatch_retry_at IS NULL OR next_dispatch_retry_at <= NOW())
                ORDER BY created_at ASC
                LIMIT 25;
            """, (MAX_DISPATCH_ATTEMPTS,))
            return cur.fetchall()
    finally:
        conn.close()


def process_dispatch_retry(claim: Dict[str, Any]) -> bool:
    """Regenerates statutory PDF and attempts delivery with telemetry logging."""
    lead_id = claim["id"]
    current_attempts = claim["dispatch_attempts"] + 1
    carrier = claim.get("carrier_name") or "Unknown Carrier"
    vertical = claim.get("vertical") or "flight_disruption"

    logger.info(f"Retrying carrier email delivery for Claim {lead_id} (Attempt {current_attempts}/{MAX_DISPATCH_ATTEMPTS})...")

    conn = get_db_connection()
    try:
        # 1. Compile statutory PDF package
        pdf_bytes = StatutoryDemandGenerator.generate_pdf(claim)

        # 2. Attempt SMTP transmission
        success, note = dispatch_demand_email(claim, pdf_bytes)

        with conn.cursor() as cur:
            if success:
                # Mark as successfully dispatched
                cur.execute("""
                    UPDATE leads
                    SET status = 'dispatched',
                        dispatch_attempts = %s,
                        last_dispatch_error = NULL,
                        last_dispatch_attempt_at = NOW(),
                        updated_at = NOW()
                    WHERE id::text = %s;
                """, (current_attempts, lead_id))

                # Log audit success event
                cur.execute("""
                    INSERT INTO carrier_inbound_events (
                        lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (
                    lead_id,
                    carrier,
                    vertical,
                    "demand_dispatched_retry",
                    0.00,
                    f"Retry {current_attempts} successful: {note}",
                    psycopg2.extras.Json({"retry_attempt": current_attempts, "note": note, "success": True})
                ))
                conn.commit()
                logger.info(f"[RETRY SUCCESS] Claim {lead_id} delivered and transitioned to 'dispatched'.")
                return True
            else:
                next_retry = calculate_next_retry(current_attempts)
                new_status = "dispatch_failed" if current_attempts >= MAX_DISPATCH_ATTEMPTS else claim["status"]

                cur.execute("""
                    UPDATE leads
                    SET status = %s,
                        dispatch_attempts = %s,
                        last_dispatch_error = %s,
                        last_dispatch_attempt_at = NOW(),
                        next_dispatch_retry_at = %s,
                        updated_at = NOW()
                    WHERE id::text = %s;
                """, (new_status, current_attempts, note, next_retry, lead_id))

                # Log audit failure event
                cur.execute("""
                    INSERT INTO carrier_inbound_events (
                        lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (
                    lead_id,
                    carrier,
                    vertical,
                    "demand_dispatch_failed",
                    0.00,
                    f"Retry {current_attempts} failed: {note}",
                    psycopg2.extras.Json({"retry_attempt": current_attempts, "error": note, "next_retry": str(next_retry)})
                ))
                conn.commit()
                logger.warning(f"[RETRY FAILED] Claim {lead_id} attempt {current_attempts} failed. Next retry: {next_retry}")
                return False
    except Exception as e:
        logger.error(f"[RETRY EXCEPTION] Error processing Claim {lead_id}: {e}")
        with conn.cursor() as cur:
            next_retry = calculate_next_retry(current_attempts)
            cur.execute("""
                UPDATE leads
                SET dispatch_attempts = %s,
                    last_dispatch_error = %s,
                    last_dispatch_attempt_at = NOW(),
                    next_dispatch_retry_at = %s,
                    updated_at = NOW()
                WHERE id::text = %s;
            """, (current_attempts, str(e), next_retry, lead_id))
            conn.commit()
        return False
    finally:
        conn.close()


def run_retry_cycle():
    """Executes a single sweep of stalled/failed dispatches."""
    claims = fetch_stalled_or_failed_dispatches()
    if not claims:
        logger.info("No stalled or failed carrier dispatches awaiting retry.")
        return 0

    logger.info(f"Found {len(claims)} claim(s) requiring carrier dispatch retry.")
    successful = 0
    for claim in claims:
        if process_dispatch_retry(claim):
            successful += 1
        time.sleep(1)

    logger.info(f"Retry cycle complete: {successful}/{len(claims)} successfully delivered.")
    return successful


if __name__ == "__main__":
    logger.info("==========================================================")
    logger.info("   Starting Carrier Demand Dispatch Retry Worker          ")
    logger.info(f"   Max Attempts: {MAX_DISPATCH_ATTEMPTS} | Polling: {RETRY_POLL_INTERVAL_SECONDS}s")
    logger.info("==========================================================")

    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    if "--once" in sys.argv:
        run_retry_cycle()
        sys.exit(0)

    try:
        while True:
            run_retry_cycle()
            time.sleep(RETRY_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Retry worker stopped by operator.")
