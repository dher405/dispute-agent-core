import os
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Tuple, Optional, Dict, Any
from dotenv import load_dotenv
from crypto import decrypt_value

load_dotenv()
logger = logging.getLogger(__name__)

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

def verify_flight(lead_id: str, flight_number: Optional[str], incident_date: Optional[str]) -> Tuple[bool, str]:
    """
    Verify a flight delay/cancellation via a flight-status API.

    Args:
        lead_id: The lead UUID
        flight_number: The flight number (e.g., "UA123")
        incident_date: The date of the incident (YYYY-MM-DD format)

    Returns:
        (verified: bool, notes: str)
    """
    api_key = get_setting("FLIGHT_STATUS_API_KEY")
    if not api_key:
        logger.info(f"Flight status API key not configured. Skipping flight verification for lead {lead_id}.")
        return False, "Flight verification API not configured"

    if not flight_number or not incident_date:
        logger.warning(f"Missing flight_number or incident_date for lead {lead_id}")
        return False, "Missing flight details for verification"

    try:
        # Example using AviationStack API (generic pattern)
        # Real implementation would use the appropriate API for your chosen service
        url = "https://api.aviationstack.com/v1/flights"
        params = {
            "access_key": api_key,
            "flight_iata": flight_number,
            "flight_date": incident_date
        }

        res = requests.get(url, params=params, timeout=15)

        if res.status_code != 200:
            logger.warning(f"Flight verification API returned {res.status_code} for flight {flight_number}")
            return False, f"API returned status {res.status_code}"

        data = res.json()

        # Check if flight was actually delayed/cancelled
        if data.get("data"):
            flight = data["data"][0] if isinstance(data["data"], list) else data["data"]

            status = flight.get("flight_status", "").lower()
            delayed = status in ["delayed", "cancelled", "diverted"]

            if delayed:
                delay_minutes = flight.get("arrival", {}).get("delay") or 0
                notes = f"Flight {flight_number} verified as {status} (delay: {delay_minutes} min)"
                logger.info(f"Lead {lead_id}: {notes}")
                return True, notes
            else:
                return False, f"Flight {flight_number} shows status: {status}"
        else:
            return False, f"No flight data found for {flight_number} on {incident_date}"

    except Exception as e:
        logger.error(f"Flight verification error for lead {lead_id}: {e}")
        return False, f"Verification API error: {str(e)}"

def update_lead_with_flight_verification(lead_id: str, verified: bool, notes: str):
    """Update the lead with flight verification results."""
    try:
        conn = get_db_connection()
        if not conn:
            return

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE leads
                SET flight_verified = %s, flight_verification_notes = %s
                WHERE id::text = %s;
            """, (verified, notes, lead_id))
        conn.commit()
        conn.close()
        logger.info(f"Updated lead {lead_id} with flight verification: {verified}")
    except Exception as e:
        logger.error(f"Failed to update flight verification for lead {lead_id}: {e}")
