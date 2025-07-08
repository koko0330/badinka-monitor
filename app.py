from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime, time
import os
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def get_mentions():
    brand = request.args.get("brand", "badinka")
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
    brand = request.args.get("brand", "badinka")

    # Get start of "today" in Europe/Sofia and convert to UTC
    local_tz = pytz.timezone("Europe/Sofia")
    now_local = datetime.now(local_tz)
    start_of_day_local = datetime.combine(now_local.date(), time.min).replace(tzinfo=local_tz)
    start_of_day_utc = start_of_day_local.astimezone(pytz.utc)

    conn = get_db_connection()
    cur = conn.cursor()

    # --- Daily Mentions ---
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'post' AND created >= %s", (brand, start_of_day_utc))
    daily_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'comment' AND created >= %s", (brand, start_of_day_utc))
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

    # Fill missing sentiments
    pos = sentiment_counts.get("positive", 0)
    neu = sentiment_counts.get("neutral", 0)
    neg = sentiment_counts.get("negative", 0)
    total = pos + neu + neg

    # Perception score (0-100)
    score = round((pos * 100 + neu * 50 + neg * 0) / total) if total > 0 else 0

    return jsonify({
        "brand": brand,
        "daily": {"posts": daily_posts, "comments": daily_comments},
        "total": {"posts": total_posts, "comments": total_comments},
        "sentiment": {"positive": pos, "neutral": neu, "negative": neg},
        "score": score
    })

@app.route("/download")
def download_csv():
    return send_file("mentions.csv", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
