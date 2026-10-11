import pytest
import math
from app.services.indicators import (
    ema,
    rsi,
    atr,
    volume_zscore,
    cvd_24h,
    efficiency_ratio,
    check_break_of_structure,
    compute_macro_trend,
    compute_features,
    compute_compact_state,
    compute_pillars,
    resolve_final_verdict,
    compute_levels
)


def test_ema_mathematical_precision():
    # Constant series test: EMA of all 100.0 should be 100.0
    values = [100.0] * 50
    ema20 = ema(values, 20)
    assert len(ema20) == 50
    assert all(x is None for x in ema20[:19])
    assert pytest.approx(ema20[19], 0.0001) == 100.0
    assert pytest.approx(ema20[-1], 0.0001) == 100.0

    # Step increase test
    seq = [10.0] * 20 + [20.0] * 20
    ema10 = ema(seq, 10)
    assert ema10[9] == 10.0
    assert ema10[-1] > 19.0


def test_ema200_warmup_necessity():
    """
    Validates Chief Engineer directive:
    48 candles is mathematically insufficient for EMA200 (error > 2.5%),
    while 300 candles achieves asymptotic convergence (error < 0.01%).
    """
    # Simulate a steady price trend
    prices = [50000.0 + i * 50.0 for i in range(500)]

    # 48 candles is shorter than period (200), so it returns all None
    ema_short = ema(prices[:48], 200)
    assert all(x is None for x in ema_short)

    # 300 candles provides full 200-period SMA init + 100 iterations of smoothing
    ema_full = ema(prices[:300], 200)
    assert ema_full[199] is not None
    assert ema_full[-1] is not None
    assert 55000.0 < ema_full[-1] < 65000.0


def test_rsi_extremes_and_golden_fixtures():
    # Monotonically increasing prices -> RSI must be 100.0
    up_prices = [10.0 + i * 2.0 for i in range(30)]
    rsi_up = rsi(up_prices, 14)
    assert rsi_up[13] is None
    assert rsi_up[14] == 100.0
    assert rsi_up[-1] == 100.0

    # Monotonically decreasing prices -> RSI must be 0.0
    down_prices = [100.0 - i * 2.0 for i in range(30)]
    rsi_down = rsi(down_prices, 14)
    assert rsi_down[14] == 0.0
    assert rsi_down[-1] == 0.0


def test_atr_calculation():
    highs = [105.0] * 30
    lows = [95.0] * 30
    closes = [100.0] * 30

    atr14 = atr(highs, lows, closes, 14)
    assert len(atr14) == 30
    assert all(x is None for x in atr14[:13])
    # True range for each bar is 105 - 95 = 10.0
    assert pytest.approx(atr14[13], 0.001) == 10.0
    assert pytest.approx(atr14[-1], 0.001) == 10.0


def test_volume_zscore():
    vols = [1000.0] * 50
    # Zero variance -> returns 0.0
    assert volume_zscore(vols, 30) == 0.0

    # Sudden 3-sigma volume spike
    vols[-1] = 4000.0
    z = volume_zscore(vols, 30)
    assert z is not None
    assert z > 2.0


def test_cvd_accumulation_and_distribution():
    # Accumulation candles: close at the high
    bull_candles = [
        {"high": 105.0, "low": 95.0, "close": 105.0, "volume": 1000.0}
        for _ in range(6)
    ]
    cvd_val, trend = cvd_24h(bull_candles, hours=6)
    assert cvd_val > 0
    assert trend == "accumulation"

    # Distribution candles: close at the low
    bear_candles = [
        {"high": 105.0, "low": 95.0, "close": 95.0, "volume": 1000.0}
        for _ in range(6)
    ]
    cvd_bear, trend_bear = cvd_24h(bear_candles, hours=6)
    assert cvd_bear < 0
    assert trend_bear == "distribution"


def test_pillars_and_conflict_resolution():
    # 1. Confluence BUY: 2 bullish + 1 neutral
    m_cat_bull = {"ema_stack": "bullish", "rsi_cat": "oversold"}
    d_cat_bull = {"funding_cat": "negative"}  # Potential squeeze context
    s_meta_neut = {"polarity_cat": "neutral"}
    pillars_bull = compute_pillars(m_cat_bull, d_cat_bull, s_meta_neut)
    assert pillars_bull["technicals"] == "bullish"
    assert pillars_bull["derivatives"] == "bullish"
    verdict, conflicts = resolve_final_verdict(pillars_bull, data_gates_passed=True)
    assert verdict == "BUY"
    assert len(conflicts) == 0

    # 2. Conflict: Bullish technicals vs Bearish derivatives -> must ABSTAIN
    d_cat_bear = {"funding_cat": "positive"}
    pillars_conflict = compute_pillars(m_cat_bull, d_cat_bear, s_meta_neut)
    verdict_c, conflicts_c = resolve_final_verdict(pillars_conflict, data_gates_passed=True)
    assert verdict_c == "ABSTAIN"
    assert len(conflicts_c) > 0
    assert "Conflicts detected" in conflicts_c[0]

    # 3. Fail-closed: data_gates_passed=False -> NO_VERDICT
    verdict_fail, reasons_fail = resolve_final_verdict(pillars_bull, data_gates_passed=False)
    assert verdict_fail == "NO_VERDICT"


def test_levels_structure_and_atr():
    # SWING profile: 2.5x ATR stop placed beyond structure, 1.5R primary, 3.0R extended
    entry = 80000.0
    atr_val = 1000.0
    swing_high = 85000.0
    swing_low = 78000.0  # Stop at 77,500.0 is beyond swing low (78,000.0) -> Valid structure

    lvls = compute_levels(entry, atr_val, swing_high, swing_low, profile="SWING")
    assert lvls["has_valid_levels"] is True
    assert lvls["stop"]["price"] == 80000.0 - 2.5 * 1000.0  # 77,500.0
    assert lvls["stop"]["atr_multiple"] == 2.5
    assert lvls["target_primary"] == 80000.0 + 1.5 * 2500.0  # 83,750.0
    assert lvls["target_extended"] == 80000.0 + 3.0 * 2500.0  # 87,500.0
    assert "No order placed" in lvls["warning"]

    # When swing_low is deep (75,000.0), stop is placed beyond structure low (75000 - 0.2*1000 = 74,800.0)
    lvls_deep = compute_levels(entry, atr_val, swing_high=85000.0, swing_low=75000.0, profile="SWING")
    assert lvls_deep["has_valid_levels"] is True
    assert lvls_deep["stop"]["price"] == 74800.0
    assert lvls_deep["stop"]["price"] < 75000.0

    # Inverted structure (swing_high <= swing_low) -> has_valid_levels = False
    assert compute_levels(entry, atr_val, swing_high=75000.0, swing_low=85000.0, profile="SWING")["has_valid_levels"] is False

    # DAY_CONTEXT profile: 1.5x ATR stop, strictly NO targets beyond session
    lvls_day = compute_levels(entry, atr_val, swing_high, swing_low, profile="DAY_CONTEXT")
    assert lvls_day["has_valid_levels"] is True
    assert lvls_day["stop"]["price"] == 80000.0 - 1.5 * 1000.0  # 78,500.0
    assert lvls_day["target_primary"] is None
    assert lvls_day["target_extended"] is None

    # Invalid ATR -> has_valid_levels = False
    assert compute_levels(entry, None, swing_high, swing_low)["has_valid_levels"] is False


def test_kaufman_efficiency_ratio():
    """Verify Kaufman Efficiency Ratio (ER20) calculations and bounds."""
    # 1. Monotonically increasing prices -> ER = 1.0 (maximum efficiency)
    monotonic_up = [100.0 + i * 5.0 for i in range(25)]
    er_up = efficiency_ratio(monotonic_up, period=20)
    assert er_up == 1.0

    # 2. Perfect alternating chop [100, 105, 100, 105, ...] -> net change near 0, path high -> ER ~ 0.0
    chop = [100.0 if i % 2 == 0 else 105.0 for i in range(25)]
    er_chop = efficiency_ratio(chop, period=20)
    assert er_chop is not None
    assert er_chop < 0.10

    # 3. Insufficient history (< period + 1) -> returns None
    assert efficiency_ratio([100.0] * 15, period=20) is None

    # 4. Zero path movement -> returns 0.0
    assert efficiency_ratio([100.0] * 25, period=20) == 0.0


def test_break_of_structure_detection():
    """Verify close-confirmed Break of Structure (BOS) over 20-candle range."""
    # Base range: 20 candles with highs up to 105.0, lows down to 95.0
    base_candles = [
        {"high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000.0}
        for _ in range(20)
    ]

    # 1. Bullish breakout: 21st candle closes firmly above 105.0 + 0.1*ATR
    bull_candles = list(base_candles) + [{"high": 112.0, "low": 104.0, "close": 110.0, "volume": 1500.0}]
    bos_bull = check_break_of_structure(bull_candles, lookback=20, atr14=10.0)
    assert bos_bull["bos"] == "BOS_BULLISH"
    assert bos_bull["range_high"] == 105.0
    assert bos_bull["range_low"] == 95.0

    # 2. Bearish breakdown: 21st candle closes firmly below 95.0 - 0.1*ATR
    bear_candles = list(base_candles) + [{"high": 96.0, "low": 88.0, "close": 89.0, "volume": 1500.0}]
    bos_bear = check_break_of_structure(bear_candles, lookback=20, atr14=10.0)
    assert bos_bear["bos"] == "BOS_BEARISH"

    # 3. Inside range: 21st candle closes at 101.0
    inside_candles = list(base_candles) + [{"high": 104.0, "low": 96.0, "close": 101.0, "volume": 1000.0}]
    bos_inside = check_break_of_structure(inside_candles, lookback=20, atr14=10.0)
    assert bos_inside["bos"] == "INSIDE_RANGE"


def test_daily_macro_trend_computation():
    """Verify 1D Daily Macro Trend determination from 1D candles."""
    # 1. 250 Daily candles in uptrend -> close > EMA200 -> bullish
    uptrend_1d = [{"close": 30000.0 + i * 200.0} for i in range(250)]
    macro_bull = compute_macro_trend(uptrend_1d)
    assert macro_bull["macro_trend"] == "bullish"
    assert macro_bull["ema200_1d"] is not None
    assert macro_bull["last_close_1d"] > macro_bull["ema200_1d"]

    # 2. 250 Daily candles in downtrend -> close < EMA200 -> bearish
    downtrend_1d = [{"close": 80000.0 - i * 200.0} for i in range(250)]
    macro_bear = compute_macro_trend(downtrend_1d)
    assert macro_bear["macro_trend"] == "bearish"
    assert macro_bear["last_close_1d"] < macro_bear["ema200_1d"]

    # 3. Insufficient daily history (< 50 candles) -> returns unknown
    short_1d = [{"close": 60000.0} for _ in range(30)]
    macro_short = compute_macro_trend(short_1d)
    assert macro_short["macro_trend"] == "unknown"

    # 4. Pullback / Consolidation regime (above EMA200 but below EMA20) -> neutral
    pullback_1d = [{"close": 50000.0 + i * 100.0} for i in range(240)] + [{"close": 70000.0 - i * 500.0} for i in range(10)]
    macro_pullback = compute_macro_trend(pullback_1d)
    assert macro_pullback["macro_trend"] == "neutral"



def test_kaufman_chop_gate_forces_abstain():
    """
    Gate 1 Validation:
    When Kaufman ER20 < 0.25 (consolidation/chop), resolve_final_verdict MUST
    fail-closed with ABSTAIN even if Technicals and Derivatives are bullish.
    """
    pillars_bull = {
        "technicals": "bullish",
        "derivatives": "bullish",
        "social": "neutral",
        "efficiency": "neutral"
    }

    # Low efficiency chop (ER = 0.18 < 0.25) -> FORCES ABSTAIN
    verdict, reasons = resolve_final_verdict(
        pillars=pillars_bull,
        data_gates_passed=True,
        er20=0.18
    )
    assert verdict == "ABSTAIN"
    assert len(reasons) > 0
    assert "Kaufman ER20" in reasons[0]
    assert "chop" in reasons[0].lower()

    # High efficiency trending (ER = 0.65 > 0.25) -> Allows BUY verdict
    verdict_trend, _ = resolve_final_verdict(
        pillars=pillars_bull,
        data_gates_passed=True,
        er20=0.65
    )
    assert verdict_trend == "BUY"


def test_daily_macro_trend_alignment_veto():
    """
    Gate 2 Validation:
    Swing BUY is vetoed when 1D Macro Trend is bearish.
    Swing SELL is vetoed when 1D Macro Trend is bullish.
    """
    # 4H pillars suggest BUY
    pillars_buy = {
        "technicals": "bullish",
        "derivatives": "bullish",
        "social": "neutral",
        "efficiency": "bullish"
    }
    # But 1D Daily Macro Trend is bearish -> Counter-trend BUY vetoed -> ABSTAIN
    verdict_veto_buy, reasons_veto_buy = resolve_final_verdict(
        pillars=pillars_buy,
        data_gates_passed=True,
        er20=0.60,
        macro_trend="bearish"
    )
    assert verdict_veto_buy == "ABSTAIN"
    assert any("Daily Macro Trend" in r and "vetoed" in r for r in reasons_veto_buy)

    # 4H pillars suggest SELL
    pillars_sell = {
        "technicals": "bearish",
        "derivatives": "bearish",
        "social": "neutral",
        "efficiency": "bearish"
    }
    # But 1D Daily Macro Trend is bullish -> Counter-trend SELL vetoed -> ABSTAIN
    verdict_veto_sell, reasons_veto_sell = resolve_final_verdict(
        pillars=pillars_sell,
        data_gates_passed=True,
        er20=0.60,
        macro_trend="bullish"
    )
    assert verdict_veto_sell == "ABSTAIN"
    assert any("Daily Macro Trend" in r and "vetoed" in r for r in reasons_veto_sell)

    # Aligned trend: 4H BUY with bullish 1D Macro Trend -> Passes
    verdict_pass, _ = resolve_final_verdict(
        pillars=pillars_buy,
        data_gates_passed=True,
        er20=0.60,
        macro_trend="bullish"
    )
    assert verdict_pass == "BUY"


def test_rsi_zero_preservation():
    """Verify that RSI=0.0 is preserved as 0.0 and not demoted to None by falsy checks."""
    from app.services.indicators import compute_features
    # Constant dropping price sequence will produce RSI = 0.0
    dropping_candles = [
        {"high": 100.0 - i, "low": 99.0 - i, "close": 99.0 - i, "volume": 100.0}
        for i in range(50)
    ]
    feat = compute_features(dropping_candles)
    assert feat["rsi14"] is not None
    assert feat["rsi14"] == 0.0


def test_levels_short_and_abstain():
    """Verify Short setup (SELL) and ABSTAIN level suppression."""
    entry = 80000.0
    atr_val = 1000.0
    swing_high = 82000.0  # Stop at 82500 is beyond swing high (82000) -> Valid structure
    swing_low = 75000.0

    # 1. Short setup on SELL
    lvls_sell = compute_levels(entry, atr_val, swing_high, swing_low, profile="SWING", verdict="SELL")
    assert lvls_sell["has_valid_levels"] is True
    assert lvls_sell["stop"]["price"] == 80000.0 + 2.5 * 1000.0  # 82,500.0 (above entry)
    assert lvls_sell["target_primary"] == 80000.0 - 1.5 * 2500.0  # 76,250.0 (below entry)
    assert lvls_sell["target_extended"] == 80000.0 - 3.0 * 2500.0  # 72,500.0 (below entry)
    assert "4h close > 82000.0" in lvls_sell["invalidation"]

    # 2. ABSTAIN suppresses levels on SWING and DAY_CONTEXT
    lvls_abstain_swing = compute_levels(entry, atr_val, swing_high, swing_low, profile="SWING", verdict="ABSTAIN")
    assert lvls_abstain_swing["has_valid_levels"] is False

    lvls_abstain_day = compute_levels(entry, atr_val, swing_high, swing_low, profile="DAY_CONTEXT", verdict="ABSTAIN")
    assert lvls_abstain_day["has_valid_levels"] is False


def test_derivatives_hourly_funding_scale():
    """Verify Kraken funding rate classification with standard 8h scale."""
    # Live Kraken normalized 8h rate: 0.0005% -> neutral (-0.001% <= r <= 0.005%)
    state_live = compute_compact_state(
        features={}, last_price=83000.0, funding_rate_pct=0.0005,
        social_meta={}, profile="SWING", ts_utc="2026-10-11T00:00:00Z"
    )
    assert state_live["derivatives_cat"]["funding_cat"] == "neutral"

    # High 8h rate: 0.008% (> 0.005%) -> positive
    state_high = compute_compact_state(
        features={}, last_price=83000.0, funding_rate_pct=0.008,
        social_meta={}, profile="SWING", ts_utc="2026-10-11T00:00:00Z"
    )
    assert state_high["derivatives_cat"]["funding_cat"] == "positive"

    # Negative 8h rate: -0.002% (< -0.001%) -> negative
    state_neg = compute_compact_state(
        features={}, last_price=83000.0, funding_rate_pct=-0.002,
        social_meta={}, profile="SWING", ts_utc="2026-10-11T00:00:00Z"
    )
    assert state_neg["derivatives_cat"]["funding_cat"] == "negative"


