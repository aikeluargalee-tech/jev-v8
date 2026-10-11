import pytest
import time
import uuid
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.core.verdicts_db import VerdictsDatabase
from app.services.market_service import filter_closed_candles
from app.services.calibration import (
    evaluate_ac10_gate,
    compute_empirical_calibration,
    compute_band_performance,
    get_full_calibration_report
)
from app.services.typesafe_service import TypeSafeService


@pytest.fixture
def temp_db(tmp_path):
    """Provides isolated test database."""
    db_file = tmp_path / "test_calibration.db"
    db = VerdictsDatabase(db_path=db_file)
    db.init_db()
    return db


def test_filter_closed_candles_tightened():
    """
    Chief Engineer Phase 2 Tightening Check:
    - Slices off the forming candle with raw_candles[:-1].
    - Guarantees that only candles with candle_close_ts <= now_ts are included.
    """
    now = 1700000000.0
    dur = 4 * 3600  # 14400s (4h)

    # Candle 0: Closed (open: now - 3*dur, close: now - 2*dur)
    c0 = [(now - 3 * dur) * 1000, 80000, 81000, 79000, 80500, 100]
    # Candle 1: Closed (open: now - 2*dur, close: now - dur)
    c1 = [(now - 2 * dur) * 1000, 80500, 82000, 80000, 81500, 120]
    # Candle 2: Closed exactly at now (open: now - dur, close: now)
    c2 = [(now - dur) * 1000, 81500, 83000, 81000, 82500, 150]
    # Candle 3: Currently forming (open: now, close: now + dur)
    c3 = [now * 1000, 82500, 83500, 82000, 83000, 50]

    raw = [c0, c1, c2, c3]
    closed = filter_closed_candles(raw, "4h", now)

    # Candle 3 (forming) must be sliced off
    assert len(closed) == 3
    # Check that close_time is calculated as open + duration and is <= now
    for candle in closed:
        assert candle["close_time"] <= now
        assert candle["close_time"] == candle["time"] + dur


def test_verdict_outcomes_crud(temp_db):
    """Verifies storing and retrieving verified forward outcomes."""
    run_id = str(uuid.uuid4())
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Log dummy verdict first
    record = {
        "run_id": run_id,
        "ts_utc": now_iso,
        "profile": "SWING",
        "rule_ver": "v8.0",
        "shadow_mode": True,
        "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
        "deterministic": {
            "final_verdict": "BUY",
            "final_confidence_band": "High",
            "final_confidence_raw": 0.82,
            "data_gates_passed": True
        },
        "cost": {"total_cost_usd": 0.0}
    }
    temp_db.log_verdict(record, "test_hash")

    # Record outcome
    res = temp_db.record_outcome(
        run_id=run_id,
        outcome="WIN",
        entry_price=82000.0,
        exit_price=85000.0,
        pnl_percent=3.66,
        r_multiple=2.1,
        hit_target=True,
        hit_stop=False,
        notes="Target 1 hit cleanly"
    )

    assert res["outcome"] == "WIN"
    assert res["run_id"] == run_id
    assert temp_db.get_outcomes_count() == 1

    stored = temp_db.get_outcome(run_id)
    assert stored is not None
    assert stored["outcome"] == "WIN"
    assert stored["pnl_percent"] == 3.66
    assert stored["hit_target"] == 1

    # Invalid outcome check
    with pytest.raises(ValueError):
        temp_db.record_outcome(run_id=run_id, outcome="INVALID_OUTCOME")

    # Non-existent run_id check
    with pytest.raises(KeyError):
        temp_db.record_outcome(run_id="non-existent-id", outcome="WIN")


def test_ac10_gate_collecting_data_under_60(temp_db, monkeypatch):
    """AC-10 Gate: Status is COLLECTING_DATA when verified outcomes < 60."""
    import app.services.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    report = evaluate_ac10_gate()
    assert report["gate"] == "AC-10"
    assert report["status"] == "COLLECTING_DATA"
    assert report["gate_passed"] is False
    assert report["total_verified_outcomes"] == 0
    assert "Sizing remains strictly locked" in report["message"]


def test_ac10_gate_passed_when_high_beats_low(temp_db, monkeypatch):
    """AC-10 Gate: PASSED when High confidence band win rate > Low band win rate (>=60 outcomes)."""
    import app.services.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Seed 30 High band verdicts (24 Wins, 6 Losses -> 80% Win Rate)
    for i in range(30):
        rid = f"high_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": True, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.85, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"hash_{i}")
        outcome = "WIN" if i < 24 else "LOSS"
        temp_db.record_outcome(run_id=rid, outcome=outcome, pnl_percent=4.0 if outcome == "WIN" else -2.0)

    # Seed 35 Low band verdicts (10 Wins, 25 Losses -> 28.5% Win Rate)
    for i in range(35):
        rid = f"low_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": True, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "Low", "final_confidence_raw": 0.45, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"hash_l_{i}")
        outcome = "WIN" if i < 10 else "LOSS"
        temp_db.record_outcome(run_id=rid, outcome=outcome, pnl_percent=2.0 if outcome == "WIN" else -2.0)

    assert temp_db.get_outcomes_count() == 65
    gate = evaluate_ac10_gate()

    assert gate["status"] == "PASSED"
    assert gate["gate_passed"] is True
    assert gate["high_band_win_rate"] == 0.8
    assert gate["low_band_win_rate"] < 0.3
    assert "AC-10 Calibration Gate PASSED" in gate["message"]


def test_ac10_gate_recalibration_required_when_low_beats_high(temp_db, monkeypatch):
    """AC-10 Gate: RECALIBRATION_REQUIRED when High band does not outperform Low band."""
    import app.services.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Seed 30 High band verdicts (10 Wins, 20 Losses -> 33% Win Rate)
    for i in range(30):
        rid = f"high_fail_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": True, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.85, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"hash_hf_{i}")
        outcome = "WIN" if i < 10 else "LOSS"
        temp_db.record_outcome(run_id=rid, outcome=outcome, pnl_percent=2.0 if outcome == "WIN" else -2.0)

    # Seed 35 Low band verdicts (25 Wins, 10 Losses -> 71% Win Rate)
    for i in range(35):
        rid = f"low_win_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": True, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "Low", "final_confidence_raw": 0.45, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"hash_lw_{i}")
        outcome = "WIN" if i < 25 else "LOSS"
        temp_db.record_outcome(run_id=rid, outcome=outcome, pnl_percent=3.0 if outcome == "WIN" else -2.0)

    assert temp_db.get_outcomes_count() == 65
    gate = evaluate_ac10_gate()

    assert gate["status"] == "RECALIBRATION_REQUIRED"
    assert gate["gate_passed"] is False
    assert "AC-10 Flagged for Recalibration" in gate["message"]


def test_calibration_locked_under_100_outcomes(temp_db, monkeypatch):
    """Constraint Check: Locally-calibrated confidence remains locked until >= 100 verified outcomes."""
    import app.services.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    # With only 65 outcomes
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for i in range(65):
        rid = f"cal_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": True, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.8, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"h_{i}")
        temp_db.record_outcome(run_id=rid, outcome="WIN")

    cal = compute_empirical_calibration()
    assert cal["is_calibrated"] is False
    assert cal["calibrated_confidence"] is None
    assert cal["display_label"] == "uncalibrated"
    assert "Locally-calibrated confidence requires >=100 verified forward outcomes" in cal["message"]


def test_empirical_calibration_active_at_100_outcomes(temp_db, monkeypatch):
    """Verification Check: With >= 100 verified outcomes, empirical calibration activates and computes Brier score."""
    import app.services.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for i in range(105):
        rid = f"cal_100_{i}"
        temp_db.log_verdict({
            "run_id": rid, "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
            "shadow_mode": False, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
            "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.8, "data_gates_passed": True},
            "cost": {"total_cost_usd": 0.0}
        }, f"h_100_{i}")
        outcome = "WIN" if i % 3 != 0 else "LOSS"
        temp_db.record_outcome(run_id=rid, outcome=outcome)

    cal = compute_empirical_calibration()
    assert cal["is_calibrated"] is True
    assert cal["calibration_status"] == "CALIBRATED"
    assert cal["display_label"] == "locally-calibrated"
    assert "brier_score" in cal
    assert 0.0 <= cal["brier_score"] <= 1.0


@pytest.mark.asyncio
async def test_typesafe_service_mock_default():
    """Cost Guardrail Check: Real Jev disabled by default (ENABLE_JEV=False)."""
    service = TypeSafeService(api_key="any-key")
    service.enable_jev = False

    dummy_state = {"market_cat": {"ema_stack": "bullish", "rsi_cat": "oversold"}}
    res = await service.evaluate_state(dummy_state)

    assert res["model_ver"] == "mock-v8"
    assert res["is_fallback"] is False
    assert "TradeAction" in res
    assert "DataQuality" in res


@pytest.mark.asyncio
async def test_typesafe_service_cost_guardrail_blocking(temp_db, monkeypatch):
    """Budget Guardrail Check: Jev call blocked if monthly cumulative cost >= $20."""
    import app.services.typesafe_service as ts_mod
    monkeypatch.setattr(ts_mod, "verdicts_db", temp_db)

    # Seed $20.00 cost into temp_db
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    temp_db.log_verdict({
        "run_id": "seed_cost", "ts_utc": now_iso, "profile": "SWING", "rule_ver": "v8.0",
        "shadow_mode": False, "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82000.0},
        "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.8, "data_gates_passed": True},
        "cost": {"total_cost_usd": 20.01}
    }, "hash_cost")

    service = TypeSafeService(api_key="real-test-key")
    service.enable_jev = True

    dummy_state = {"market_cat": {"ema_stack": "bullish"}}
    res = await service.evaluate_state(dummy_state)

    assert res["is_fallback"] is True
    assert res["model_ver"] == "cost-guardrail-blocked"


@pytest.mark.asyncio
async def test_typesafe_service_missing_api_key_fail_closed():
    """Security Check: Missing API key with ENABLE_JEV=True fails closed with fallback."""
    service = TypeSafeService(api_key="")
    service.enable_jev = True

    dummy_state = {"market_cat": {"ema_stack": "bullish"}}
    res = await service.evaluate_state(dummy_state)

    assert res["is_fallback"] is True
    assert res["model_ver"] == "fallback-unconfigured"


@pytest.mark.asyncio
async def test_typesafe_service_real_call_payload_and_2_questions():
    """Protocol Check: Real TypeSafe invocation passes compact state and max 2 questions."""
    service = TypeSafeService(api_key="valid-test-key")
    service.enable_jev = True

    # Mock AsyncTypeSafeClient
    mock_response = MagicMock()
    mock_response.model = "typesafe-system-one-v1"

    action_ans = MagicMock()
    action_ans.choice = "BUY"
    action_ans.confidence = 0.78
    action_ans.probabilities = {"BUY": 0.78, "ABSTAIN": 0.22}

    quality_ans = MagicMock()
    quality_ans.choice = "reliable"
    quality_ans.confidence = 0.85

    mock_response.answers = {
        "TradeAction": action_ans,
        "DataQuality": quality_ans
    }

    mock_client = AsyncMock()
    mock_client.system_one = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    compact_state = {
        "profile": "SWING",
        "market_cat": {"ema_stack": "bullish", "ema200_pos": "above", "rsi_cat": "oversold"},
        "derivatives_cat": {"funding_cat": "negative"},
        "social_meta": {"polarity_cat": "fear", "diversity_ratio": 0.7}
    }

    with patch("typesafe_sdk.AsyncTypeSafeClient", return_value=mock_client):
        res = await service.evaluate_state(compact_state)

        # Assert exactly 2 questions were sent in questions dict
        call_kwargs = mock_client.system_one.call_args.kwargs
        sent_questions = call_kwargs["questions"]
        assert len(sent_questions) == 2
        assert "TradeAction" in sent_questions
        assert "DataQuality" in sent_questions
        assert call_kwargs["state"] == compact_state

        # Assert response shape conforms to v8_jev_raw
        assert res["TradeAction"]["choice"] == "BUY"
        assert res["TradeAction"]["confidence"] == 0.78
        assert res["DataQuality"]["choice"] == "reliable"
        assert res["is_fallback"] is False


@pytest.mark.asyncio
async def test_api_endpoints_outcome_and_calibration(temp_db, monkeypatch):
    """API Check: Verify /outcome, /recent, and /calibration endpoints."""
    import app.core.verdicts_db as vdb_mod
    import app.services.calibration as cal_mod
    monkeypatch.setattr(vdb_mod, "verdicts_db", temp_db)
    monkeypatch.setattr(cal_mod, "verdicts_db", temp_db)

    # Seed verdict
    run_id = str(uuid.uuid4())
    temp_db.log_verdict({
        "run_id": run_id, "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profile": "SWING", "rule_ver": "v8.0", "shadow_mode": True,
        "market": {"venue": "kraken", "pair": "BTC/USD", "last_price": 82500.0},
        "deterministic": {"final_verdict": "BUY", "final_confidence_band": "High", "final_confidence_raw": 0.8, "data_gates_passed": True},
        "cost": {"total_cost_usd": 0.0}
    }, "hash_api")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # 1. Post Outcome
        post_res = await client.post(f"/api/v1/verdicts/{run_id}/outcome", json={
            "outcome": "WIN",
            "exit_price": 85000.0,
            "pnl_percent": 3.03,
            "r_multiple": 1.8,
            "hit_target": True,
            "hit_stop": False,
            "notes": "API test outcome"
        })
        assert post_res.status_code == 200
        assert post_res.json()["status"] == "success"

        # 2. Get Recent Verdicts
        recent_res = await client.get("/api/v1/verdicts/recent")
        assert recent_res.status_code == 200
        assert recent_res.json()["count"] >= 1

        # 3. Get Calibration Report
        cal_res = await client.get("/api/v1/calibration")
        assert cal_res.status_code == 200
        report = cal_res.json()["report"]
        assert "dataset_summary" in report
        assert "ac10_gate" in report
        assert "empirical_calibration" in report
