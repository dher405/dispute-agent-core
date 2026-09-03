import logging
import psycopg2
from typing import Optional

logger = logging.getLogger(__name__)

def log_status_change(conn, lead_id: str, old_status: str, new_status: str, changed_by: str, note: Optional[str] = None):
    """
    Log a status change to the status_audit_log and update the lead's status atomically.

    Args:
        conn: Database connection (should not be auto-commit for transaction safety)
        lead_id: The lead/claim UUID
        old_status: Previous status
        new_status: New status
        changed_by: Username or system identifier who made the change
        note: Optional additional notes
    """
    try:
        with conn.cursor() as cur:
            # Insert audit log
            cur.execute("""
                INSERT INTO status_audit_log (lead_id, old_status, new_status, changed_by, note)
                VALUES (%s, %s, %s, %s, %s);
            """, (lead_id, old_status, new_status, changed_by, note))

            # Update the lead's status and last_status_change_at
            cur.execute("""
                UPDATE leads
                SET status = %s, last_status_change_at = NOW()
                WHERE id::text = %s;
            """, (new_status, lead_id))

        conn.commit()
        logger.info(f"Status change logged for lead {lead_id}: {old_status} -> {new_status} (by {changed_by})")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to log status change for lead {lead_id}: {e}")
        raise
