from typing import List, Dict, Any, Optional

FEAR_KEYWORDS = {
    "crash", "dump", "liquidation", "sell", "selling", "dead", "bear", "bearish",
    "scam", "rekt", "drop", "loss", "bleeding", "panic", "fear", "down", "dip", "fall"
}

GREED_KEYWORDS = {
    "pump", "moon", "ath", "buy", "buying", "gem", "bull", "bullish", "breakout",
    "rally", "gain", "accumulate", "rocket", "up", "long", "hold", "squeeze"
}


class StatsService:
    @staticmethod
    def process_tweets(tweets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deterministic pre-processing of ingested tweets.
        Calculates author diversity, polarity category, and summary metrics.
        Returns polarity_cat='unavailable' when sample is empty. Never masks empty data as 'neutral'.
        """
        if not tweets:
            return {
                "sample_size": 0,
                "unique_authors_count": 0,
                "diversity_ratio": 0.0,
                "author_diversity_pct": 0.0,
                "total_likes": 0,
                "total_retweets": 0,
                "avg_engagement": 0.0,
                "fear_mentions": 0,
                "greed_mentions": 0,
                "polarity_score": None,
                "sentiment_label": "Unavailable",
                "polarity_cat": "unavailable",
                "engagement_vel": "unavailable",
                "is_available": False,
                "stratified_sample": []
            }

        total_likes = 0
        total_retweets = 0
        total_replies = 0
        authors = set()
        fear_count = 0
        greed_count = 0

        for t in tweets:
            likes = t.get("likes", 0)
            retweets = t.get("retweets", 0)
            replies = t.get("replies", 0)

            total_likes += likes
            total_retweets += retweets
            total_replies += replies

            username = t.get("author_username") or "unknown"
            authors.add(username.lower())

            # Text polarity check
            text_lower = t.get("text", "").lower()
            words = set(text_lower.replace("$", "").replace("#", "").split())

            fear_hits = len(words.intersection(FEAR_KEYWORDS))
            greed_hits = len(words.intersection(GREED_KEYWORDS))

            fear_count += fear_hits
            greed_count += greed_hits

        sample_size = len(tweets)
        unique_authors = len(authors)
        diversity_ratio = round(unique_authors / sample_size, 3) if sample_size > 0 else 0.0
        diversity_pct = round(diversity_ratio * 100.0, 1)
        avg_engagement = round((total_likes + total_retweets * 2) / sample_size, 1)

        total_polar = fear_count + greed_count
        if total_polar > 0:
            polarity_score = round((greed_count - fear_count) / total_polar, 2)
        else:
            polarity_score = 0.0

        # Map polarity score to v8 schema categories
        if polarity_score <= -0.4:
            polarity_cat = "extreme_fear"
            sentiment_label = "Extreme Panic"
        elif polarity_score < -0.1:
            polarity_cat = "fear"
            sentiment_label = "Bearish / Fearful"
        elif polarity_score <= 0.1:
            polarity_cat = "neutral"
            sentiment_label = "Neutral / Mixed"
        elif polarity_score < 0.4:
            polarity_cat = "greed"
            sentiment_label = "Bullish / Optimistic"
        else:
            polarity_cat = "euphoria"
            sentiment_label = "Euphoric / Greedy"

        # Map engagement velocity
        if avg_engagement > 100:
            engagement_vel = "high"
        elif avg_engagement > 20:
            engagement_vel = "normal"
        else:
            engagement_vel = "low"

        return {
            "sample_size": sample_size,
            "unique_authors_count": unique_authors,
            "diversity_ratio": diversity_ratio,
            "author_diversity_pct": diversity_pct,
            "total_likes": total_likes,
            "total_retweets": total_retweets,
            "avg_engagement": avg_engagement,
            "fear_mentions": fear_count,
            "greed_mentions": greed_count,
            "polarity_score": polarity_score,
            "sentiment_label": sentiment_label,
            "polarity_cat": polarity_cat,
            "engagement_vel": engagement_vel,
            "is_available": True
        }


stats_service = StatsService()
