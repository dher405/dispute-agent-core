import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from db import get_db_connection

def dispatch_approved_outreach():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT l.id, l.platform_username, l.source_platform, de.outreach_copy_draft
                FROM leads l
                JOIN dispute_evaluations de ON l.id = de.lead_id
                WHERE l.status = 'approved';
            """ )
            approved_leads = cur.fetchall()

            for lead in approved_leads:
                api_host = os.getenv("API_PUBLIC_URL", "http://localhost:8000")
                claim_portal_url = f"{api_host}/claim?lead_id={lead['id']}"
                full_message = f"{lead['outreach_copy_draft']} File and claim here: {claim_portal_url}"

                print(f"[AUTO-DISPATCH] Sending to @{lead['platform_username']} on {lead['source_platform']}: {full_message}")

                cur.execute("UPDATE leads SET status = 'contacted' WHERE id = %s;", (lead['id'],))
                conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    print("Background worker loop started...")
    while True:
        try:
            dispatch_approved_outreach()
        except Exception as e:
            print(f"Worker iteration notice: {e}")
        time.sleep(60)
