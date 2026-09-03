import os
import sys
import time
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from audit import log_status_change
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [REDDIT_REPLY] - %(message)s")
logger = logging.getLogger("RedditReplyListener")

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
    except Exception:
        pass
    return os.getenv(key, default)

def check_reddit_inbox():
    """Poll the bot's Reddit inbox for opt-in replies."""
    reddit_user = get_setting("REDDIT_USERNAME")
    reddit_pass = get_setting("REDDIT_PASSWORD")
    reddit_client_id = get_setting("REDDIT_CLIENT_ID")
    reddit_client_secret = get_setting("REDDIT_CLIENT_SECRET")

    if not all([reddit_user, reddit_pass, reddit_client_id, reddit_client_secret]):
        logger.info("Reddit credentials not fully configured. Skipping Reddit reply listener.")
        return

    try:
        import praw

        reddit = praw.Reddit(
            client_id=reddit_client_id,
            client_secret=reddit_client_secret,
            user_agent="DisputeAgentCore/3.6",
            username=reddit_user,
            password=reddit_pass
        )

        logger.info("Checking Reddit inbox for opt-in replies...")

        inbox = reddit.user.me().inbox.stream.submissions(pause_after=0)

        opt_in_keywords = ["yes", "i'm in", "sign me up", "go ahead", "do it", "proceed", "confirm", "approved"]

        for message in inbox:
            if message is None:
                break

            text = message.body.lower() if hasattr(message, 'body') else ""
            author = message.author.name if hasattr(message, 'author') else "unknown"

            # Check if this is an opt-in message
            is_opt_in = any(kw in text for kw in opt_in_keywords)

            if not is_opt_in:
                continue

            logger.info(f"Opt-in message detected from {author}: {text[:100]}")

            # Try to find the corresponding lead by post URL or username
            # This is a simplified matching; in production, you'd have better metadata
            try:
                conn = get_db_connection()
                if not conn:
                    continue

                with conn.cursor() as cur:
                    # Try to find lead by username and source platform
                    cur.execute("""
                        SELECT id::text, status FROM leads
                        WHERE source_platform = 'reddit' AND username = %s
                        AND status != 'opted_in'
                        ORDER BY created_at DESC LIMIT 1;
                    """, (author,))
                    lead = cur.fetchone()

                if lead and lead["status"] != "opted_in":
                    lead_id = lead["id"]
                    old_status = lead["status"]

                    # Update status to opted_in
                    log_status_change(
                        conn, lead_id, old_status, "opted_in",
                        "system:reddit_reply_listener",
                        f"Auto-opted-in from Reddit reply: {text[:100]}"
                    )

                    logger.info(f"Transitioned lead {lead_id} to opted_in from Reddit reply")

                conn.close()

            except Exception as e:
                logger.error(f"Error processing Reddit reply from {author}: {e}")

        time.sleep(1)

    except ImportError:
        logger.error("PRAW library not installed. Cannot check Reddit inbox.")
    except Exception as e:
        logger.error(f"Reddit inbox check error: {e}")

if __name__ == "__main__":
    if "--once" in sys.argv:
        check_reddit_inbox()
        sys.exit(0)
    while True:
        check_reddit_inbox()
        time.sleep(300)  # Check every 5 minutes
