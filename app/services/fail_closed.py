from dataclasses import dataclass
import datetime
import math
from typing import List, Tuple, Dict, Optional

# Configuration constants
PRIMARY_VENUE = "kraken"
PAIR = "BTC/USD"
RULE_VER = "v8.0"
SHADOW_MODE_MIN_VERDICTS = 60

# Warmup requirements: 48 candles is insufficient for EMA200
WARMUP = {
    "SWING": {"15m": 0, "1h": 250, "4h": 300, "1D": 250},
    "DAY_CONTEXT": {"15m": 300, "1h": 250, "4h": 250, "1D": 0}
}

STALENESS_THRESH = {
    "SWING": {"market_s": 300, "funding_s": 300},
    "DAY_CONTEXT": {"market_s": 60, "funding_s": 300}
}


@dataclass
class MarketSnapshot:
    venue: str
    pair: str
    last_price: float
    last_candle_close_ts: datetime.datetime
    freshness_market_s: float
    freshness_funding_s: float
    is_fallback: bool
    is_synthetic: bool
    candle_count_15m: int
    candle_count_1h: int
    candle_count_4h: int
    candle_count_1d: int
    profile: str  # "SWING" or "DAY_CONTEXT"


def check_fail_closed(snap: MarketSnapshot) -> Tuple[bool, List[str]]:
    """
    Returns (passed, reasons_if_failed). If not passed -> NO_VERDICT.
    Enforces fail-closed on fallback, synthetic data, staleness, insufficient warmup, or invalid price.
    """
    fails = []

    # 1. Synthetic/fallback flag
    if snap.is_fallback or snap.is_synthetic:
        fails.append(
            f"DEMO DATA — NOT LIVE: is_fallback={snap.is_fallback} is_synthetic={snap.is_synthetic} "
            f"from market_service.py fallback path"
        )

    # 2. Staleness check
    profile = snap.profile if snap.profile in STALENESS_THRESH else "SWING"
    thresh = STALENESS_THRESH[profile]
    if snap.freshness_market_s > thresh["market_s"]:
        fails.append(f"Market stale {snap.freshness_market_s:.1f}s > {thresh['market_s']}s")
    if snap.freshness_funding_s > thresh["funding_s"]:
        fails.append(f"Funding stale {snap.freshness_funding_s:.1f}s > {thresh['funding_s']}s")

    # 2.1 Candle data staleness check (verifies exchange feed is active and not frozen)
    if snap.last_candle_close_ts:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        c_ts = snap.last_candle_close_ts
        if c_ts.tzinfo is None:
            c_ts = c_ts.replace(tzinfo=datetime.timezone.utc)
        candle_age_s = max(0.0, (now_utc - c_ts).total_seconds())
        # SWING: 4h candle = 14,400s. Max allowed tolerance = 4.5h (16,200s)
        # DAY_CONTEXT: 15m candle = 900s. Max allowed tolerance = 25m (1,500s)
        max_candle_age_s = 16200.0 if profile == "SWING" else 1500.0
        if candle_age_s > max_candle_age_s:
            fails.append(f"Primary candle stale: {candle_age_s:.0f}s > {max_candle_age_s:.0f}s (exchange feed frozen)")

    # 3. Warmup sufficiency check
    req = WARMUP[profile]
    if snap.candle_count_4h < req["4h"]:
        fails.append(f"Insufficient history 4h: have {snap.candle_count_4h} need {req['4h']} for EMA200 warmup")
    if req["1D"] > 0 and snap.candle_count_1d < req["1D"]:
        fails.append(f"Insufficient history 1D: have {snap.candle_count_1d} need {req['1D']}")
    if req["1h"] > 0 and snap.candle_count_1h < req["1h"]:
        fails.append(f"Insufficient history 1h: have {snap.candle_count_1h} need {req['1h']}")
    if req["15m"] > 0 and snap.candle_count_15m < req["15m"]:
        fails.append(f"Insufficient history 15m: have {snap.candle_count_15m} need {req['15m']}")

    # 4. Price sanity check
    if snap.last_price <= 0 or snap.last_price > 2_000_000:
        fails.append(f"Implausible price {snap.last_price}")

    passed = len(fails) == 0
    return passed, fails


def check_jev_fallback(jev_raw: Dict) -> Tuple[bool, str]:
    """
    If Jev returned deterministic fallback with fixed confidence -> treat as Model Unavailable.
    Triggers NO_VERDICT for Jev-assisted mode.
    """
    if jev_raw.get("is_fallback"):
        return False, "Jev fallback deterministic rule set with fixed confidence — must be treated as Model Unavailable -> NO_VERDICT for Jev-assisted mode"

    # Also detect fixed fallback probabilities/confidences
    conf = jev_raw.get("TradeAction", {}).get("confidence")
    model_ver = str(jev_raw.get("model_ver", ""))
    if conf in [0.5, 0.75, 0.8, 0.9] and model_ver.startswith("fallback"):
        return False, "Jev confidence looks like fixed fallback value"

    return True, ""


def check_cost_guardrail(monthly_cumulative_usd: float, budget_usd: float) -> Tuple[str, bool]:
    """
    Returns (banner_message, block_paid_calls).
    - 80% warning: banner displayed
    - 100% cap: paid calls blocked
    """
    if monthly_cumulative_usd >= budget_usd:
        return f"Cost cap reached ${monthly_cumulative_usd:.2f}/${budget_usd:.2f} — paid calls blocked", True
    if monthly_cumulative_usd >= 0.8 * budget_usd:
        return f"Cost warning 80% ${monthly_cumulative_usd:.2f}/${budget_usd:.2f}", False
    return "", False
