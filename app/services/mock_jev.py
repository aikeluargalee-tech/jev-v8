import hashlib
import json
from typing import Dict, Any


def mock_jev_adapter(compact_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic mock adapter for Phase 0-1.
    Zero Jev credits wasted. Returns the exact shape expected by v8_jev_raw schema.
    is_fallback is False, and model_ver is strictly 'mock-v8'.
    """
    market_cat = compact_state.get("market_cat", {})
    ema = market_cat.get("ema_stack", "neutral")
    rsi = market_cat.get("rsi_cat", "neutral")
    social_meta = compact_state.get("social_meta", {})
    pol = social_meta.get("polarity_cat", "neutral")
    diversity = social_meta.get("diversity_ratio", 0.6)

    if ema == "bullish" and rsi in ["oversold", "neutral_low"] and pol in ["fear", "extreme_fear"]:
        choice = "BUY"
        conf = 0.62
    elif ema == "bearish" and rsi in ["overbought", "neutral_high"] and pol in ["greed", "euphoria"]:
        choice = "SELL"
        conf = 0.58
    else:
        choice = "ABSTAIN"
        conf = 0.55

    return {
        "TradeAction": {
            "choice": choice,
            "confidence": conf,
            "probabilities": {choice: conf}
        },
        "DataQuality": {
            "choice": "reliable" if diversity > 0.5 else "mixed",
            "confidence": 0.7
        },
        "is_fallback": False,
        "model_ver": "mock-v8",
        "latency_ms": 12
    }


def state_hash(compact_state: Dict[str, Any]) -> str:
    """Computes deterministic SHA256 state hash across canonical JSON string."""
    j = json.dumps(compact_state, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()
