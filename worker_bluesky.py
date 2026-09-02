import os
import sys
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [BSKY] - %(message)s")
logger = logging.getLogger("BlueskyIngestion")

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

QUERY_KEYWORDS = [
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

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def is_duplicate(post_url: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
            res = cur.fetchone()
        conn.close()
        return res is not None
    except Exception as e:
        logger.error(f"Duplicate check failed: {e}")
        return False

def search_bluesky():
    endpoint = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    logger.info("Polling Bluesky public feed for statutory consumer disputes...")

    for query in QUERY_KEYWORDS:
        try:
            params = {"q": query, "limit": 10, "sort": "latest"}
            res = requests.get(endpoint, params=params, timeout=10)
            
            if res.status_code != 200:
                logger.warning(f"Bluesky query '{query}' failed with status {res.status_code}")
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

                if len(text) < 30 or is_duplicate(post_url):
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
