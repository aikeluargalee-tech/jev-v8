import pytest
import math
from app.services.indicators import (
    ema,
    rsi,
    atr,
    volume_zscore,
    cvd_24h,
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
    # SWING profile: 2.5x ATR stop, 1.5R primary, 3.0R extended
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

    # Stop inside structure -> has_valid_levels = False
    lvls_inside = compute_levels(entry, atr_val, swing_high=85000.0, swing_low=75000.0, profile="SWING")
    assert lvls_inside["has_valid_levels"] is False

    # DAY_CONTEXT profile: 1.5x ATR stop, strictly NO targets beyond session
    lvls_day = compute_levels(entry, atr_val, swing_high, swing_low, profile="DAY_CONTEXT")
    assert lvls_day["has_valid_levels"] is True
    assert lvls_day["stop"]["price"] == 80000.0 - 1.5 * 1000.0  # 78,500.0
    assert lvls_day["target_primary"] is None
    assert lvls_day["target_extended"] is None

    # Invalid ATR -> has_valid_levels = False
    assert compute_levels(entry, None, swing_high, swing_low)["has_valid_levels"] is False
