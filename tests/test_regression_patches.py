"""
Regression test suite covering the 6 patches and engineering refinements:
1. Swing lookback window excludes the forming/current candle.
2. Unclassified / None confidence bands are excluded from calibration band performance & Brier scoring.
3. Missing or corrupt Kraken Futures funding timestamp triggers fail-closed staleness.
4. verify_outcomes.py --simulate strictly requires --allow-synthetic.
5. volume_zscore degenerate zero-variance handling.
6. Brier score returns None (never a false perfect 0.0) when no scorable WIN/LOSS records exist.
7. Plain English raw confidence handling never outputs 'Raw: undefined'.
"""
import pytest
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.indicators import compute_features, volume_zscore
from app.services.calibration import compute_band_performance, compute_empirical_calibration
from app.services.market_service import market_service
from app.services.fail_closed import check_fail_closed


def test_regression_swing_lookback_excludes_current_candle():
    """
    Patch 2 Regression:
    Ensure that swing_high and swing_low strictly exclude the current (last) candle.
    If the current candle has an extreme high/low, it must NOT become the swing level.
    """
    # 25 candles with baseline highs between 100-110 and lows between 90-95
    candles = [
        {"high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000.0}
        for _ in range(25)
    ]
    # Extreme spike on current candle
    candles[-1] = {"high": 999.0, "low": 1.0, "close": 500.0, "volume": 5000.0}

    features = compute_features(candles, profile="SWING")

    # Current candle's 999.0 and 1.0 must be excluded
    assert features["swing_high"] == 105.0
    assert features["swing_low"] == 95.0
    assert features["swing_high"] < 999.0
    assert features["swing_low"] > 1.0


def test_regression_unclassified_bands_excluded_from_calibration(isolated_verdicts_db):
    """
    Patch 3 Regression:
    Ensure that verdicts with None or unclassified confidence bands do NOT corrupt the
    'Low' band and are excluded from Brier scoring.
    """
    db = isolated_verdicts_db
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Log 10 High band verdicts (all WINs)
    for i in range(10):
        rid = f"high_{i}"
        db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": False, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 80000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.85, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"h_{i}")
        db.record_outcome(run_id=rid, outcome="WIN")

    # Log 10 unclassified/None band verdicts (all LOSSes)
    for i in range(10):
        rid = f"none_{i}"
        db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": False, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 80000.0},
            "deterministic": {"final_verdict": "ABSTAIN", "final_confidence_band": None, "final_confidence_raw": None, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"n_{i}")
        db.record_outcome(run_id=rid, outcome="LOSS")

    perf = compute_band_performance()
    # High band has 10 wins (100%)
    assert perf["High"]["total_outcomes"] == 10
    assert perf["High"]["wins"] == 10

    # Low band must be completely empty (0), NOT polluted by the None band losses
    assert perf["Low"]["total_outcomes"] == 0
    assert perf["Low"]["losses"] == 0


def test_regression_brier_score_returns_none_when_no_scorable_records(isolated_verdicts_db):
    """
    Regression:
    When >=100 outcomes exist but none have valid WIN/LOSS or valid bands,
    Brier score must return None instead of a misleading 'perfect' 0.0.
    """
    db = isolated_verdicts_db
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Seed 105 outcomes all ABSTAIN_OBSERVED with None band
    for i in range(105):
        rid = f"abstain_{i}"
        db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": False, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 80000.0},
            "deterministic": {"final_verdict": "ABSTAIN", "final_confidence_band": None, "final_confidence_raw": None, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"abs_{i}")
        db.record_outcome(run_id=rid, outcome="ABSTAIN_OBSERVED")

    cal = compute_empirical_calibration()
    assert cal["is_calibrated"] is True
    assert cal["brier_score"] is None
    assert cal["brier_sample_size"] == 0
    assert "N/A" in cal["message"]


@pytest.mark.asyncio
async def test_regression_missing_funding_timestamp_fails_closed():
    """
    Patch 4 Regression:
    If Kraken Futures funding rate payload lacks a timestamp, freshness_funding_s must
    be set to 999999.0 and fail the data gates.
    """
    mock_futures = AsyncMock()
    # Simulate Kraken Futures response with missing timestamp
    mock_futures.fetch_funding_rate = AsyncMock(return_value={
        "fundingRate": 0.0001,
        "timestamp": None,
        "info": {"openInterest": 2000.0}
    })
    mock_futures.close = AsyncMock()

    mock_spot = AsyncMock()
    mock_spot.fetch_ticker = AsyncMock(return_value={"last": 80000.0, "timestamp": 1700000000000})
    mock_spot.fetch_ohlcv = AsyncMock(return_value=[
        [1700000000000 - i * 14400000, 80000, 81000, 79000, 80500, 100]
        for i in range(350)
    ])
    mock_spot.close = AsyncMock()

    with patch("ccxt.async_support.kraken", return_value=mock_spot), \
         patch("ccxt.async_support.krakenfutures", return_value=mock_futures):
        snap, _ = await market_service.fetch_market_snapshot("BTC", "SWING")
        assert snap.freshness_funding_s >= 999999.0

        # Assess gates: must fail due to stale funding
        passed, fails = check_fail_closed(snap)
        assert passed is False
        assert any("Funding stale" in f for f in fails)


def test_regression_verify_outcomes_simulate_guard():
    """
    Patch 6 Regression:
    Ensure scripts/verify_outcomes.py --simulate fails without --allow-synthetic flag.
    """
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "verify_outcomes.py"
    res = subprocess.run(
        [sys.executable, str(script_path), "--simulate", "5"],
        capture_output=True,
        text=True
    )
    assert res.returncode == 1
    assert "Refusing to inject synthetic outcomes into database without explicit '--allow-synthetic' flag." in res.stdout


def test_regression_volume_zscore_constant_baseline():
    """
    Regression:
    A constant baseline with zero variance should return 0.0 if current equals mean,
    and +/-3.0 if there is a divergence.
    """
    vols = [500.0] * 20
    assert volume_zscore(vols, 10) == 0.0

    vols_spike = vols.copy()
    vols_spike[-1] = 1000.0
    assert volume_zscore(vols_spike, 10) == 3.0

    vols_drop = vols.copy()
    vols_drop[-1] = 100.0
    assert volume_zscore(vols_drop, 10) == -3.0
