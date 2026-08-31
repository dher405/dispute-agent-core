import os
import time
import requests

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"
TARGET_SUBREDDITS = "unitedairlines+delta+americanairlines+travel+flights"
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

def poll_reddit_public_feed():
    url = f"https://www.reddit.com/r/{TARGET_SUBREDDITS}/new.json?limit=25"
    seen_post_ids = set()

    print(f"[FEED LISTENER] Polling public feed for r/{TARGET_SUBREDDITS}...", flush=True)

    while True:
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                posts = data.get("data", {}).get("children", [])
                print(f"[POLL] Retrieved {len(posts)} posts.", flush=True)

                for item in posts:
                    post = item.get("data", {})
                    post_id = post.get("id")
                    
                    if post_id in seen_post_ids:
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
                            print(f"[INGESTED] {post_id} (@{payload['username']}): {eval_res.status_code} - {eval_res.text}", flush=True)
                        except Exception as post_err:
                            print(f"[API ERROR] Failed to send {post_id}: {post_err}", flush=True)

            elif res.status_code == 429:
                print("[FEED LISTENER] Rate limited. Backing off for 60s...", flush=True)
                time.sleep(60)
            else:
                print(f"[FEED LISTENER] HTTP status: {res.status_code}", flush=True)

        except Exception as e:
            print(f"[FEED ERROR] Request failed: {e}", flush=True)

        time.sleep(60)

if __name__ == "__main__":
    poll_reddit_public_feed()
