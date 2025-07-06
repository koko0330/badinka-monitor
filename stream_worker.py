import praw
import json
import re
import time
from datetime import datetime, timezone

MENTIONS_FILE = "mentions.json"
KEYWORD_PATTERN = re.compile(r'[@#]?(badinka)(\\.com)?', re.IGNORECASE)

reddit = praw.Reddit(
    client_id="z12aa_E8kaHr_vC9LL6xCw",
    client_secret="AfCarYADJDQ2MU3rdIUW1KjMDRvSrw",
    user_agent="BrandMentionBot/0.1 by ConfectionInfamous97"
)

seen_ids = set()
mentions = []

def save_data():
    with open(MENTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(mentions, f, ensure_ascii=False, indent=2)

subreddit = reddit.subreddit("all")
post_stream = subreddit.stream.submissions(skip_existing=True)
comment_stream = subreddit.stream.comments(skip_existing=True)

print("🔁 Starting Reddit stream...")

try:
    while True:
        now = time.time()

        # Posts
        try:
            post = next(post_stream)
            if post.id not in seen_ids:
                text = f"{post.title or ''} {post.selftext or ''}"
                if KEYWORD_PATTERN.search(text):
                    mentions.append({
                        "type": "post",
                        "id": post.id,
                        "title": post.title,
                        "body": post.selftext,
                        "permalink": f"https://reddit.com{post.permalink}",
                        "created": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
                        "subreddit": str(post.subreddit),
                        "author": str(post.author),
                        "score": post.score
                    })
                    print(f"🧵 Post match: https://reddit.com{post.permalink}")
                    seen_ids.add(post.id)
        except Exception:
            pass

        # Comments
        try:
            comment = next(comment_stream)
            if comment.id not in seen_ids:
                if KEYWORD_PATTERN.search(comment.body or ""):
                    mentions.append({
                        "type": "comment",
                        "id": comment.id,
                        "body": comment.body,
                        "permalink": f"https://reddit.com{comment.permalink}",
                        "created": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).isoformat(),
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

        # Periodically save
        if len(mentions) % 5 == 0:
            save_data()

except KeyboardInterrupt:
    print("🛑 Exiting stream. Saving data...")
    save_data()
