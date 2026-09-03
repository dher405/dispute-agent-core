import os
import sys
import time
import logging
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from crypto import decrypt_value

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - [REVIEWS] - %(message)s")
logger = logging.getLogger("ReviewScraper")

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

def search_review_sites():
    """Scrape complaint/review sites for statutory disruption complaints."""
    api_key = get_setting("REVIEW_SITE_API_KEY")
    if not api_key:
        logger.info("Review site API key not configured. Skipping review scraper.")
        return

    # Get list of URLs to scrape from system_settings
    urls_json = get_setting("REVIEW_SITE_URLS", "[]")
    try:
        urls = json.loads(urls_json)
    except:
        logger.warning("Failed to parse REVIEW_SITE_URLS JSON. Skipping.")
        return

    if not urls:
        logger.info("No review site URLs configured. Skipping review scraper.")
        return

    logger.info(f"Polling {len(urls)} review/complaint sites for statutory signals...")

    for url_config in urls:
        site_name = url_config.get("name", "unknown")
        site_url = url_config.get("url")
        if not site_url:
            continue

        try:
            logger.info(f"Checking {site_name}...")
            res = requests.get(site_url, timeout=15)

            if res.status_code != 200:
                logger.warning(f"{site_name} returned status {res.status_code}")
                continue

            # Basic parsing: look for complaint text in response
            html = res.text
            lines = html.split('\n')

            for line in lines[:50]:
                text = line.strip()
                if len(text) < 30:
                    continue

                # Basic check for complaint keywords
                if not any(k in text.lower() for k in ["delay", "cancel", "outage", "denied", "withheld"]):
                    continue

                # Create a pseudo-URL for dedup
                post_url = f"{site_url}#{text[:40]}"

                conn = get_db()
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
                        if cur.fetchone():
                            conn.close()
                            continue
                    conn.close()

                payload = {
                    "source_platform": "review_site",
                    "platform_user_id": site_name,
                    "username": site_name,
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
                        logger.info(f"Staged review signal ({lead.get('vertical')} | {lead.get('carrier_name')}): {site_name}")

                time.sleep(0.5)

        except Exception as e:
            logger.error(f"Error scraping {site_name}: {e}")

        time.sleep(2)

if __name__ == "__main__":
    if "--once" in sys.argv:
        search_review_sites()
        sys.exit(0)
    while True:
        search_review_sites()
        time.sleep(600)
