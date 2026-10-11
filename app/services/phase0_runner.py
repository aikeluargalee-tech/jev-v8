import uuid
import datetime
import time
from typing import Dict, Any, Tuple, List, Optional

from app.core.config import settings
from app.core.verdicts_db import verdicts_db
from app.services.fail_closed import (
    MarketSnapshot,
    check_fail_closed,
    check_jev_fallback,
    check_cost_guardrail,
    RULE_VER
)
from app.services.mock_jev import mock_jev_adapter, state_hash
from app.services.market_service import market_service
from app.services.indicators import (
    compute_features,
    compute_compact_state,
    compute_pillars,
    resolve_final_verdict,
    compute_levels,
    compute_macro_trend
)


async def run_v8_evaluation(
    symbol: str = "BTC",
    profile: str = "SWING",
    force_shadow: Optional[bool] = None
) -> Tuple[Dict[str, Any], float]:
    """
    Executes a complete v8 decision-support evaluation:
    1. Fetches live closed candles & funding from Kraken.
    2. Runs fail-closed checks (stale, synthetic, fallback, warmup, price sanity).
    3. Computes Lean 5 indicators (EMA20/50/200, RSI14, ATR14+ATR%, Volume Z 30d, CVD 24h).
    4. Transforms to compact categorical state per v8_JSON_state_schema.json.
    5. Computes 3 pillars (Technicals, Derivatives, Social) & surfaces conflicts.
    6. Resolves deterministic final verdict (default ABSTAIN on conflict).
    7. Computes Structure + ATR levels (2.5x ATR SWING / 1.5x DAY_CONTEXT).
    8. Enforces 60-verdict shadow mode.
    9. Logs immutable row to verdicts.db.
    Returns (verdict_record, latency_seconds).
    """
    t_start = time.perf_counter()
    run_id = str(uuid.uuid4())
    ts_now_utc = datetime.datetime.now(datetime.timezone.utc)
    ts_utc_str = ts_now_utc.isoformat()

    # Local MYT time (UTC+8)
    myt_tz = datetime.timezone(datetime.timedelta(hours=8))
    ts_local_myt_str = ts_now_utc.astimezone(myt_tz).strftime("%Y-%m-%d %H:%M:%S MYT")

    # 1. Fetch live market snapshot
    snap, market_raw = await market_service.fetch_market_snapshot(symbol=symbol, profile=profile)

    # 2. Run fail-closed checks
    data_gates_passed, fail_reasons = check_fail_closed(snap)

    # 3. Check monthly cumulative cost guardrail
    current_monthly_cost = verdicts_db.get_monthly_cumulative_cost(ts_utc_str[:7])
    cost_banner, block_paid = check_cost_guardrail(current_monthly_cost, settings.MONTHLY_BUDGET_USD)
    if block_paid:
        data_gates_passed = False
        fail_reasons.append(cost_banner)

    # 4. Shadow mode check (First 60 verdicts mandatory shadow mode)
    total_verdicts_so_far = verdicts_db.get_verdict_count()
    if force_shadow is not None:
        shadow_mode = force_shadow
    else:
        shadow_mode = total_verdicts_so_far < settings.SHADOW_MODE_MIN_VERDICTS

    # 5. Compute Lean 5 Indicators from closed candles
    primary_candles = market_raw.get("candles_4h" if profile == "SWING" else "candles_15m") or []
    features = compute_features(primary_candles, profile=profile)

    # 6. Build social meta (Phase 0-1 summary default)
    social_meta = {
        "provider": "twitterapi.io",
        "query": "$BTC OR Bitcoin",
        "query_ver": "v1",
        "count_requested": 100,
        "count_retrieved": 0,
        "diversity_ratio": 0.6,
        "polarity_cat": "neutral",
        "engagement_vel": "normal"
    }

    # 7. Build compact categorical state per v8_JSON_state_schema.json
    compact_state = compute_compact_state(
        features=features,
        last_price=snap.last_price,
        funding_rate_pct=market_raw.get("funding_rate_pct"),
        social_meta=social_meta,
        profile=profile,
        ts_utc=ts_utc_str
    )
    s_hash = state_hash(compact_state)

    # 8. Evaluate Jev (mock-v8 default, or real if ENABLE_JEV=True)
    from app.services.typesafe_service import typesafe_service
    jev_raw = await typesafe_service.evaluate_state(compact_state)
    jev_passed, jev_reason = check_jev_fallback(jev_raw)
    if not jev_passed:
        data_gates_passed = False
        fail_reasons.append(jev_reason)

    # 8.5 Compute Daily Macro Trend
    candles_1d = market_raw.get("candles_1d") or []
    macro_info = compute_macro_trend(candles_1d)

    # 9. Compute Pillars & Conflict Surfacing
    pillars = compute_pillars(compact_state["market_cat"], compact_state["derivatives_cat"], compact_state["social_meta"])
    det_verdict, det_conflicts = resolve_final_verdict(
        pillars=pillars,
        data_gates_passed=data_gates_passed,
        shadow_mode=shadow_mode,
        er20=features.get("er20"),
        macro_trend=macro_info.get("macro_trend")
    )

    # 10. Formulate final confidence & reasons
    if not data_gates_passed:
        final_verdict = "NO_VERDICT"
        confidence_band = None
        raw_conf = None
        reasons = fail_reasons
        degraded_flags = fail_reasons
    else:
        final_verdict = det_verdict
        trade_action = jev_raw.get("TradeAction", {})
        raw_conf = trade_action.get("confidence", 0.55)
        if raw_conf >= 0.70:
            confidence_band = "High"
        elif raw_conf >= 0.58:
            confidence_band = "Medium"
        else:
            confidence_band = "Low"
        reasons = det_conflicts if det_conflicts else [f"Pillars aligned: Technicals {pillars['technicals']}, Derivatives {pillars['derivatives']}, Social {pillars['social']}, Efficiency {pillars.get('efficiency', 'neutral')}"]
        degraded_flags = []

    # 11. Compute Structure + ATR Levels
    levels_data = compute_levels(
        entry_price=snap.last_price,
        atr14=features.get("atr14"),
        swing_high=features.get("swing_high", snap.last_price * 1.05),
        swing_low=features.get("swing_low", snap.last_price * 0.95),
        profile=profile
    )

    # 12. Construct complete v8_verdict_log record
    jev_cost = 0.01 if (getattr(typesafe_service, "enable_jev", False) and not jev_raw.get("is_fallback") and jev_raw.get("model_ver") != "mock-v8") else 0.0
    tweet_cost = 0.0
    cost_total = round(jev_cost + tweet_cost, 4)
    record: Dict[str, Any] = {
        "run_id": run_id,
        "ts_utc": ts_utc_str,
        "ts_local_myt": ts_local_myt_str,
        "profile": profile,
        "rule_ver": RULE_VER,
        "shadow_mode": shadow_mode,
        "market": {
            "venue": snap.venue,
            "pair": snap.pair,
            "last_price": snap.last_price,
            "last_candle_close_ts": snap.last_candle_close_ts.isoformat(),
            "freshness_market_s": snap.freshness_market_s,
            "freshness_funding_s": snap.freshness_funding_s,
            "is_fallback": snap.is_fallback,
            "is_synthetic": snap.is_synthetic
        },
        "features": {
            "ema20": features.get("ema20"),
            "ema50": features.get("ema50"),
            "ema200": features.get("ema200"),
            "rsi14": features.get("rsi14"),
            "atr14": features.get("atr14"),
            "atr_pct": features.get("atr_pct"),
            "vol_z": features.get("vol_z"),
            "cvd_24h": features.get("cvd_24h"),
            "er20": features.get("er20"),
            "er_regime": features.get("er_regime"),
            "bos": features.get("bos"),
            "range_high": features.get("range_high"),
            "range_low": features.get("range_low"),
            "macro_trend": macro_info.get("macro_trend"),
            "ema50_1d": macro_info.get("ema50_1d"),
            "ema200_1d": macro_info.get("ema200_1d")
        },
        "categorical": compact_state,
        "jev_raw": jev_raw,
        "deterministic": {
            "pillars": pillars,
            "conflicts": det_conflicts,
            "final_verdict": final_verdict,
            "final_confidence_band": confidence_band,
            "final_confidence_raw": raw_conf,
            "final_confidence_raw_label": "uncalibrated",
            "reasons": reasons,
            "data_gates_passed": data_gates_passed,
            "degraded_flags": degraded_flags
        },
        "levels": levels_data,
        "cost": {
            "tweet_cost_usd": tweet_cost,
            "jev_cost_usd": jev_cost,
            "total_cost_usd": cost_total,
            "monthly_cumulative_usd": round(current_monthly_cost + cost_total, 4)
        }
    }

    # 13. Immutable log to verdicts.db
    verdicts_db.log_verdict(record, s_hash)

    t_end = time.perf_counter()
    latency_s = t_end - t_start
    return record, latency_s


# Maintain backward compatibility alias
run_phase0_evaluation = run_v8_evaluation
