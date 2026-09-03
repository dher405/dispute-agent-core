import os
import sys
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [TWITTER] - %(message)s")
logger = logging.getLogger("TwitterScraper")

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

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
    except Exception as e:
        logger.debug(f"Failed to fetch setting {key}: {e}")
    return os.getenv(key, default)

def search_twitter():
    """Search Twitter/X for statutory disruption complaints."""
    bearer_token = get_setting("TWITTER_BEARER_TOKEN")
    if not bearer_token:
        logger.info("Twitter/X Bearer Token not configured. Skipping Twitter scraper.")
        return

    logger.info("Polling X/Twitter API for statutory consumer disputes...")

    search_queries = [
        "flight delayed OR cancelled",
        "airline delay -jk -meme",
        "flight cancellation",
        "ISP outage",
        "internet down",
        "security deposit withheld"
    ]

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "DisputeAgentCore/3.6"
    }

    for query in search_queries:
        try:
            url = "https://api.twitter.com/2/tweets/search/recent"
            params = {
                "query": query,
                "max_results": 10,
                "tweet.fields": "created_at,public_metrics",
                "expansions": "author_id",
                "user.fields": "username,created_at"
            }
            res = requests.get(url, headers=headers, params=params, timeout=15)

            if res.status_code == 429:
                logger.warning("Twitter API rate limit hit. Backing off.")
                time.sleep(60)
                continue

            if res.status_code != 200:
                logger.warning(f"Twitter query '{query}' failed with status {res.status_code}: {res.text}")
                continue

            data = res.json()
            tweets = data.get("data", [])
            users_lookup = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

            for tweet in tweets:
                tweet_id = tweet.get("id")
                text = tweet.get("text", "").strip()
                author_id = tweet.get("author_id")
                author = users_lookup.get(author_id, {})
                username = author.get("username", "unknown")
                post_url = f"https://twitter.com/{username}/status/{tweet_id}"

                if len(text) < 30:
                    continue

                conn = get_db()
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
                        if cur.fetchone():
                            conn.close()
                            continue
                    conn.close()

                payload = {
                    "source_platform": "twitter",
                    "platform_user_id": author_id,
                    "username": username,
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
                        logger.info(f"Staged Twitter signal ({lead.get('vertical')} | {lead.get('carrier_name')}): {post_url}")

                time.sleep(0.5)

        except Exception as e:
            logger.error(f"Error querying Twitter for '{query}': {e}")

        time.sleep(1)

if __name__ == "__main__":
    if "--once" in sys.argv:
        search_twitter()
        sys.exit(0)
    while True:
        search_twitter()
        time.sleep(300)
