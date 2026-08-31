import os
import time
import random
import xml.etree.ElementTree as ET
import requests

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"
TARGET_SUBREDDITS = ["unitedairlines", "delta", "americanairlines", "travel", "flights"]
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection", "stranded"]

HEADERS = {
    "User-Agent": "DisputeClaimBot/1.0 (Air Passenger Rights Evaluator; contact: ops@disputeagent.local)"
}

def poll_reddit_rss():
    print("[RSS SCANNER] Starting Reddit RSS feed listener...", flush=True)
    seen_entry_ids = set()

    while True:
        for sub in TARGET_SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/new.rss"
            try:
                res = requests.get(url, headers=HEADERS, timeout=12)
                if res.status_code == 200:
                    root = ET.fromstring(res.content)
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    entries = root.findall("atom:entry", ns)
                    print(f"[RSS r/{sub}] Successfully fetched {len(entries)} entries.", flush=True)

                    for entry in entries:
                        entry_id_elem = entry.find("atom:id", ns)
                        entry_id = entry_id_elem.text if entry_id_elem is not None else ""
                        if not entry_id or entry_id in seen_entry_ids:
                            continue
                        seen_entry_ids.add(entry_id)

                        title_elem = entry.find("atom:title", ns)
                        title = title_elem.text if title_elem is not None else ""

                        content_elem = entry.find("atom:content", ns)
                        content_html = content_elem.text if content_elem is not None else ""

                        link_elem = entry.find("atom:link", ns)
                        link = link_elem.attrib.get("href", "") if link_elem is not None else ""

                        author_elem = entry.find("atom:author/atom:name", ns)
                        author = author_elem.text.replace("/u/", "") if author_elem is not None else "unknown"

                        full_text = f"{title} {content_html}".lower()

                        if any(k in full_text for k in KEYWORDS):
                            payload = {
                                "source_platform": "reddit",
                                "username": author,
                                "user_id": entry_id.split("/")[-1],
                                "post_url": link,
                                "post_text": f"Title: {title}\nExcerpt: {title}"
                            }
                            try:
                                eval_res = requests.post(API_URL, json=payload, timeout=10)
                                print(f"[RSS INGESTED] {payload['user_id']} (@{author}): {eval_res.status_code} - {eval_res.text}", flush=True)
                            except Exception as post_err:
                                print(f"[API ERROR] Failed to send {payload['user_id']}: {post_err}", flush=True)

                else:
                    print(f"[RSS r/{sub}] HTTP {res.status_code}", flush=True)

            except Exception as e:
                print(f"[RSS r/{sub} ERROR] {e}", flush=True)

            time.sleep(3)

        time.sleep(random.uniform(45, 60))

if __name__ == "__main__":
    poll_reddit_rss()
