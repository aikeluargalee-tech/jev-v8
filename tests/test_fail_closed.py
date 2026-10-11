import datetime
import pytest
import time
from app.services.fail_closed import (
    MarketSnapshot,
    check_fail_closed,
    check_jev_fallback,
    check_cost_guardrail,
    WARMUP,
    STALENESS_THRESH
)


def make_valid_snapshot(profile: str = "SWING") -> MarketSnapshot:
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    req = WARMUP[profile]
    return MarketSnapshot(
        venue="kraken",
        pair="BTC/USD",
        last_price=82500.0,
        last_candle_close_ts=now_dt,
        freshness_market_s=5.0,
        freshness_funding_s=10.0,
        is_fallback=False,
        is_synthetic=False,
        candle_count_15m=req["15m"] + 10 if req["15m"] > 0 else 0,
        candle_count_1h=req["1h"] + 10 if req["1h"] > 0 else 0,
        candle_count_4h=req["4h"] + 10 if req["4h"] > 0 else 0,
        candle_count_1d=req["1D"] + 10 if req["1D"] > 0 else 0,
        profile=profile
    )


def test_valid_snapshot_passes():
    snap = make_valid_snapshot("SWING")
    passed, fails = check_fail_closed(snap)
    assert passed is True
    assert len(fails) == 0


def test_case_a_synthetic_and_fallback_fail_closed():
    """Test Case A: is_fallback=True or is_synthetic=True triggers DEMO DATA — NOT LIVE banner in <2s."""
    t0 = time.perf_counter()
    snap = make_valid_snapshot("SWING")
    snap.is_fallback = True
    passed, fails = check_fail_closed(snap)
    t1 = time.perf_counter()

    assert passed is False
    assert any("DEMO DATA — NOT LIVE" in f for f in fails)
    assert (t1 - t0) < 2.0  # AC-02: must resolve in <2s

    snap2 = make_valid_snapshot("SWING")
    snap2.is_synthetic = True
    passed2, fails2 = check_fail_closed(snap2)
    assert passed2 is False
    assert any("DEMO DATA — NOT LIVE" in f for f in fails2)


def test_case_b_staleness_triggers_fail_closed():
    """Test Case B: Market or funding staleness triggers fail-closed."""
    # SWING market threshold is 300s
    snap = make_valid_snapshot("SWING")
    snap.freshness_market_s = 301.0
    passed, fails = check_fail_closed(snap)
    assert passed is False
    assert any("Market stale" in f for f in fails)

    # SWING funding threshold is 300s
    snap_funding = make_valid_snapshot("SWING")
    snap_funding.freshness_funding_s = 305.0
    passed_f, fails_f = check_fail_closed(snap_funding)
    assert passed_f is False
    assert any("Funding stale" in f for f in fails_f)

    # DAY_CONTEXT market threshold is 60s
    snap_day = make_valid_snapshot("DAY_CONTEXT")
    snap_day.freshness_market_s = 61.0
    passed_d, fails_d = check_fail_closed(snap_day)
    assert passed_d is False
    assert any("Market stale" in f for f in fails_d)


def test_case_c_insufficient_warmup_triggers_fail_closed():
    """Test Case C: Missing required history candles triggers NO_VERDICT."""
    snap = make_valid_snapshot("SWING")
    # Needs 300x 4h candles
    snap.candle_count_4h = 299
    passed, fails = check_fail_closed(snap)
    assert passed is False
    assert any("Insufficient history 4h" in f for f in fails)

    # Needs 250x 1D candles
    snap_1d = make_valid_snapshot("SWING")
    snap_1d.candle_count_1d = 249
    passed_1d, fails_1d = check_fail_closed(snap_1d)
    assert passed_1d is False
    assert any("Insufficient history 1D" in f for f in fails_1d)

    # DAY_CONTEXT needs 300x 15m candles
    snap_day = make_valid_snapshot("DAY_CONTEXT")
    snap_day.candle_count_15m = 299
    passed_day, fails_day = check_fail_closed(snap_day)
    assert passed_day is False
    assert any("Insufficient history 15m" in f for f in fails_day)


def test_case_d_price_sanity():
    """Test Case D: Implausible price triggers fail-closed."""
    snap_zero = make_valid_snapshot("SWING")
    snap_zero.last_price = 0.0
    passed_z, fails_z = check_fail_closed(snap_zero)
    assert passed_z is False
    assert any("Implausible price" in f for f in fails_z)

    snap_huge = make_valid_snapshot("SWING")
    snap_huge.last_price = 2_500_000.0
    passed_h, fails_h = check_fail_closed(snap_huge)
    assert passed_h is False
    assert any("Implausible price" in f for f in fails_h)


def test_jev_fallback_detector():
    """Test Case E: Jev deterministic fallback is caught and rejected."""
    # Explicit fallback flag
    passed, reason = check_jev_fallback({"is_fallback": True})
    assert passed is False
    assert "Model Unavailable" in reason

    # Pseudo-precision fallback
    passed2, reason2 = check_jev_fallback({
        "TradeAction": {"confidence": 0.8},
        "model_ver": "fallback-v1"
    })
    assert passed2 is False
    assert "fixed fallback value" in reason2

    # Live mock adapter passes
    from app.services.mock_jev import mock_jev_adapter
    mock_res = mock_jev_adapter({})
    passed_mock, _ = check_jev_fallback(mock_res)
    assert passed_mock is True


def test_cost_guardrail():
    """Test Case F: Budget alerts at 80% and blocks at 100%."""
    budget = 20.0

    # Under 80%: no banner, no block
    banner, block = check_cost_guardrail(15.0, budget)
    assert banner == ""
    assert block is False

    # At 80% ($16.00): warning banner, no block
    banner80, block80 = check_cost_guardrail(16.0, budget)
    assert "Cost warning 80%" in banner80
    assert block80 is False

    # At 100% ($20.00): cap reached, paid calls blocked
    banner100, block100 = check_cost_guardrail(20.0, budget)
    assert "Cost cap reached" in banner100
    assert block100 is True
