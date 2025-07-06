import praw
import pandas as pd
from datetime import datetime, timezone
import time
import re

# === CONFIG ===
reddit = praw.Reddit(
    client_id="z12aa_E8kaHr_vC9LL6xCw",
    client_secret="AfCarYADJDQ2MU3rdIUW1KjMDRvSrw",
    user_agent="BrandMentionBot/0.1 by ConfectionInfamous97"
)

KEYWORD = "Badinka"
OUTPUT_FILE = "badinka_mentions_live.csv"
SAVE_INTERVAL = 60  # in seconds

# === MATCHING REGEX ===
keyword_pattern = re.compile(r'[@#]?(badinka)(\.com)?', re.IGNORECASE)

# === INIT ===
seen_ids = set()
all_data = []
last_save_time = time.time()

print(f"🔍 Monitoring Reddit for variations of '{KEYWORD}'...")

subreddit = reddit.subreddit("all")
post_stream = subreddit.stream.submissions(skip_existing=True)
comment_stream = subreddit.stream.comments(skip_existing=True)

# === STREAMING LOOP ===
try:
    while True:
        now = time.time()

        # Check new posts
        try:
            post = next(post_stream)
            if post.id not in seen_ids:
                text = f"{post.title or ''} {post.selftext or ''}"
                if keyword_pattern.search(text):
                    all_data.append({
                        "type": "post",
                        "id": post.id,
                        "title": post.title,
                        "body": post.selftext,
                        "permalink": f"https://reddit.com{post.permalink}",
                        "created": datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                        "subreddit": str(post.subreddit),
                        "author": str(post.author),
                        "score": post.score
                    })
                    print(f"🧵 Post match: https://reddit.com{post.permalink}")
                    seen_ids.add(post.id)
        except Exception:
            pass

        # Check new comments
        try:
            comment = next(comment_stream)
            if comment.id not in seen_ids:
                if keyword_pattern.search(comment.body or ""):
                    all_data.append({
                        "type": "comment",
                        "id": comment.id,
                        "body": comment.body,
                        "permalink": f"https://reddit.com{comment.permalink}",
                        "created": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc),
                        "subreddit": str(comment.subreddit),
                        "author": str(comment.author),
                        "score": comment.score,
                        "link_id": comment.link_id,
                        "parent_id": comment.parent_id
                    })
                    print(f"💬 Comment match: https://reddit.com{comment.permalink}")
                    seen_ids.add(comment.id)
        except Exception:
            pass

        # Auto-save every 60 seconds
        if now - last_save_time > SAVE_INTERVAL:
            df = pd.DataFrame(all_data)
            df.to_csv(OUTPUT_FILE, index=False)
            print(f"💾 Auto-saved {len(df)} results to {OUTPUT_FILE}")
            last_save_time = now

except KeyboardInterrupt:
    print("\n🛑 Manual stop detected. Final save in progress...")
    df = pd.DataFrame(all_data)
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"✅ Saved {len(df)} mentions to '{OUTPUT_FILE}' before exiting.")
