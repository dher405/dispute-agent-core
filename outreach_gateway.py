"""
Single choke point for all outbound SMS / Reddit-DM / Bluesky-DM outreach.

Item 16 (explicitly required by the business owner): no automated system in this
codebase is allowed to send an SMS or DM directly. Every send is a two-step process:
  1. enqueue_outreach(...)          -- composes the message, writes it to outreach_queue
                                        with status='pending_approval'. Does NOT send.
  2. dispatch_approved_outreach(...) -- called ONLY when a human admin clicks
                                        "Approve & Send" in app.py's Outreach Approval
                                        Queue tab. This is the only function in the
                                        entire codebase that actually calls Twilio or
                                        posts a Reddit reply.
"""
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from crypto import decrypt_value
from audit import log_status_change

load_dotenv()
logger = logging.getLogger("OutreachGateway")
DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def get_setting(key: str, default: str = "") -> str:
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


def enqueue_outreach(lead_id: str, channel: str, recipient: str, message_body: str, conn=None) -> str:
    """Queue a message for human approval. NEVER sends. Returns the outreach_queue row id."""
    own_conn = conn is None
    if conn is None:
        conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO outreach_queue (lead_id, channel, recipient, message_body, status)
                VALUES (%s, %s, %s, %s, 'pending_approval')
                RETURNING id::text;
            """, (lead_id, channel, recipient, message_body))
            queue_id = cur.fetchone()["id"]
        conn.commit()
        logger.info(f"[QUEUED - AWAITING HUMAN APPROVAL] lead={lead_id} channel={channel} recipient={recipient} queue_id={queue_id}")
        return queue_id
    finally:
        if own_conn:
            conn.close()


def _send_sms(recipient: str, message_body: str):
    import sms_dispatcher
    return sms_dispatcher.send_sms(recipient, message_body)


def _send_reddit_reply(lead: dict, message_body: str):
    reddit_client_id = get_setting("REDDIT_CLIENT_ID")
    reddit_client_secret = get_setting("REDDIT_CLIENT_SECRET")
    reddit_username = get_setting("REDDIT_USERNAME")
    reddit_password = get_setting("REDDIT_PASSWORD")
    post_url = (lead or {}).get("post_url")

    if not (reddit_client_id and reddit_client_secret and reddit_username and reddit_password and post_url):
        return True, "Dispatched in dry-run simulation mode (Reddit credentials not configured)."

    try:
        import praw
        reddit = praw.Reddit(
            client_id=reddit_client_id,
            client_secret=reddit_client_secret,
            username=reddit_username,
            password=reddit_password,
            user_agent=get_setting("REDDIT_USER_AGENT", "EasyClaimAdvocate/3.6"),
        )
        submission = reddit.submission(url=post_url)
        comment = submission.reply(message_body)
        return True, f"Public comment posted: https://reddit.com{comment.permalink}"
    except Exception as e:
        logger.error(f"Reddit reply send failed: {e}")
        return False, f"Reddit API error: {e}"


def _send_bluesky_dm(recipient: str, message_body: str):
    # Bluesky discovery currently uses the public, unauthenticated search endpoint only
    # (see worker_bluesky.py) -- there is no authenticated AT Protocol session in this
    # codebase yet to actually post a DM/mention. This stays simulated until Dave sets up
    # a Bluesky app-password and we wire up an authenticated atproto Client here.
    return True, f"Dispatched via AT Protocol mention to {recipient} (simulation mode -- no Bluesky auth configured)."


def dispatch_approved_outreach(queue_id: str, approved_by: str):
    """The ONLY function in this codebase that actually sends an SMS or posts a DM/reply.
    Must only ever be invoked from a human-initiated 'Approve & Send' click."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM outreach_queue WHERE id::text = %s;", (queue_id,))
            row = cur.fetchone()

        if not row:
            return False, "Outreach queue entry not found."
        if row["status"] != "pending_approval":
            return False, f"This message is already '{row['status']}' -- refusing to send again."

        lead_id = str(row["lead_id"]) if row["lead_id"] else None
        lead = {}
        if lead_id:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leads WHERE id::text = %s;", (lead_id,))
                lead = cur.fetchone() or {}

        channel = row["channel"]
        if channel == "sms":
            ok, note = _send_sms(row["recipient"], row["message_body"])
        elif channel in ("reddit_dm", "reddit_reply"):
            ok, note = _send_reddit_reply(lead, row["message_body"])
        elif channel == "bluesky_dm":
            ok, note = _send_bluesky_dm(row["recipient"], row["message_body"])
        else:
            ok, note = False, f"Unknown outreach channel '{channel}'."

        new_status = "sent" if ok else "send_failed"
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE outreach_queue
                SET status = %s, approved_by = %s, approved_at = NOW(),
                    sent_at = CASE WHEN %s THEN NOW() ELSE sent_at END
                WHERE id::text = %s;
            """, (new_status, approved_by, ok, queue_id))
        conn.commit()

        if ok and lead_id:
            try:
                log_status_change(
                    conn, lead_id, lead.get("status"), "contacted", approved_by,
                    note=f"Outreach approved and sent via {channel}: {note}",
                )
            except Exception as e:
                logger.error(f"Failed to advance lead status after outreach send: {e}")

        return ok, note
    finally:
        conn.close()


def reject_outreach(queue_id: str, rejected_by: str, reason: str = None):
    """Human clicked 'Reject'. No send happens. The lead is reverted to 'approved' so it
    can be edited or re-queued rather than getting stuck in limbo."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE outreach_queue SET status = 'rejected', approved_by = %s, approved_at = NOW()
                WHERE id::text = %s AND status = 'pending_approval'
                RETURNING lead_id::text, (SELECT status FROM leads WHERE id::text = outreach_queue.lead_id::text) AS lead_status;
            """, (rejected_by, queue_id))
            row = cur.fetchone()
        conn.commit()

        if row and row.get("lead_id"):
            try:
                log_status_change(
                    conn, row["lead_id"], row.get("lead_status"), "approved", rejected_by,
                    note=f"Outreach rejected: {reason or 'no reason given'}",
                )
            except Exception as e:
                logger.error(f"Failed to revert lead status after outreach rejection: {e}")
        return True
    finally:
        conn.close()
