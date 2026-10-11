import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.market_service import market_service
from app.services.phase0_runner import run_v8_evaluation
from app.core.verdicts_db import verdicts_db
from app.services.calibration import evaluate_calibration_dataset


@pytest.mark.asyncio
async def test_market_snapshot_live():
    """Verify live Kraken snapshot generation for SWING and DAY_CONTEXT."""
    snap_swing, payload_swing = await market_service.fetch_market_snapshot("BTC", "SWING")
    assert snap_swing.venue == "kraken"
    assert snap_swing.pair == "BTC/USD"
    assert snap_swing.last_price > 0
    assert snap_swing.candle_count_4h >= 300
    assert snap_swing.is_fallback is False
    assert snap_swing.is_synthetic is False

    snap_day, payload_day = await market_service.fetch_market_snapshot("BTC", "DAY_CONTEXT")
    assert snap_day.candle_count_15m >= 300
    assert snap_day.is_fallback is False


@pytest.mark.asyncio
async def test_v8_pipeline_runner():
    """Verify end-to-end v8 evaluation logs immutable row to verdicts.db."""
    initial_count = verdicts_db.get_verdict_count()
    # Test forced shadow mode
    record_shadow, latency_s = await run_v8_evaluation("BTC", "SWING", force_shadow=True)
    assert latency_s < 30.0  # Exit gate: median < 30s
    assert record_shadow["rule_ver"] == "v8.0"
    assert record_shadow["shadow_mode"] is True
    assert record_shadow["cost"]["total_cost_usd"] == 0.0

    # Test post-60 verdict natural shadow state
    record_auto, _ = await run_v8_evaluation("BTC", "SWING")
    assert "features" in record_auto
    assert "categorical" in record_auto
    assert "deterministic" in record_auto

    new_count = verdicts_db.get_verdict_count()
    assert new_count == initial_count + 2


@pytest.mark.asyncio
async def test_unlisted_symbol_fails_closed():
    """Verify unlisted symbol triggers fail-closed with is_fallback=True and price=0."""
    snap, payload = await market_service.fetch_market_snapshot("NONEXISTENT_SYMBOL_XYZ", "SWING")
    assert snap.is_fallback is True
    assert snap.last_price == 0.0
    assert snap.candle_count_4h == 0


@pytest.mark.asyncio
async def test_security_settings_endpoint_deleted():
    """Verify settings mutation endpoint returns 404 (deleted for security)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/settings", json={"typesafe_api_key": "some_key"})
        assert res.status_code == 404


def test_ac10_calibration_dataset_accumulated():
    """Verify AC-10 calibration gate readiness from verdicts.db."""
    calib = evaluate_calibration_dataset()
    assert calib["total_recorded_verdicts"] >= 60
    assert calib["calibration_dataset_ready"] is True
    assert calib["ac10_gate_status"] == "READY_FOR_PHASE_2_CALIBRATION"
