#!/usr/bin/env python3
"""
Unified Reddit Brand Monitoring System
- Single Reddit app required
- Multiple data sources for completeness
- Proper rate limiting and error handling
- Commercial-ready architecture
"""

import asyncio
import aiohttp
import aioredis
import praw
import prawcore
import requests
import feedparser
import time
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
import psycopg2
from psycopg2.extras import execute_values
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Mention:
    id: str
    type: str  # 'post', 'comment'
    title: Optional[str]
    body: Optional[str]
    permalink: str
    created: str
    subreddit: str
    author: str
    score: int
    sentiment: Optional[str]
    brand: str
    source: str  # 'praw', 'pushshift', 'rss', 'json'

class RateLimiter:
    def __init__(self, requests_per_minute: int = 50):
        self.requests_per_minute = requests_per_minute
        self.requests = []
    
    async def acquire(self):
        now = time.time()
        # Remove requests older than 1 minute
        self.requests = [req_time for req_time in self.requests if now - req_time < 60]
        
        if len(self.requests) >= self.requests_per_minute:
            sleep_time = 60 - (now - self.requests[0])
            if sleep_time > 0:
                logger.info(f"Rate limit reached, sleeping for {sleep_time:.2f} seconds")
                await asyncio.sleep(sleep_time)
                return await self.acquire()
        
        self.requests.append(now)

class DatabaseManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables"""
        conn = psycopg2.connect(self.database_url)
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mentions (
                id VARCHAR(50) PRIMARY KEY,
                type VARCHAR(20) NOT NULL,
                title TEXT,
                body TEXT,
                permalink TEXT NOT NULL,
                created TIMESTAMP WITH TIME ZONE NOT NULL,
                subreddit VARCHAR(100) NOT NULL,
                author VARCHAR(100) NOT NULL,
                score INTEGER,
                sentiment VARCHAR(20),
                brand VARCHAR(50) NOT NULL,
                source VARCHAR(20) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            
            CREATE INDEX IF NOT EXISTS idx_mentions_brand ON mentions(brand);
            CREATE INDEX IF NOT EXISTS idx_mentions_created ON mentions(created);
            CREATE INDEX IF NOT EXISTS idx_mentions_type ON mentions(type);
        """)
        
        conn.commit()
        cur.close()
        conn.close()
    
    def insert_mentions(self, mentions: List[Mention]):
        if not mentions:
            return
        
        conn = psycopg2.connect(self.database_url)
        cur = conn.cursor()
        
        rows = [
            (
                mention.id, mention.type, mention.title, mention.body,
                mention.permalink, mention.created, mention.subreddit,
                mention.author, mention.score, mention.sentiment,
                mention.brand, mention.source
            )
            for mention in mentions
        ]
        
        query = """
            INSERT INTO mentions (
                id, type, title, body, permalink, created, subreddit,
                author, score, sentiment, brand, source
            ) VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                score = EXCLUDED.score,
                sentiment = EXCLUDED.sentiment;
        """
        
        execute_values(cur, query, rows)
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"Inserted {len(mentions)} mentions to database")
    
    def get_existing_ids(self) -> Set[str]:
        conn = psycopg2.connect(self.database_url)
        cur = conn.cursor()
        cur.execute("SELECT id FROM mentions")
        ids = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()
        return ids

class SentimentAnalyzer:
    def __init__(self, api_token: str):
        self.api_url = "https://api-inference.huggingface.co/models/tabularisai/multilingual-sentiment-analysis"
        self.headers = {"Authorization": f"Bearer {api_token}"}
        self.rate_limiter = RateLimiter(requests_per_minute=100)  # HF has higher limits
    
    async def analyze(self, text: str) -> str:
        if not text or len(text.strip()) == 0:
            return "neutral"
        
        await self.rate_limiter.acquire()
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"inputs": text[:1000]}
                async with session.post(
                    self.api_url, 
                    headers=self.headers, 
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        scores = result[0]
                        top_label = max(scores, key=lambda x: x['score'])['label'].lower()
                        
                        if 'positive' in top_label:
                            return "positive"
                        elif 'negative' in top_label:
                            return "negative"
                        else:
                            return "neutral"
                    else:
                        logger.warning(f"Sentiment API returned {response.status}")
                        return "neutral"
        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            return "neutral"

class UnifiedRedditMonitor:
    def __init__(self, config: Dict):
        self.config = config
        self.brands = {
            name: re.compile(pattern, re.IGNORECASE) 
            for name, pattern in config['brands'].items()
        }
        
        # Initialize components
        self.db = DatabaseManager(config['database_url'])
        self.sentiment = SentimentAnalyzer(config['hf_api_token'])
        self.seen_ids = self.db.get_existing_ids()
        
        # Rate limiters for different sources
        self.praw_limiter = RateLimiter(45)  # Conservative for PRAW
        self.json_limiter = RateLimiter(100)  # More aggressive for JSON API
        
        # Initialize Reddit client
        self.reddit = praw.Reddit(
            client_id=config['reddit']['client_id'],
            client_secret=config['reddit']['client_secret'],
            user_agent=config['reddit']['user_agent']
        )
        
        # Subreddits to monitor
        self.subreddits = config.get('subreddits', ['all'])
        
        # Collection buffer
        self.mention_buffer: List[Mention] = []
        self.buffer_size = 50
        
    def find_brands(self, text: str) -> List[str]:
        """Find brand mentions in text"""
        brands_found = []
        for brand, pattern in self.brands.items():
            if pattern.search(text):
                brands_found.append(brand)
        return brands_found
    
    async def process_mention_buffer(self):
        """Process and save mentions from buffer"""
        if not self.mention_buffer:
            return
        
        # Add sentiment analysis
        for mention in self.mention_buffer:
            if mention.sentiment is None:
                text = f"{mention.title or ''} {mention.body or ''}"
                mention.sentiment = await self.sentiment.analyze(text)
        
        # Save to database
        self.db.insert_mentions(self.mention_buffer)
        self.mention_buffer.clear()
    
    async def stream_praw_comments(self):
        """Stream comments using PRAW"""
        logger.info("Starting PRAW comment stream...")
        
        while True:
            try:
                subreddit = self.reddit.subreddit("+".join(self.subreddits))
                comment_stream = subreddit.stream.comments(skip_existing=True, pause_after=10)
                
                async for comment in self._async_praw_stream(comment_stream):
                    if comment is None:
                        await asyncio.sleep(1)
                        continue
                    
                    await self.praw_limiter.acquire()
                    
                    if comment.id in self.seen_ids:
                        continue
                    
                    brands = self.find_brands(comment.body)
                    if brands:
                        for brand in brands:
                            mention = Mention(
                                id=comment.id,
                                type="comment",
                                title=None,
                                body=comment.body,
                                permalink=f"https://reddit.com{comment.permalink}",
                                created=datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).isoformat(),
                                subreddit=str(comment.subreddit),
                                author=str(comment.author),
                                score=comment.score,
                                sentiment=None,
                                brand=brand,
                                source="praw"
                            )
                            
                            self.mention_buffer.append(mention)
                            self.seen_ids.add(comment.id)
                            logger.info(f"Found comment mention: {brand} in r/{comment.subreddit}")
                    
                    if len(self.mention_buffer) >= self.buffer_size:
                        await self.process_mention_buffer()
                        
            except prawcore.exceptions.TooManyRequests:
                logger.warning("PRAW rate limited, sleeping 60 seconds")
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"PRAW stream error: {e}")
                await asyncio.sleep(10)
    
    async def _async_praw_stream(self, stream):
        """Convert PRAW stream to async generator"""
        loop = asyncio.get_event_loop()
        
        def get_next():
            try:
                return next(stream)
            except StopIteration:
                return None
        
        while True:
            item = await loop.run_in_executor(None, get_next)
            yield item
    
    async def monitor_json_api(self):
        """Monitor Reddit JSON API for comments"""
        logger.info("Starting JSON API monitoring...")
        
        while True:
            try:
                for subreddit_chunk in self._chunk_subreddits():
                    await self.json_limiter.acquire()
                    
                    chunk_str = "+".join(subreddit_chunk)
                    url = f"https://www.reddit.com/r/{chunk_str}/comments.json?limit=100"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                            if response.status == 200:
                                data = await response.json()
                                await self._process_json_comments(data)
                            elif response.status == 429:
                                logger.warning("JSON API rate limited")
                                await asyncio.sleep(30)
                    
                    await asyncio.sleep(2)  # Small delay between chunks
                
                await asyncio.sleep(15)  # Wait before next full cycle
                
            except Exception as e:
                logger.error(f"JSON API error: {e}")
                await asyncio.sleep(10)
    
    def _chunk_subreddits(self, chunk_size: int = 5):
        """Split subreddits into chunks for API calls"""
        if 'all' in self.subreddits:
            return [['all']]
        
        for i in range(0, len(self.subreddits), chunk_size):
            yield self.subreddits[i:i + chunk_size]
    
    async def _process_json_comments(self, data: Dict):
        """Process comments from JSON API response"""
        children = data.get("data", {}).get("children", [])
        
        for item in children:
            comment_data = item.get("data", {})
            comment_id = comment_data.get("id")
            
            if not comment_id or comment_id in self.seen_ids:
                continue
            
            body = comment_data.get("body", "")
            if len(body) < 10:  # Skip very short comments
                continue
            
            brands = self.find_brands(body)
            if brands:
                for brand in brands:
                    mention = Mention(
                        id=comment_id,
                        type="comment",
                        title=None,
                        body=body,
                        permalink=f"https://reddit.com{comment_data['permalink']}",
                        created=datetime.fromtimestamp(comment_data["created_utc"], tz=timezone.utc).isoformat(),
                        subreddit=comment_data["subreddit"],
                        author=comment_data["author"],
                        score=comment_data["score"],
                        sentiment=None,
                        brand=brand,
                        source="json"
                    )
                    
                    self.mention_buffer.append(mention)
                    self.seen_ids.add(comment_id)
                    logger.info(f"Found JSON comment mention: {brand} in r/{comment_data['subreddit']}")
    
    async def monitor_rss_feeds(self):
        """Monitor RSS feeds for new posts"""
        logger.info("Starting RSS feed monitoring...")
        
        while True:
            try:
                for subreddit in self.subreddits:
                    if subreddit == 'all':
                        continue
                    
                    url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                            if response.status == 200:
                                rss_text = await response.text()
                                await self._process_rss_feed(rss_text, subreddit)
                    
                    await asyncio.sleep(5)  # Delay between subreddits
                
                await asyncio.sleep(300)  # Check RSS every 5 minutes
                
            except Exception as e:
                logger.error(f"RSS monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def _process_rss_feed(self, rss_text: str, subreddit: str):
        """Process RSS feed content"""
        try:
            feed = feedparser.parse(rss_text)
            
            for entry in feed.entries:
                post_id = entry.link.split('/')[-2] if '/' in entry.link else entry.link
                
                if post_id in self.seen_ids:
                    continue
                
                title = entry.title
                content = getattr(entry, 'content', [{}])[0].get('value', '')
                text = f"{title} {content}"
                
                brands = self.find_brands(text)
                if brands:
                    for brand in brands:
                        mention = Mention(
                            id=post_id,
                            type="post",
                            title=title,
                            body=content,
                            permalink=entry.link,
                            created=datetime.now(timezone.utc).isoformat(),  # RSS doesn't always have precise time
                            subreddit=subreddit,
                            author="unknown",  # RSS might not include author
                            score=0,
                            sentiment=None,
                            brand=brand,
                            source="rss"
                        )
                        
                        self.mention_buffer.append(mention)
                        self.seen_ids.add(post_id)
                        logger.info(f"Found RSS post mention: {brand} in r/{subreddit}")
                        
        except Exception as e:
            logger.error(f"RSS processing error: {e}")
    
    async def periodic_buffer_flush(self):
        """Periodically flush the mention buffer"""
        while True:
            await asyncio.sleep(30)  # Flush every 30 seconds
            if self.mention_buffer:
                await self.process_mention_buffer()
    
    async def run(self):
        """Run all monitoring tasks concurrently"""
        logger.info("Starting unified Reddit monitor...")
        
        tasks = [
            self.stream_praw_comments(),
            self.monitor_json_api(),
            self.monitor_rss_feeds(),
            self.periodic_buffer_flush()
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)

def load_config() -> Dict:
    """Load configuration from environment variables"""
    return {
        'database_url': os.getenv('DATABASE_URL'),
        'hf_api_token': os.getenv('HF_API_TOKEN'),
        'brands': {
            'badinka': r'[@#]?badinka(?:\.com)?',
            'iheartraves': r'[@#]?iheartraves(?:\.com)?'
        },
        'reddit': {
            'client_id': os.getenv('REDDIT_CLIENT_ID'),
            'client_secret': os.getenv('REDDIT_CLIENT_SECRET'),
            'user_agent': 'UnifiedBrandMonitor/1.0'
        },
        'subreddits': [
            "Rezz", "aves", "ElectricForest", "sewing", "avesfashion",
            "cyber_fashion", "aveoutfits", "RitaFourEssenceSystem", "SoftDramatics", 
            "Shein", "avesNYC", "veld", "BADINKA", "PlusSize", "LostLandsMusicFest",
            "festivals", "avefashion", "avesafe", "EDCOrlando", "findfashion",
            "BassCanyon", "Aerials", "electricdaisycarnival", "bonnaroo",
            "Tomorrowland", "femalefashion", "Soundhaven", "warpedtour", "Shambhala",
            "Lollapalooza", "EDM", "BeyondWonderland", "kandi"
        ]
    }

if __name__ == "__main__":
    config = load_config()
    monitor = UnifiedRedditMonitor(config)
    asyncio.run(monitor.run())