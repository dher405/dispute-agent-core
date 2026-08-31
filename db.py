import os
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "dispute_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "postgres"),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432")
    )

def fetch_staged_leads(status_filter: str = "staged_for_review") -> pd.DataFrame:
    conn = get_db_connection()
    query = """
        SELECT * FROM v_staged_leads_for_review
        WHERE status = %s
        ORDER BY discovered_at DESC;
    """
    df = pd.read_sql(query, conn, params=(status_filter,))
    conn.close()
    return df

def update_lead_review_status(lead_id: str, new_status: str, edited_copy: str = None, edited_amount: float = None):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE leads SET status = %s WHERE id = %s;", (new_status, lead_id))
        if edited_copy is not None or edited_amount is not None:
            cur.execute("""
                UPDATE dispute_evaluations
                SET outreach_copy_draft = COALESCE(%s, outreach_copy_draft),
                    estimated_recovery_amount = COALESCE(%s, estimated_recovery_amount)
                WHERE lead_id = %s;
            """, (edited_copy, edited_amount, lead_id))
        conn.commit()
    conn.close()
