import os
import time
import random
import requests

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"
TARGET_SUBREDDITS = ["unitedairlines", "delta", "americanairlines", "travel", "flights"]
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection", "stranded"]

# API-compliant User-Agent bypasses CDN browser-cookie challenges
HEADERS = {
    "User-Agent": "python:dispute-agent-scanner:v1.1 (contact: admin@disputeagent.local)",
    "Accept": "application/json"
}

def get_proxies():
    if os.path.exists("/tmp/ts-run/tailscaled.sock"):
        return {
            "http": "socks5h://127.0.0.1:1055",
            "https": "socks5h://127.0.0.1:1055"
        }
    return None

def poll_reddit_public_feed():
    print("[FEED LISTENER] Poller started with Session persistence and jitter...", flush=True)
    seen_post_ids = set()
    
    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        proxies = get_proxies()
        if proxies:
            session.proxies.update(proxies)

        for sub in TARGET_SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=10"
            try:
                res = session.get(url, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    posts = data.get("data", {}).get("children", [])
                    print(f"[POLL r/{sub}] Found {len(posts)} posts.", flush=True)

                    for item in posts:
                        post = item.get("data", {})
                        post_id = post.get("id")
                        
                        if not post_id or post_id in seen_post_ids:
                            continue
                        seen_post_ids.add(post_id)

                        title = post.get("title", "")
                        selftext = post.get("selftext", "")
                        full_text = f"{title} {selftext}".lower()

                        if any(k in full_text for k in KEYWORDS):
                            payload = {
                                "source_platform": "reddit",
                                "username": post.get("author", "[deleted]"),
                                "user_id": post_id,
                                "post_url": f"https://reddit.com{post.get('permalink', '')}",
                                "post_text": f"Title: {title}\nDetails: {selftext[:500]}"
                            }
                            try:
                                eval_res = requests.post(API_URL, json=payload, timeout=10)
                                print(f"[INGESTED] {post_id} from r/{sub} (@{payload['username']}): {eval_res.status_code}", flush=True)
                            except Exception as post_err:
                                print(f"[API ERROR] Failed to send {post_id}: {post_err}", flush=True)

                elif res.status_code == 429:
                    print(f"[FEED r/{sub}] 429 Rate Limited. Sleeping 60s...", flush=True)
                    time.sleep(60)
                else:
                    print(f"[FEED r/{sub}] HTTP {res.status_code} - CDN block.", flush=True)

            except Exception as e:
                print(f"[FEED r/{sub} ERROR] {e}", flush=True)

            time.sleep(random.uniform(3, 7))

        time.sleep(random.uniform(45, 60))

if __name__ == "__main__":
    poll_reddit_public_feed()
