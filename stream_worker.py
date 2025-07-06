import praw
import re
import time
import requests
from datetime import datetime, timezone

# === Your Render endpoint here ===
POST_ENDPOINT = "https://your-render-app.onrender.com/update"

# === Reddit setup ===
reddit = praw.Reddit(
    client_id="z12aa_E8kaHr_vC9LL6xCw",
    client_secret="AfCarYADJDQ2MU3rdIUW1KjMDRvSrw",
    user_agent="BrandMentionBot/0.1 by ConfectionInfamous97"
)

KEYWORD_PATTERN = re.compile(r'[@#]?(badinka)(\\.com)?', re.IGNORECASE)
seen_ids = set()

def format_mention(item, type_):
    return {
        "type": type_,
        "id": item.id,
        "title": getattr(item, "title", None),
        "body": getattr(item, "selftext", None) if type_ == "post" else item.body,
        "permalink": f"https://reddit.com{item.permalink}",
        "created": datetime.fromtimestamp(item.created_utc, tz=timezone.utc).isoformat(),
        "subreddit": str(item.subreddit),
        "author": str(item.author),
        "score": item.score,
        "link_id": getattr(item, "link_id", None),
        "parent_id": getattr(item, "parent_id", None)
    }

def post_mentions(batch):
    try:
        res = requests.post(POST_ENDPOINT, json=batch)
        print(f"✅ Sent {len(batch)} mentions | {res.status_code}")
    except Exception as e:
        print(f"❌ Failed to post batch: {e}")

subreddit = reddit.subreddit("all")
post_stream = subreddit.stream.submissions(skip_existing=True)
comment_stream = subreddit.stream.comments(skip_existing=True)

buffer = []

print("🚀 Streaming Reddit for 'Badinka' on Replit...")

try:
    while True:
        # Posts
        try:
            post = next(post_stream)
            if post.id not in seen_ids:
                text = f"{post.title or ''} {post.selftext or ''}"
                if KEYWORD_PATTERN.search(text):
                    mention = format_mention(post, "post")
                    buffer.append(mention)
                    seen_ids.add(post.id)
        except Exception:
            pass

        # Comments
        try:
            comment = next(comment_stream)
            if comment.id not in seen_ids:
                if KEYWORD_PATTERN.search(comment.body):
                    mention = format_mention(comment, "comment")
                    buffer.append(mention)
                    seen_ids.add(comment.id)
        except Exception:
            pass

        # Send batch every 5 mentions
        if len(buffer) >= 5:
            post_mentions(buffer)
            buffer = []

        time.sleep(1)

except KeyboardInterrupt:
    print("🛑 Stopping...")
    if buffer:
        post_mentions(buffer)
