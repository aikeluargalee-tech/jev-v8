import pytest
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from app.core.database import db
from app.services.twitter_service import twitter_service


@pytest.mark.asyncio
async def test_dedup_with_saved_tweets():
    """Verifies database ID deduplication and known_ids tracking."""
    now = int(time.time())
    test_tweets = [
        {
            "id": f"real_test_tweet_{now}_{i}",
            "text": f"Bitcoin market discussion #{i}",
            "created_at": "2026-10-10T12:00:00Z",
            "timestamp_epoch": now - i * 60,
            "likes": 10 + i,
            "retweets": i,
            "replies": 1,
            "author_username": f"analyst_{i}",
            "author_followers": 5000,
            "author_verified": False
        }
        for i in range(20)
    ]

    saved = await db.save_tweets("BTC", test_tweets)
    assert saved == 20, f"Expected 20 saved tweets, got {saved}"

    # Verify duplicate insert ignored
    saved_dupes = await db.save_tweets("BTC", test_tweets)
    assert saved_dupes == 0, "Duplicate tweets must be ignored"

    # Verify known IDs
    known = await db.get_known_ids("BTC")
    for t in test_tweets:
        assert t["id"] in known


@pytest.mark.asyncio
async def test_invalid_symbol_rejected():
    with pytest.raises(ValueError):
        await twitter_service.fetch_tweets("BTC; DROP TABLE", target_count=50)
