from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging
import re

from app.services.phase0_runner import run_v8_evaluation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Analysis"])

SYMBOL_REGEX = re.compile(r"^[A-Z0-9]{1,15}$")


class AnalyzeRequest(BaseModel):
    symbol: str = Field("BTC", description="Crypto symbol (BTC/USD primary)")
    profile: str = Field("SWING", description="Operational profile: SWING (primary) or DAY_CONTEXT (intraday context)")
    force_shadow: Optional[bool] = Field(None, description="Force shadow mode status if overriding default calibration window")


@router.post("/analyze")
async def analyze_asset(req: AnalyzeRequest) -> Dict[str, Any]:
    """
    On-demand decision-support evaluation powered by v8 engine.
    - Zero trading keys or execution endpoints.
    - Fails closed on stale, synthetic, fallback, or insufficient history.
    - Returns Lean 5 indicators, 3 pillars, conflict analysis, and Structure+ATR levels.
    """
    sym = req.symbol.strip().upper().replace("$", "")
    if not SYMBOL_REGEX.match(sym):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid crypto symbol '{req.symbol}'. Symbol must be 1-15 alphanumeric characters."
        )

    profile = req.profile.upper()
    if profile not in ["SWING", "DAY_CONTEXT"]:
        profile = "SWING"

    try:
        record, latency_s = await run_v8_evaluation(
            symbol=sym,
            profile=profile,
            force_shadow=req.force_shadow
        )
        return {
            "status": "success",
            "latency_s": round(latency_s, 2),
            "record": record
        }

    except Exception as e:
        logger.error(f"Error during v8 evaluation of {sym}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to evaluate {sym}: {str(e)}")


@router.get("/status")
async def get_system_status() -> Dict[str, Any]:
    """Returns local engine status and calibration stats."""
    from app.core.verdicts_db import verdicts_db
    from app.core.config import settings

    total_verdicts = verdicts_db.get_verdict_count()
    total_outcomes = verdicts_db.get_outcomes_count()
    monthly_cost = verdicts_db.get_monthly_cumulative_cost()
    is_shadow = total_verdicts < settings.SHADOW_MODE_MIN_VERDICTS

    return {
        "venue": settings.DEFAULT_VENUE,
        "pair": settings.DEFAULT_PAIR,
        "total_logged_verdicts": total_verdicts,
        "total_verified_outcomes": total_outcomes,
        "shadow_mode_active": is_shadow,
        "shadow_mode_threshold": settings.SHADOW_MODE_MIN_VERDICTS,
        "calibration_min_outcomes": settings.CALIBRATION_MIN_OUTCOMES,
        "monthly_cumulative_usd": monthly_cost,
        "monthly_budget_usd": settings.MONTHLY_BUDGET_USD,
        "rule_ver": "v8.0"
    }


class OutcomeRequest(BaseModel):
    outcome: str = Field(..., description="WIN | LOSS | BREAKEVEN | EXPIRED | ABSTAIN_OBSERVED")
    exit_price: Optional[float] = Field(None, description="Actual realized exit price")
    pnl_percent: Optional[float] = Field(None, description="Realized PnL %")
    r_multiple: Optional[float] = Field(None, description="Realized R-multiple")
    hit_target: Optional[bool] = Field(None, description="True if primary target reached")
    hit_stop: Optional[bool] = Field(None, description="True if stop loss hit")
    notes: Optional[str] = Field(None, description="User trade execution notes")


@router.post("/verdicts/{run_id}/outcome")
async def record_verdict_outcome(run_id: str, req: OutcomeRequest) -> Dict[str, Any]:
    """
    Records a verified forward outcome for a past verdict run.
    Stores outcome in verdict_outcomes table for AC-10 evaluation.
    """
    from app.core.verdicts_db import verdicts_db

    try:
        res = verdicts_db.record_outcome(
            run_id=run_id,
            outcome=req.outcome,
            exit_price=req.exit_price,
            pnl_percent=req.pnl_percent,
            r_multiple=req.r_multiple,
            hit_target=req.hit_target,
            hit_stop=req.hit_stop,
            notes=req.notes
        )
        return {"status": "success", "outcome": res}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error recording outcome for {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to record outcome: {str(e)}")


@router.get("/verdicts/recent")
async def get_recent_verdicts_with_outcomes(limit: int = 50) -> Dict[str, Any]:
    """Retrieves recent verdicts with outcome status."""
    from app.core.verdicts_db import verdicts_db

    lim = max(1, min(limit, 200))
    rows = verdicts_db.get_verdicts_with_outcomes(lim)
    return {
        "status": "success",
        "count": len(rows),
        "verdicts": rows
    }


@router.get("/calibration")
async def get_calibration_report() -> Dict[str, Any]:
    """
    Returns full calibration status:
    - AC-10 gate status and band performance (High vs Low)
    - Empirical calibration status (requires >=100 outcomes)
    """
    from app.services.calibration import get_full_calibration_report

    try:
        report = get_full_calibration_report()
        return {"status": "success", "report": report}
    except Exception as e:
        logger.error(f"Error compiling calibration report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate calibration report: {str(e)}")
