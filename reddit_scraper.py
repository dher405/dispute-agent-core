import os
import time
import requests

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"
TARGET_SUBREDDITS = ["unitedairlines", "delta", "americanairlines", "travel", "flights"]
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection", "stranded"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def get_proxies():
    if os.path.exists("/tmp/ts-run/tailscaled.sock"):
        return {
            "http": "socks5h://127.0.0.1:1055",
            "https": "socks5h://127.0.0.1:1055"
        }
    return None

def poll_reddit_public_feed():
    print("[FEED LISTENER] Background thread running. Starting poll cycle...", flush=True)
    seen_post_ids = set()

    while True:
        proxies = get_proxies()
        for sub in TARGET_SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=10"
            try:
                res = requests.get(url, headers=HEADERS, proxies=proxies, timeout=10)
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
                    print(f"[FEED r/{sub}] 429 Rate Limited. Sleeping 30s...", flush=True)
                    time.sleep(30)
                else:
                    print(f"[FEED r/{sub}] HTTP {res.status_code}", flush=True)

            except Exception as e:
                print(f"[FEED r/{sub} ERROR] {e}", flush=True)

            time.sleep(3)  # Short delay between subreddit requests

        time.sleep(45)  # Rest interval between full cycles

if __name__ == "__main__":
    poll_reddit_public_feed()
