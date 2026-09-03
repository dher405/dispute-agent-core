import os
import sys
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from dedup import compute_dedup_key
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stranded", "outage", "no internet", "bill credit", "deposit", "landlord kept"]

# Item 1: negative signals -- posts that already resolved, jokes/memes, or are otherwise
# clearly not a live viable claim. Filtered out BEFORE the Gemini viability call to cut
# noise and API cost.
NEGATIVE_SIGNALS = [
    "jk", "just kidding", "lol", "meme", "joke",
    "already resolved", "fixed it", "not a problem", "scam alert", "fake",
    "got my refund", "they refunded me", "resolved now",
]

def get_setting(key: str, default: str = "") -> str:
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
            res = cur.fetchone()
        conn.close()
        if res and res.get("value"):
            return decrypt_value(res["value"].strip())
    except Exception:
        pass
    return os.getenv(key, default)

def has_negative_signals(text: str) -> bool:
    """Item 1: filter out jokes/memes/already-resolved posts before ever calling Gemini."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in NEGATIVE_SIGNALS)

def is_duplicate(url: str, username: str = None) -> bool:
    """Item 3: dedup by post URL (cheap pre-check) or by normalized username/dedup_key
    (cross-platform duplicate-person detection). This is a fast pre-filter to avoid
    wasting a Gemini call; the /api/v1/leads/evaluate endpoint in main.py does the
    authoritative dedup check before actually inserting a row."""
    if not DATABASE_URL: return False
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (url,))
            if cur.fetchone():
                return True
            if username:
                dedup_key = compute_dedup_key(username=username)
                if dedup_key:
                    cur.execute("SELECT 1 FROM leads WHERE dedup_key = %s AND source_platform = 'reddit' LIMIT 1;", (dedup_key,))
                    if cur.fetchone():
                        return True
            return False
    finally:
        conn.close()

def run_ingestion_cycle():
    # "mildlyinfuriating" removed: too noisy/off-topic, generated far more irrelevant
    # posts than real airline/ISP/landlord disputes. Still fully DB-configurable per item 5.
    raw_subreddits = get_setting("MONITORED_SUBREDDITS", "unitedairlines,delta,americanairlines,southwestairlines,comcast,ATT,Tenant")
    subreddits = [s.strip() for s in raw_subreddits.split(",") if s.strip()]

    logger.info(f"Scanning {len(subreddits)} subreddits for statutory disruption signals...")
    for sub in subreddits:
        try:
            res = requests.get(f"https://www.reddit.com/r/{sub}/new.json?limit=10", headers={"User-Agent": "DisputeAgentCore/2.5"}, timeout=10)
            if res.status_code == 200:
                for item in res.json().get("data", {}).get("children", []):
                    d = item.get("data", {})
                    url = f"https://reddit.com{d.get('permalink')}"
                    author = d.get("author")
                    text = f"{d.get('title', '')}\n{d.get('selftext', '')}".strip()

                    if len(text) <= 30 or not any(k in text.lower() for k in KEYWORDS):
                        continue
                    if has_negative_signals(text):
                        logger.info(f"[FILTERED] Negative signal, skipping: {d.get('title', '')[:60]}")
                        continue
                    if is_duplicate(url, author):
                        logger.info(f"[DEDUP] Duplicate/known author, skipping: {url}")
                        continue

                    payload = {
                        "source_platform": "reddit",
                        "platform_user_id": d.get("author_fullname") or f"u_{author}",
                        "username": author,
                        "post_url": url,
                        "raw_post_text": text
                    }
                    eval_res = requests.post(f"{API_BASE_URL}/api/v1/leads/evaluate", json=payload, timeout=20)
                    if eval_res.status_code == 201 and eval_res.json().get("status") == "staged":
                        logger.info(f"Staged signal from r/{sub}: {url}")
                    time.sleep(1)
        except Exception as e:
            logger.error(f"Error scraping r/{sub}: {e}")

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_ingestion_cycle()
        sys.exit(0)
    while True:
        poll_sec = int(get_setting("POLL_INTERVAL_SECONDS", "60"))
        run_ingestion_cycle()
        time.sleep(poll_sec)
