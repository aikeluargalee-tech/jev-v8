import httpx
import logging
from typing import List, Dict, Any, Optional
import time
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from app.core.config import settings
from app.core.cache import social_cache
from app.core.database import db

logger = logging.getLogger(__name__)

TWITTER_API_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
SYMBOL_REGEX = re.compile(r"^[A-Z0-9]{1,15}$")


def parse_twitter_timestamp(ts_str: Optional[str]) -> int:
    """Parse various Twitter date formats into unix integer seconds."""
    if not ts_str:
        return int(time.time())
    try:
        if ts_str.endswith("Z"):
            return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
        elif "T" in ts_str:
            return int(datetime.fromisoformat(ts_str).timestamp())
        dt = parsedate_to_datetime(ts_str)
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


class TwitterService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.TWITTER_API_KEY

    def _build_query(self, symbol: str) -> str:
        sym = symbol.upper().replace("$", "").strip()
        if not SYMBOL_REGEX.match(sym):
            raise ValueError(f"Invalid symbol '{symbol}' for query builder. Must be 1-15 alphanumeric characters.")

        names = {
            "BTC": "Bitcoin",
            "SOL": "Solana",
            "ETH": "Ethereum"
        }
        full_name = names.get(sym, sym)
        if sym == full_name:
            return f"${sym} lang:en -is:retweet min_faves:2"
        return f"(${sym} OR {full_name}) lang:en -is:retweet min_faves:2"

    async def fetch_tweets(self, symbol: str, target_count: int = 100) -> Dict[str, Any]:
        """
        Fetches up to target_count tweets for a given symbol.
        In Phase 0-1, if TWITTER_API_KEY is not configured, returns empty real tweets
        without generating fake simulated tweets. Database poisoning is strictly eliminated.
        """
        sym = symbol.upper().replace("$", "").strip()
        if not SYMBOL_REGEX.match(sym):
            raise ValueError(f"Invalid crypto symbol '{symbol}'. Must be 1-15 alphanumeric characters.")

        cache_key = f"tweets_{sym}_{target_count}"
        cached = await social_cache.get(cache_key)
        if cached:
            return cached

        # Check known tweet IDs in local database
        known_ids = await db.get_known_ids(sym, limit=5000)

        # If API key is not configured, return existing real DB tweets or empty
        if not self.api_key or self.api_key.strip() == "":
            logger.info("TWITTER_API_KEY not configured. Checking DB for real historical tweets.")
            db_tweets = await db.get_recent_tweets(sym, limit=target_count)
            result = {
                "symbol": sym,
                "count": len(db_tweets),
                "target_count": target_count,
                "api_calls_made": 0,
                "cost_saved": True,
                "yield_deficit": len(db_tweets) < target_count,
                "tweets": db_tweets,
                "is_fallback": len(db_tweets) == 0,
                "cost_usd": 0.0
            }
            await social_cache.set(cache_key, result, ttl=settings.CACHE_TTL_SECONDS)
            return result

        # Call TwitterAPI.io with pagination & early stopping
        new_tweets: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        headers = {"X-API-Key": self.api_key}
        query = self._build_query(sym)

        early_stopped = False
        api_pages_called = 0
        max_pages = min(35, max(5, (target_count // 15) + 3))

        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
            while len(new_tweets) < target_count and api_pages_called < max_pages:
                api_pages_called += 1
                params = {
                    "query": query,
                    "queryType": "Latest"
                }
                if cursor:
                    params["cursor"] = cursor

                try:
                    res = await client.get(TWITTER_API_URL, headers=headers, params=params)
                    if res.status_code != 200:
                        logger.error(f"TwitterAPI.io returned {res.status_code}: {res.text}")
                        break

                    data = res.json()
                    raw_tweets = data.get("tweets") or data.get("data") or []
                    if not raw_tweets:
                        break

                    for t in raw_tweets:
                        tid = str(t.get("id", ""))
                        if tid in known_ids:
                            early_stopped = True
                            break

                        pub_ts = parse_twitter_timestamp(
                            t.get("createdAt") or t.get("created_at") or t.get("timestamp")
                        )
                        tweet_obj = {
                            "id": tid,
                            "text": t.get("text", ""),
                            "created_at": t.get("createdAt") or t.get("created_at", ""),
                            "timestamp_epoch": pub_ts,
                            "likes": int(t.get("likeCount") or t.get("likes") or 0),
                            "retweets": int(t.get("retweetCount") or t.get("retweets") or 0),
                            "replies": int(t.get("replyCount") or t.get("replies") or 0),
                            "author_username": t.get("author", {}).get("userName") or t.get("author_username", "unknown"),
                            "author_followers": int(t.get("author", {}).get("followers") or t.get("author_followers") or 0),
                            "author_verified": bool(t.get("author", {}).get("isBlueVerified") or t.get("author_verified", False))
                        }
                        new_tweets.append(tweet_obj)
                        if len(new_tweets) >= target_count:
                            break

                    if early_stopped:
                        break

                    cursor = data.get("next_cursor") or data.get("cursor")
                    if not cursor:
                        break

                except Exception as e:
                    logger.error(f"Error querying TwitterAPI.io for {sym}: {e}")
                    break

        # Save real new tweets to DB
        if new_tweets:
            await db.save_tweets(sym, new_tweets)

        # Merge with existing DB historical tweets if needed
        combined_tweets = list(new_tweets)
        if len(combined_tweets) < target_count:
            db_historical = await db.get_recent_tweets(sym, limit=target_count * 2)
            existing_ids = {t["id"] for t in combined_tweets}
            for dbt in db_historical:
                if dbt["id"] not in existing_ids:
                    combined_tweets.append(dbt)
                    existing_ids.add(dbt["id"])
                    if len(combined_tweets) >= target_count:
                        break

        # TwitterAPI.io pricing: $0.15 per 1,000 tweets retrieved
        tweet_cost_usd = round(len(new_tweets) * 0.00015, 6)

        result = {
            "symbol": sym,
            "count": len(combined_tweets),
            "newly_fetched_count": len(new_tweets),
            "target_count": target_count,
            "api_pages_called": api_pages_called,
            "early_stopped": early_stopped,
            "yield_deficit": len(combined_tweets) < target_count,
            "tweets": combined_tweets,
            "is_fallback": len(combined_tweets) == 0,
            "cost_usd": tweet_cost_usd
        }
        await social_cache.set(cache_key, result, ttl=settings.CACHE_TTL_SECONDS)
        return result


twitter_service = TwitterService()
