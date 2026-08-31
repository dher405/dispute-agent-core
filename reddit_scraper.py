import os
import time
import random
import xml.etree.ElementTree as ET
import requests

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"
COMBINED_FEED = "unitedairlines+delta+americanairlines+travel+flights"
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection", "stranded", "diverted"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"
}

def poll_reddit_rss():
    print(f"[RSS SCANNER] Listening to combined stream r/{COMBINED_FEED}...", flush=True)
    seen_entry_ids = set()
    url = f"https://www.reddit.com/r/{COMBINED_FEED}/new.rss"

    while True:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                entries = root.findall("atom:entry", ns)
                print(f"[RSS COMBINED] Successfully fetched {len(entries)} entries.", flush=True)

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
                        clean_id = entry_id.split("/")[-1]
                        payload = {
                            "source_platform": "reddit",
                            "username": author,
                            "user_id": clean_id,
                            "post_url": link,
                            "post_text": f"Title: {title}\nExcerpt: {title}"
                        }
                        try:
                            eval_res = requests.post(API_URL, json=payload, timeout=10)
                            print(f"[RSS INGESTED] {clean_id} (@{author}): {eval_res.status_code} - {eval_res.text}", flush=True)
                        except Exception as post_err:
                            print(f"[API ERROR] Failed to send {clean_id}: {post_err}", flush=True)

            elif res.status_code == 429:
                print("[RSS COMBINED] 429 Rate Limited. Sleeping 90s...", flush=True)
                time.sleep(90)
            else:
                print(f"[RSS COMBINED] HTTP {res.status_code}", flush=True)

        except Exception as e:
            print(f"[RSS ERROR] {e}", flush=True)

        # Standard poll interval
        time.sleep(random.uniform(60, 90))

if __name__ == "__main__":
    poll_reddit_rss()
