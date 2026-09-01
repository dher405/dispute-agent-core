import os
import sys
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")
SUBREDDITS = ["unitedairlines", "delta", "americanairlines", "comcast", "ATT", "Tenant"]
KEYWORDS = ["delay", "cancelled", "stranded", "outage", "no internet", "bill credit", "deposit", "landlord kept"]

def is_duplicate(url: str) -> bool:
    if not DATABASE_URL: return False
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (url,))
            return cur.fetchone() is not None
    finally:
        conn.close()

def run_ingestion_cycle():
    logger.info("Scanning Reddit communities for statutory disruption signals...")
    for sub in SUBREDDITS:
        try:
            res = requests.get(f"https://www.reddit.com/r/{sub}/new.json?limit=10", headers={"User-Agent": "DisputeAgentCore/2.5"}, timeout=10)
            if res.status_code == 200:
                for item in res.json().get("data", {}).get("children", []):
                    d = item.get("data", {})
                    url = f"https://reddit.com{d.get('permalink')}"
                    text = f"{d.get('title', '')}\n{d.get('selftext', '')}".strip()
                    if len(text) > 30 and not is_duplicate(url) and any(k in text.lower() for k in KEYWORDS):
                        payload = {"source_platform": "reddit", "platform_user_id": d.get("author_fullname") or f"u_{d.get('author')}", "username": d.get("author"), "post_url": url, "raw_post_text": text}
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
        run_ingestion_cycle()
        time.sleep(60)
