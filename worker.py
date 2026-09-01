import os
import sys
import time
import logging
from typing import List, Dict, Any
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# Target Subreddits across all 4 operational verticals
TARGET_SUBREDDITS = [
    # Aviation
    "unitedairlines",
    "delta",
    "americanairlines",
    "southwestairlines",
    "travel",
    # Telecom & Utilities
    "comcast",
    "centurylink",
    "chartercable",
    "ATT",
    # Tenancy & Housing
    "Tenant",
    "LandlordLove",
    # General Consumer Grievance
    "mildlyinfuriating"
]

HIGH_INTENT_KEYWORDS = [
    "delay", "delayed", "cancelled", "cancellation", "stranded", "denied boarding",
    "outage", "down", "no internet", "fiber", "bill credit", "no service",
    "security deposit", "withheld deposit", "itemized deduction", "landlord kept",
    "refund refused", "voucher", "dot claim"
]


def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def is_post_already_ingested(post_url: str) -> bool:
    """Checks database for duplicate post URLs to prevent reprocessing."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def contains_dispute_signals(text: str) -> bool:
    """Fast pre-filter to screen out conversational noise before invoking Gemini API."""
    lower_text = text.lower()
    return any(keyword in lower_text for keyword in HIGH_INTENT_KEYWORDS)


def fetch_public_reddit_posts(subreddit: str, limit: int = 15) -> List[Dict[str, Any]]:
    """
    Fetches recent posts using public JSON endpoints with custom user agent.
    Falls back gracefully on rate limits without crashing worker loop.
    """
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    headers = {"User-Agent": "DisputeAgentCore/2.0 (Consumer Advocacy Research; macOS)"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            posts = []
            for item in data.get("data", {}).get("children", []):
                post_data = item.get("data", {})
                posts.append({
                    "source_platform": "reddit",
                    "platform_user_id": post_data.get("author_fullname") or f"u_{post_data.get('author')}",
                    "username": post_data.get("author"),
                    "post_url": f"https://reddit.com{post_data.get('permalink')}",
                    "title": post_data.get("title", ""),
                    "selftext": post_data.get("selftext", "")
                })
            return posts
        elif response.status_code == 429:
            logger.warning(f"Reddit rate limit encountered for r/{subreddit}. Backing off.")
            return []
        else:
            logger.warning(f"HTTP {response.status_code} fetching r/{subreddit}")
            return []
    except Exception as e:
        logger.error(f"Failed to fetch posts from r/{subreddit}: {e}")
        return []


def ingest_signal_via_api(payload: Dict[str, Any]) -> bool:
    """Routes candidate post payload to the FastAPI evaluation gateway."""
    try:
        endpoint = f"{API_BASE_URL}/api/v1/leads/evaluate"
        res = requests.post(endpoint, json=payload, timeout=25)
        if res.status_code == 201:
            data = res.json()
            if data.get("status") == "staged":
                lead = data.get("lead", {})
                logger.info(
                    f"[STAGED] Lead {lead.get('id')} | Vertical: {lead.get('vertical')} | "
                    f"Entity: {lead.get('carrier_name')} | Value: ${lead.get('estimated_compensation')}"
                )
                return True
            else:
                logger.debug(f"[IGNORED] Non-viable: {data.get('reason')}")
        else:
            logger.warning(f"Evaluation returned status {res.status_code}: {res.text}")
        return False
    except Exception as e:
        logger.error(f"Error submitting lead to evaluation endpoint: {e}")
        return False


def run_ingestion_cycle():
    """Executes a single scanning cycle across all configured subreddits."""
    total_evaluated = 0
    total_staged = 0

    logger.info("--- Starting Multi-Vertical Signal Ingestion Cycle ---")
    
    for sub in TARGET_SUBREDDITS:
        logger.info(f"Scanning r/{sub} for high-intent statutory disruption signals...")
        posts = fetch_public_reddit_posts(sub, limit=10)
        
        for post in posts:
            full_text = f"{post['title']}\n{post['selftext']}".strip()
            
            if not full_text or len(full_text) < 25:
                continue
                
            if is_post_already_ingested(post["post_url"]):
                continue

            if not contains_dispute_signals(full_text):
                continue

            payload = {
                "source_platform": "reddit",
                "platform_user_id": post["platform_user_id"],
                "username": post["username"],
                "post_url": post["post_url"],
                "raw_post_text": full_text
            }

            total_evaluated += 1
            if ingest_signal_via_api(payload):
                total_staged += 1

            # Polite throttle to stay within Gemini & Reddit limits
            time.sleep(1.5)

    logger.info(f"Ingestion cycle completed. Evaluated: {total_evaluated} candidate signals | Staged: {total_staged}")


if __name__ == "__main__":
    logger.info("==========================================================")
    logger.info("   Dispute Agent Autonomous Ingestion Worker Online       ")
    logger.info(f"   API Base:     {API_BASE_URL}                          ")
    logger.info(f"   Poll Cadence: Every {POLL_INTERVAL_SECONDS}s          ")
    logger.info("==========================================================")

    if not DATABASE_URL:
        logger.error("DATABASE_URL must be configured.")
        sys.exit(1)

    # If run in one-shot CLI mode:
    if "--once" in sys.argv:
        run_ingestion_cycle()
        sys.exit(0)

    # Continuous daemon polling loop:
    try:
        while True:
            run_ingestion_cycle()
            logger.info(f"Sleeping for {POLL_INTERVAL_SECONDS} seconds until next scan...")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Worker process stopped by operator.")
