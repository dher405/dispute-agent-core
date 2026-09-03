import os
import sys
import time
import logging
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from dedup import compute_dedup_key
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [BSKY] - %(message)s")
logger = logging.getLogger("BlueskyIngestion")

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

DEFAULT_QUERY_KEYWORDS = [
    "flight cancelled",
    "flight delayed",
    "united airlines delay",
    "delta delay",
    "american airlines cancelled",
    "xfinity outage",
    "comcast internet down",
    "landlord security deposit",
    "deposit withheld"
]

# Negative signals
NEGATIVE_SIGNALS = [
    "jk", "just kidding", "lol", "meme", "joke",
    "already resolved", "fixed it", "not a problem", "scam alert", "fake"
]

def get_db():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_setting(key: str, default: str = "") -> str:
    """Fetch setting from system_settings table."""
    try:
        conn = get_db()
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

def is_duplicate(post_url: str, handle: str = None) -> bool:
    """Check for duplicate by URL or dedup key."""
    if not DATABASE_URL:
        return False
    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Check by URL
            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
            if cur.fetchone():
                return True

            # Check by dedup key (handle-based)
            if handle:
                dedup_key = compute_dedup_key(username=handle)
                if dedup_key:
                    cur.execute("SELECT 1 FROM leads WHERE dedup_key = %s AND source_platform = 'bluesky' LIMIT 1;", (dedup_key,))
                    if cur.fetchone():
                        return True
        conn.close()
        return False
    except Exception as e:
        logger.error(f"Duplicate check failed: {e}")
        return False

def has_negative_signals(text: str) -> bool:
    """Check if text contains negative signals."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in NEGATIVE_SIGNALS)

def search_bluesky():
    endpoint = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    logger.info("Polling Bluesky public feed for statutory consumer disputes...")

    # Get configurable queries from database or use defaults
    queries_json = get_setting("BLUESKY_QUERIES")
    try:
        queries = json.loads(queries_json) if queries_json else DEFAULT_QUERY_KEYWORDS
    except:
        queries = DEFAULT_QUERY_KEYWORDS

    for query in queries:
        try:
            params = {"q": query, "limit": 10, "sort": "latest"}
            res = requests.get(endpoint, params=params, timeout=10)

            if res.status_code == 403 or res.status_code == 429:
                logger.warning(f"Bluesky rate limited or forbidden. Backing off and retrying with exponential backoff.")
                time.sleep(min(300, 10 * (len(queries) - queries.index(query))))
                continue

            if res.status_code != 200:
                logger.warning(f"Bluesky query '{query}' failed with status {res.status_code}: {res.text}")
                continue

            posts = res.json().get("posts", [])
            for p in posts:
                author = p.get("author", {})
                record = p.get("record", {})

                did = author.get("did")
                handle = author.get("handle")
                rkey = p.get("uri", "").split("/")[-1]
                post_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
                text = record.get("text", "").strip()

                # Pre-filter by length
                if len(text) < 30:
                    continue

                # Check for negative signals (jokes, resolved, etc)
                if has_negative_signals(text):
                    logger.debug(f"Skipping post with negative signals: {text[:50]}")
                    continue

                # Check for duplicates
                if is_duplicate(post_url, handle):
                    logger.debug(f"Duplicate detected for user {handle}")
                    continue

                payload = {
                    "source_platform": "bluesky",
                    "platform_user_id": did,
                    "username": handle,
                    "post_url": post_url,
                    "raw_post_text": text
                }

                eval_res = requests.post(
                    f"{API_BASE_URL}/api/v1/leads/evaluate",
                    json=payload,
                    timeout=20
                )

                if eval_res.status_code == 201:
                    data = eval_res.json()
                    if data.get("status") == "staged":
                        lead = data.get("lead", {})
                        logger.info(f"Staged Bluesky signal ({lead.get('vertical')} | {lead.get('carrier_name')}): {post_url}")
                
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"Error querying Bluesky for '{query}': {e}")
        
        time.sleep(1)

if __name__ == "__main__":
    if "--once" in sys.argv:
        search_bluesky()
        sys.exit(0)
    while True:
        search_bluesky()
        time.sleep(60)
