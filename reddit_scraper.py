import os
import time
import requests
import praw

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "dispute-agent:v1.0 (by /u/YOUR_REDDIT_USERNAME)")

API_URL = os.getenv("API_PUBLIC_URL", "https://dispute-api-xyl7.onrender.com") + "/api/v1/leads/evaluate"

TARGET_SUBREDDITS = "unitedairlines+delta+americanairlines+travel+flights"
KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stuck", "hours late", "missed connection"]

def run_reddit_listener():
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        print("[REDDIT SCRAPER] Missing Reddit API credentials. Waiting...", flush=True)
        return

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT
    )

    subreddit = reddit.subreddit(TARGET_SUBREDDITS)
    print(f"[REDDIT SCRAPER] Listening for new posts on r/{TARGET_SUBREDDITS}...", flush=True)

    for submission in subreddit.stream.submissions(skip_existing=True):
        full_text = f"{submission.title} {submission.selftext}".lower()
        if any(keyword in full_text for keyword in KEYWORDS):
            payload = {
                "source_platform": "reddit",
                "username": str(submission.author.name) if submission.author else "[deleted]",
                "user_id": submission.id,
                "post_url": f"https://reddit.com{submission.permalink}",
                "post_text": f"Title: {submission.title}\nDetails: {submission.selftext[:500]}"
            }
            try:
                res = requests.post(API_URL, json=payload, timeout=10)
                print(f"[INGESTED REDDIT POST] {submission.id} | Status: {res.status_code} | Res: {res.text}", flush=True)
            except Exception as e:
                print(f"[INGEST ERROR] Failed to send post {submission.id}: {e}", flush=True)

if __name__ == "__main__":
    while True:
        try:
            run_reddit_listener()
        except Exception as e:
            print(f"[REDDIT RESTART] Listener dropped: {e}", flush=True)
            time.sleep(30)
