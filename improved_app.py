#!/usr/bin/env python3
"""
Improved Flask frontend for Reddit Brand Monitoring
- Better error handling and performance
- Connection pooling
- Commercial-ready features
"""

from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime, timedelta
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import logging
from functools import wraps
import time
from typing import Dict, Any, Optional
import io
import csv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Database connection pool
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(
        1, 20,  # min and max connections
        DATABASE_URL
    )
    logger.info("Database connection pool created successfully")
except Exception as e:
    logger.error(f"Failed to create database pool: {e}")
    db_pool = None

def get_db_connection():
    """Get database connection from pool"""
    if db_pool:
        return db_pool.getconn()
    else:
        return psycopg2.connect(DATABASE_URL)

def return_db_connection(conn):
    """Return connection to pool"""
    if db_pool:
        db_pool.putconn(conn)
    else:
        conn.close()

def handle_db_errors(f):
    """Decorator to handle database errors gracefully"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except psycopg2.Error as e:
            logger.error(f"Database error in {f.__name__}: {e}")
            return jsonify({"error": "Database error occurred"}), 500
        except Exception as e:
            logger.error(f"Unexpected error in {f.__name__}: {e}")
            return jsonify({"error": "An unexpected error occurred"}), 500
    return decorated_function

def validate_brand(brand: str) -> bool:
    """Validate brand parameter"""
    allowed_brands = {"badinka", "iheartraves"}
    return brand in allowed_brands

def paginate_results(query: str, params: tuple, page: int = 1, per_page: int = 100) -> Dict[str, Any]:
    """Add pagination to database queries"""
    offset = (page - 1) * per_page
    
    # Get total count
    count_query = f"SELECT COUNT(*) FROM ({query}) AS count_query"
    
    # Add pagination to main query
    paginated_query = f"{query} LIMIT %s OFFSET %s"
    paginated_params = params + (per_page, offset)
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Get total count
    cur.execute(count_query, params)
    total = cur.fetchone()[0]
    
    # Get paginated results
    cur.execute(paginated_query, paginated_params)
    results = cur.fetchall()
    
    cur.close()
    return_db_connection(conn)
    
    return {
        "results": results,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page
        }
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health_check():
    """Health check endpoint for monitoring"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return_db_connection(conn)
        return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503

@app.route("/data")
@handle_db_errors
def get_mentions():
    brand = request.args.get("brand", "badinka")
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 100)), 500)  # Max 500 per page
    
    if not validate_brand(brand):
        return jsonify({"error": "Invalid brand"}), 400
    
    # Build query with filters
    filters = ["brand = %s"]
    params = [brand]
    
    # Date filter
    if request.args.get("date_from"):
        filters.append("created >= %s")
        params.append(request.args.get("date_from"))
    
    if request.args.get("date_to"):
        filters.append("created <= %s")
        params.append(request.args.get("date_to"))
    
    # Sentiment filter
    if request.args.get("sentiment"):
        filters.append("sentiment = %s")
        params.append(request.args.get("sentiment"))
    
    # Subreddit filter
    if request.args.get("subreddit"):
        filters.append("subreddit ILIKE %s")
        params.append(f"%{request.args.get('subreddit')}%")
    
    where_clause = " AND ".join(filters)
    query = f"""
        SELECT id, type, title, body, permalink, created, subreddit, 
               author, score, sentiment, source
        FROM mentions 
        WHERE {where_clause}
        ORDER BY created DESC
    """
    
    result = paginate_results(query, tuple(params), page, per_page)
    
    # Convert datetime objects to ISO strings
    for mention in result["results"]:
        if mention["created"]:
            mention["created"] = mention["created"].isoformat()
    
    return jsonify(result)

@app.route("/delete", methods=["POST"])
@handle_db_errors
def delete_mention():
    data = request.get_json()
    mention_id = data.get("id")
    
    if not mention_id:
        return jsonify({"error": "Missing id"}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM mentions WHERE id = %s", (mention_id,))
    deleted_count = cur.rowcount
    conn.commit()
    cur.close()
    return_db_connection(conn)
    
    if deleted_count == 0:
        return jsonify({"error": "Mention not found"}), 404
    
    return jsonify({"status": "deleted", "id": mention_id})

@app.route("/stats")
@handle_db_errors
def get_stats():
    brand = request.args.get("brand", "badinka")
    tz_offset = int(request.args.get("tz_offset", "0"))  # in minutes
    
    if not validate_brand(brand):
        return jsonify({"error": "Invalid brand"}), 400
    
    now_utc = datetime.utcnow()
    user_now = now_utc - timedelta(minutes=tz_offset)
    user_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = user_start + timedelta(minutes=tz_offset)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Daily stats
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE type = 'post') as daily_posts,
            COUNT(*) FILTER (WHERE type = 'comment') as daily_comments
        FROM mentions 
        WHERE brand = %s AND created >= %s
    """, (brand, start_of_day_utc))
    
    daily_result = cur.fetchone()
    daily_posts, daily_comments = daily_result
    
    # All time stats
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE type = 'post') as total_posts,
            COUNT(*) FILTER (WHERE type = 'comment') as total_comments
        FROM mentions 
        WHERE brand = %s
    """, (brand,))
    
    total_result = cur.fetchone()
    total_posts, total_comments = total_result
    
    # Sentiment analysis
    cur.execute("""
        SELECT sentiment, COUNT(*) 
        FROM mentions 
        WHERE brand = %s AND sentiment IS NOT NULL
        GROUP BY sentiment
    """, (brand,))
    
    sentiment_counts = dict(cur.fetchall())
    
    # Source breakdown
    cur.execute("""
        SELECT source, COUNT(*) 
        FROM mentions 
        WHERE brand = %s
        GROUP BY source
    """, (brand,))
    
    source_counts = dict(cur.fetchall())
    
    cur.close()
    return_db_connection(conn)
    
    # Calculate sentiment score
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
        "score": score,
        "sources": source_counts
    })

@app.route("/weekly_mentions")
@handle_db_errors
def weekly_mentions():
    brand = request.args.get("brand", "badinka")
    tz = request.args.get("tz", "UTC")
    week_offset = int(request.args.get("week_offset", "0"))
    
    if not validate_brand(brand):
        return jsonify({"error": "Invalid brand"}), 400
    
    now_utc = datetime.utcnow()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Convert current UTC to user's local time to find local Monday
    cur.execute("SELECT (%s AT TIME ZONE 'UTC' AT TIME ZONE %s)::timestamp", (now_utc, tz))
    user_now_local = cur.fetchone()[0]
    local_monday = user_now_local - timedelta(days=user_now_local.weekday()) + timedelta(weeks=week_offset)
    local_monday = local_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Convert local Monday back to UTC
    cur.execute("SELECT (%s AT TIME ZONE %s AT TIME ZONE 'UTC')::timestamp", (local_monday, tz))
    start_utc = cur.fetchone()[0]
    end_utc = start_utc + timedelta(days=7)
    
    # Query mentions grouped by user-local date
    cur.execute("""
        SELECT 
            (timezone(%s, created))::date AS local_day, 
            COUNT(*),
            COUNT(*) FILTER (WHERE type = 'post') as posts,
            COUNT(*) FILTER (WHERE type = 'comment') as comments
        FROM mentions 
        WHERE brand = %s AND created >= %s AND created < %s
        GROUP BY local_day
        ORDER BY local_day
    """, (tz, brand, start_utc, end_utc))
    
    rows = cur.fetchall()
    cur.close()
    return_db_connection(conn)
    
    data = {
        row[0].strftime("%Y-%m-%d"): {
            "total": row[1],
            "posts": row[2], 
            "comments": row[3]
        } for row in rows
    }
    
    return jsonify(data)

@app.route("/trending_subreddits")
@handle_db_errors
def trending_subreddits():
    brand = request.args.get("brand", "badinka")
    days = int(request.args.get("days", 7))
    
    if not validate_brand(brand):
        return jsonify({"error": "Invalid brand"}), 400
    
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            subreddit,
            COUNT(*) as mention_count,
            AVG(score) as avg_score,
            COUNT(*) FILTER (WHERE sentiment = 'positive') as positive_mentions,
            COUNT(*) FILTER (WHERE sentiment = 'negative') as negative_mentions
        FROM mentions 
        WHERE brand = %s AND created >= %s
        GROUP BY subreddit
        ORDER BY mention_count DESC
        LIMIT 20
    """, (brand, cutoff_date))
    
    results = cur.fetchall()
    cur.close()
    return_db_connection(conn)
    
    trending = []
    for row in results:
        subreddit, count, avg_score, pos, neg = row
        trending.append({
            "subreddit": subreddit,
            "mention_count": count,
            "avg_score": float(avg_score) if avg_score else 0,
            "positive_mentions": pos,
            "negative_mentions": neg,
            "sentiment_ratio": pos / (pos + neg) if (pos + neg) > 0 else 0.5
        })
    
    return jsonify(trending)

@app.route("/export")
@handle_db_errors
def export_data():
    brand = request.args.get("brand", "badinka")
    format_type = request.args.get("format", "csv")
    
    if not validate_brand(brand):
        return jsonify({"error": "Invalid brand"}), 400
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT id, type, title, body, permalink, created, subreddit, 
               author, score, sentiment, source
        FROM mentions 
        WHERE brand = %s
        ORDER BY created DESC
        LIMIT 10000
    """, (brand,))
    
    mentions = cur.fetchall()
    cur.close()
    return_db_connection(conn)
    
    if format_type == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'id', 'type', 'title', 'body', 'permalink', 'created', 
            'subreddit', 'author', 'score', 'sentiment', 'source'
        ])
        writer.writeheader()
        
        for mention in mentions:
            # Convert datetime to string
            mention_dict = dict(mention)
            if mention_dict['created']:
                mention_dict['created'] = mention_dict['created'].isoformat()
            writer.writerow(mention_dict)
        
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'{brand}_mentions_{datetime.now().strftime("%Y%m%d")}.csv'
        )
    
    return jsonify({"error": "Unsupported format"}), 400

@app.route("/system_status")
@handle_db_errors  
def system_status():
    """Get system status and monitoring info"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Recent activity
    cur.execute("""
        SELECT 
            DATE_TRUNC('hour', created) as hour,
            COUNT(*) as mentions
        FROM mentions 
        WHERE created >= NOW() - INTERVAL '24 hours'
        GROUP BY hour
        ORDER BY hour DESC
    """)
    
    recent_activity = [{"hour": row[0].isoformat(), "mentions": row[1]} for row in cur.fetchall()]
    
    # Database stats
    cur.execute("SELECT COUNT(*) FROM mentions")
    total_mentions = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT brand) FROM mentions")
    total_brands = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT subreddit) FROM mentions")
    total_subreddits = cur.fetchone()[0]
    
    # Source breakdown
    cur.execute("""
        SELECT source, COUNT(*), MAX(created) as last_update
        FROM mentions 
        GROUP BY source
    """)
    
    sources = []
    for row in cur.fetchall():
        sources.append({
            "source": row[0],
            "count": row[1],
            "last_update": row[2].isoformat() if row[2] else None
        })
    
    cur.close()
    return_db_connection(conn)
    
    return jsonify({
        "total_mentions": total_mentions,
        "total_brands": total_brands,
        "total_subreddits": total_subreddits,
        "sources": sources,
        "recent_activity": recent_activity,
        "system_time": datetime.utcnow().isoformat()
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)