from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime, timedelta
import os
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
    tz_offset = int(request.args.get("tz_offset", "0"))  # in minutes

    now_utc = datetime.utcnow()
    user_now = now_utc - timedelta(minutes=tz_offset)
    user_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = user_start + timedelta(minutes=tz_offset)

    conn = get_db_connection()
    cur = conn.cursor()

    # Daily
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'post' AND created >= %s", (brand, start_of_day_utc))
    daily_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'comment' AND created >= %s", (brand, start_of_day_utc))
    daily_comments = cur.fetchone()[0]

    # All Time
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'post'", (brand,))
    total_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mentions WHERE brand = %s AND type = 'comment'", (brand,))
    total_comments = cur.fetchone()[0]

    # Sentiment
    cur.execute("""
        SELECT sentiment, COUNT(*) 
        FROM mentions 
        WHERE brand = %s 
        GROUP BY sentiment
    """, (brand,))
    sentiment_counts = dict(cur.fetchall())

    cur.close()
    conn.close()

    pos = sentiment_counts.get("positive", 0)
    neu = sentiment_counts.get("neutral", 0)
    neg = sentiment_counts.get("negative", 0)
    total = pos + neu + neg

    score = round((pos * 100 + neu * 50 + neg * 0) / total) if total > 0 else 0

    return jsonify({
        "brand": brand,
        "daily": {"posts": daily_posts, "comments": daily_comments},
        "total": {"posts": total_posts, "comments": total_comments},
        "sentiment": {"positive": pos, "neutral": neu, "negative": neg},
        "score": score
    })

@app.route("/weekly_mentions")
def weekly_mentions():
    brand = request.args.get("brand", "badinka")
    tz = request.args.get("tz", "UTC")
    week_offset = int(request.args.get("week_offset", "0"))

    # Get current UTC time
    now_utc = datetime.utcnow()

    # Convert current UTC to user's local time to find local Monday
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT (%s AT TIME ZONE 'UTC' AT TIME ZONE %s)::timestamp", (now_utc, tz))
    user_now_local = cur.fetchone()[0]
    local_monday = user_now_local - timedelta(days=user_now_local.weekday()) + timedelta(weeks=week_offset)
    local_monday = local_monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # Convert local Monday back to UTC to get correct query window
    cur.execute("SELECT (%s AT TIME ZONE %s AT TIME ZONE 'UTC')::timestamp", (local_monday, tz))
    start_utc = cur.fetchone()[0]
    end_utc = start_utc + timedelta(days=7)

    # Query mentions grouped by user-local date
    cur.execute("""
        SELECT 
            (timezone(%s, created))::date AS local_day
            COUNT(*) 
        FROM mentions 
        WHERE brand = %s AND created >= %s AND created < %s
        GROUP BY local_day
        ORDER BY local_day
    """, (tz, brand, start_utc, end_utc))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    data = {row[0].strftime("%Y-%m-%d"): row[1] for row in rows}
    return jsonify(data)

@app.route("/download")
def download_csv():
    return send_file("mentions.csv", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
