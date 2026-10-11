import math
from typing import List, Optional, Dict, Any, Tuple


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """
    Exponential Moving Average with proper warmup.
    Returns list of same length. The first (period - 1) elements are None.
    Index (period - 1) is initialized with the Simple Moving Average (SMA).
    """
    if len(values) < period:
        return [None] * len(values)

    k = 2.0 / (period + 1)
    ema_vals: List[Optional[float]] = [None] * len(values)
    sma = sum(values[:period]) / period
    ema_vals[period - 1] = sma

    for i in range(period, len(values)):
        ema_vals[i] = values[i] * k + ema_vals[i - 1] * (1.0 - k)

    return ema_vals


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Relative Strength Index using Wilder's smoothing.
    Returns list of same length. The first period elements are None.
    """
    if len(values) < period + 1:
        return [None] * len(values)

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(0.0, d) for d in deltas]
    losses = [max(0.0, -d) for d in deltas]

    rsi_vals: List[Optional[float]] = [None] * len(values)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0.0:
        rsi_vals[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_vals[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0.0:
            rsi_vals[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_vals[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi_vals


def atr(high: List[float], low: List[float], close: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Average True Range using Wilder's smoothing.
    Returns list of same length. The first (period - 1) elements are None.
    """
    if len(close) < period + 1:
        return [None] * len(close)

    tr = []
    for i in range(len(close)):
        if i == 0:
            tr.append(high[i] - low[i])
        else:
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))

    atr_vals: List[Optional[float]] = [None] * len(close)
    atr_vals[period - 1] = sum(tr[:period]) / period

    for i in range(period, len(close)):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period

    return atr_vals


def volume_zscore(volumes: List[float], baseline_count: int = 180) -> Optional[float]:
    """
    Calculates Volume Z-score vs 30-day baseline.
    For 4h candles: 30 days = 30 * 6 = 180 candles.
    """
    if len(volumes) < baseline_count or baseline_count < 2:
        return None

    baseline = volumes[-baseline_count:]
    mean = sum(baseline) / len(baseline)
    var = sum((v - mean) ** 2 for v in baseline) / len(baseline)
    std = math.sqrt(var) if var > 0.0 else 1.0

    if std == 0.0:
        return 0.0
    return round((volumes[-1] - mean) / std, 2)


def cvd_24h(candles: List[Dict[str, Any]], hours: int = 24) -> Tuple[Optional[float], str]:
    """
    Calculates Cumulative Volume Delta over 24h using candle intrabar delta approximation.
    Delta = (2 * (Close - Low) / (High - Low) - 1) * Volume.
    Returns (cvd_val, cvd_trend).
    """
    if not candles:
        return None, "unknown"

    # Last 24 hours (for 4h candles = 6 candles; for 1h candles = 24 candles)
    recent = candles[-hours:] if len(candles) >= hours else candles
    total_delta = 0.0
    total_vol = 0.0

    for c in recent:
        h = c["high"]
        l = c["low"]
        close_p = c["close"]
        v = c["volume"]
        total_vol += v
        if h > l:
            delta_ratio = 2.0 * ((close_p - l) / (h - l)) - 1.0
            total_delta += delta_ratio * v
        else:
            total_delta += 0.0

    if total_vol == 0.0:
        return 0.0, "neutral"

    normalized_ratio = total_delta / total_vol
    if normalized_ratio > 0.15:
        trend = "accumulation"
    elif normalized_ratio < -0.15:
        trend = "distribution"
    else:
        trend = "neutral"

    return round(total_delta, 2), trend


def compute_features(candles: List[Dict[str, Any]], profile: str = "SWING") -> Dict[str, Any]:
    """
    Computes all Lean 5 indicator features from closed candles.
    """
    if not candles:
        return {
            "ema20": None, "ema50": None, "ema200": None,
            "rsi14": None, "rsi_dir": "unknown",
            "atr14": None, "atr_pct": None,
            "vol_z": None, "cvd_24h": None, "cvd_trend": "unknown",
            "swing_high": 0.0, "swing_low": 0.0
        }

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # EMAs
    ema20_series = ema(closes, 20)
    ema50_series = ema(closes, 50)
    ema200_series = ema(closes, 200)

    # RSI
    rsi_series = rsi(closes, 14)

    # ATR
    atr_series = atr(highs, lows, closes, 14)

    # Latest values
    ema20_val = ema20_series[-1] if ema20_series else None
    ema50_val = ema50_series[-1] if ema50_series else None
    ema200_val = ema200_series[-1] if ema200_series else None
    rsi14_val = rsi_series[-1] if rsi_series else None
    atr14_val = atr_series[-1] if atr_series else None

    # RSI direction
    if len(rsi_series) >= 2 and rsi_series[-1] is not None and rsi_series[-2] is not None:
        diff = rsi_series[-1] - rsi_series[-2]
        if diff > 1.0:
            rsi_dir = "rising"
        elif diff < -1.0:
            rsi_dir = "falling"
        else:
            rsi_dir = "flat"
    else:
        rsi_dir = "unknown"

    # ATR %
    atr_pct = round((atr14_val / closes[-1]) * 100.0, 2) if atr14_val and closes[-1] > 0 else None

    # Volume Z-score (180 4h candles = 30 days)
    baseline_candles = 180 if profile == "SWING" else 30
    vol_z = volume_zscore(volumes, baseline_count=min(baseline_candles, len(volumes)))

    # CVD 24h
    window_24h = 6 if profile == "SWING" else 24
    cvd_val, cvd_trend = cvd_24h(candles, hours=window_24h)

    # Recent swing high/low over last 20 candles
    recent_window = min(20, len(candles))
    swing_high = max(highs[-recent_window:]) if recent_window > 0 else closes[-1]
    swing_low = min(lows[-recent_window:]) if recent_window > 0 else closes[-1]

    return {
        "ema20": round(ema20_val, 2) if ema20_val else None,
        "ema50": round(ema50_val, 2) if ema50_val else None,
        "ema200": round(ema200_val, 2) if ema200_val else None,
        "rsi14": round(rsi14_val, 2) if rsi14_val else None,
        "rsi_dir": rsi_dir,
        "atr14": round(atr14_val, 2) if atr14_val else None,
        "atr_pct": atr_pct,
        "vol_z": vol_z,
        "cvd_24h": cvd_val,
        "cvd_trend": cvd_trend,
        "swing_high": swing_high,
        "swing_low": swing_low
    }


def compute_compact_state(
    features: Dict[str, Any],
    last_price: float,
    funding_rate_pct: Optional[float],
    social_meta: Dict[str, Any],
    profile: str,
    ts_utc: str
) -> Dict[str, Any]:
    """
    Transforms numerical features into compact categorical state strictly per v8_JSON_state_schema.json.
    """
    ema20 = features.get("ema20")
    ema50 = features.get("ema50")
    ema200 = features.get("ema200")
    rsi14 = features.get("rsi14")
    rsi_dir = features.get("rsi_dir", "unknown")
    vol_z = features.get("vol_z") or 0.0
    cvd_trend = features.get("cvd_trend", "neutral")

    # EMA Stack
    if ema20 and ema50 and ema200:
        if ema20 > ema50 > ema200:
            ema_stack = "bullish"
        elif ema20 < ema50 < ema200:
            ema_stack = "bearish"
        elif abs(ema20 - ema50) / ema50 < 0.005:
            ema_stack = "neutral"
        else:
            ema_stack = "mixed"
    else:
        ema_stack = "neutral"

    # EMA200 position
    if ema200:
        ema200_pos = "above" if last_price >= ema200 else "below"
    else:
        ema200_pos = "unknown"

    # RSI category
    if rsi14 is None:
        rsi_cat = "unknown"
    elif rsi14 < 30.0:
        rsi_cat = "oversold"
    elif rsi14 < 45.0:
        rsi_cat = "neutral_low"
    elif rsi14 <= 55.0:
        rsi_cat = "neutral"
    elif rsi14 <= 70.0:
        rsi_cat = "neutral_high"
    else:
        rsi_cat = "overbought"

    # Volume regime
    if vol_z < -1.0:
        vol_regime = "low"
    elif vol_z > 1.5:
        vol_regime = "high"
    else:
        vol_regime = "normal"

    # Derivatives funding category
    if funding_rate_pct is None:
        funding_cat = "unavailable"
    elif funding_rate_pct < -0.002:
        funding_cat = "negative"
    elif funding_rate_pct > 0.015:
        funding_cat = "positive"
    else:
        funding_cat = "neutral"

    return {
        "profile": profile,
        "rule_ver": "v8.0",
        "ts_utc": ts_utc,
        "market_cat": {
            "ema_stack": ema_stack,
            "ema200_pos": ema200_pos,
            "rsi_cat": rsi_cat,
            "rsi_dir": rsi_dir,
            "vol_regime": vol_regime,
            "vol_z": float(vol_z),
            "cvd_trend": cvd_trend
        },
        "derivatives_cat": {
            "funding_cat": funding_cat,
            "oi_change_cat": "flat"  # Categorical default
        },
        "social_meta": social_meta
    }


def compute_pillars(market_cat: Dict[str, Any], derivatives_cat: Dict[str, Any], social_meta: Dict[str, Any]) -> Dict[str, str]:
    """
    Evaluates 3 pillars: technicals, derivatives, social.
    Each returns: 'bullish' | 'bearish' | 'neutral' | 'weak' | 'unavailable'.
    """
    # Technicals pillar
    ema_s = market_cat.get("ema_stack")
    rsi_c = market_cat.get("rsi_cat")
    if ema_s == "bullish" and rsi_c in ["oversold", "neutral_low", "neutral"]:
        tech = "bullish"
    elif ema_s == "bearish" and rsi_c in ["overbought", "neutral_high", "neutral"]:
        tech = "bearish"
    elif ema_s == "neutral" or rsi_c == "neutral":
        tech = "neutral"
    else:
        tech = "weak"

    # Derivatives pillar
    funding = derivatives_cat.get("funding_cat")
    if funding == "negative":
        deriv = "bullish"  # Squeeze context, descriptive only
    elif funding == "positive":
        deriv = "bearish"
    elif funding == "neutral":
        deriv = "neutral"
    else:
        deriv = "unavailable"

    # Social pillar
    pol = social_meta.get("polarity_cat")
    if pol in ["extreme_fear", "fear"]:
        soc = "bullish"  # Contrarian absorption context
    elif pol in ["euphoria", "greed"]:
        soc = "bearish"
    elif pol == "neutral":
        soc = "neutral"
    else:
        soc = "unavailable"

    return {"technicals": tech, "derivatives": deriv, "social": soc}


def resolve_final_verdict(pillars: Dict[str, str], data_gates_passed: bool, shadow_mode: bool = True) -> Tuple[str, List[str]]:
    """
    Deterministic final verdict resolver.
    Default stance: ABSTAIN on conflict, unless 2 strongly agree and 3rd is neutral/weak.
    """
    if not data_gates_passed:
        return "NO_VERDICT", ["Data gates failed"]

    conflicts = []
    vals = [pillars["technicals"], pillars["derivatives"], pillars["social"]]
    effective = [v for v in vals if v != "unavailable"]

    if len(effective) < 2:
        return "NO_VERDICT", ["Insufficient pillars available"]

    bull = sum(1 for v in effective if v == "bullish")
    bear = sum(1 for v in effective if v == "bearish")

    if bull >= 2 and bear == 0:
        return "BUY", []
    if bear >= 2 and bull == 0:
        return "SELL", []
    if bull > 0 and bear > 0:
        conflicts.append(f"Conflicts detected: Technicals {pillars['technicals']} vs Derivatives {pillars['derivatives']} vs Social {pillars['social']}")
        return "ABSTAIN", conflicts

    # Low confluence
    return "ABSTAIN", ["Low confluence across pillars"]


def compute_levels(
    entry_price: float,
    atr14: Optional[float],
    swing_high: float,
    swing_low: float,
    profile: str = "SWING"
) -> Dict[str, Any]:
    """
    Structure + ATR decision levels.
    SWING: 2.5x ATR stops beyond structure, 1.5R primary target, 3R extended target.
    DAY_CONTEXT: 1.5x ATR stops, strictly NO targets beyond session.
    Copy constraint (AC-08): strictly one number per field with label.
    """
    if atr14 is None or atr14 <= 0 or entry_price <= 0:
        return {"has_valid_levels": False}

    if profile == "SWING":
        atr_mult_stop = 2.5
        stop_price = entry_price - atr_mult_stop * atr14 if entry_price > swing_low else entry_price + atr_mult_stop * atr14
        risk = abs(entry_price - stop_price)
        target_primary = entry_price + 1.5 * risk
        target_extended = entry_price + 3.0 * risk

        # Structure sanity check: stop must not be inside structure
        if swing_low < stop_price < swing_high:
            return {"has_valid_levels": False}

        stop_pct = round((abs(entry_price - stop_price) / entry_price) * 100.0, 2)
        return {
            "has_valid_levels": True,
            "entry_zone": [round(entry_price * 0.998, 1), round(entry_price * 1.002, 1)],
            "stop": {
                "price": round(stop_price, 1),
                "pct": stop_pct,
                "atr_multiple": atr_mult_stop
            },
            "target_primary": round(target_primary, 1),
            "target_extended": round(target_extended, 1),
            "method": "structure+ATR 2.5x",
            "expiry_ts": None,
            "invalidation": f"4h close < {round(swing_low, 1)}",
            "warning": "No order placed. Not an active stop. App does not monitor or execute."
        }
    else:  # DAY_CONTEXT
        atr_mult_stop = 1.5
        stop_price = entry_price - atr_mult_stop * atr14
        stop_pct = round((abs(entry_price - stop_price) / entry_price) * 100.0, 2)
        return {
            "has_valid_levels": True,
            "entry_zone": [round(entry_price * 0.999, 1), round(entry_price * 1.001, 1)],
            "stop": {
                "price": round(stop_price, 1),
                "pct": stop_pct,
                "atr_multiple": atr_mult_stop
            },
            "target_primary": None,
            "target_extended": None,
            "method": "ATR 1.5x intraday context only",
            "expiry_ts": None,
            "invalidation": f"15m close < {round(stop_price, 1)}",
            "warning": "No order placed. Intraday context only."
        }
