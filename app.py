from flask import Flask, render_template, jsonify, request
from flask import send_file
from datetime import datetime, timedelta
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def get_mentions():
    brand = request.args.get("brand", "badinka")  # Default to 'badinka'
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM mentions WHERE brand = %s ORDER BY created DESC LIMIT 100;",
        (brand,)
    )
    mentions = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(mentions)

@app.route("/delete", methods=["POST"])
def delete_mention():
    data = request.get_json()
    mention_id = data.get("id")
    if not mention_id:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM mentions WHERE id = %s;", (mention_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "deleted", "id": mention_id})

@app.route("/stats")
def get_stats():
    brand = request.args.get("brand", "badinka")  # brand param

    conn = get_db_connection()
    cur = conn.cursor()

    # --- Date range for today ---
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # --- Daily Mentions ---
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'post' AND created >= %s", (brand, start_of_day))
    daily_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'comment' AND created >= %s", (brand, start_of_day))
    daily_comments = cur.fetchone()[0]

    # --- Total Mentions ---
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'post'", (brand,))
    total_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'comment'", (brand,))
    total_comments = cur.fetchone()[0]

    # --- Sentiment Counts ---
    cur.execute("""
        SELECT sentiment, COUNT(*) 
        FROM mentions 
        WHERE brand = %s 
        GROUP BY sentiment
    """, (brand,))
    sentiment_counts = dict(cur.fetchall())

    cur.close()
    conn.close()

    # Fill in missing sentiments with 0
    pos = sentiment_counts.get("positive", 0)
    neu = sentiment_counts.get("neutral", 0)
    neg = sentiment_counts.get("negative", 0)
    total = pos + neu + neg

    # Perception score (0-100 scale)
    score = round((pos * 100 + neu * 50 + neg * 0) / total) if total > 0 else 0

    return jsonify({
        "brand": brand,
        "daily": {"posts": daily_posts, "comments": daily_comments},
        "total": {"posts": total_posts, "comments": total_comments},
        "sentiment": {"positive": pos, "neutral": neu, "negative": neg},
        "score": score
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
