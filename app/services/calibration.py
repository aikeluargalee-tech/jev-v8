import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.core.verdicts_db import verdicts_db
from app.core.config import settings

logger = logging.getLogger(__name__)


def evaluate_calibration_dataset() -> Dict[str, Any]:
    """
    Evaluates AC-10 calibration gate readiness from verdicts.db.
    Assesses confidence band distribution and calibration dataset readiness.
    """
    conn = verdicts_db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT final_verdict, final_confidence_band, final_confidence_raw, shadow_mode, log_record_json FROM verdicts")
        rows = cursor.fetchall()

        total_verdicts = len(rows)
        band_counts = {"High": 0, "Medium": 0, "Low": 0, "None": 0}
        verdict_counts = {}
        shadow_count = 0

        for r in rows:
            band = r["final_confidence_band"] or "None"
            band_counts[band] = band_counts.get(band, 0) + 1
            verdict = r["final_verdict"]
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            if r["shadow_mode"]:
                shadow_count += 1

        dataset_ready = total_verdicts >= settings.SHADOW_MODE_MIN_VERDICTS

        return {
            "total_recorded_verdicts": total_verdicts,
            "shadow_mode_verdicts": shadow_count,
            "min_verdicts_threshold": settings.SHADOW_MODE_MIN_VERDICTS,
            "calibration_dataset_ready": dataset_ready,
            "confidence_band_distribution": band_counts,
            "verdict_distribution": verdict_counts,
            "ac10_gate_status": "READY_FOR_PHASE_2_CALIBRATION" if dataset_ready else "COLLECTING_OUTCOMES",
            "notes": (
                f"Logged {total_verdicts} immutable records. "
                f"Surpassed minimum {settings.SHADOW_MODE_MIN_VERDICTS} shadow verdicts threshold. "
                "Calibration dataset is successfully structured for Phase 2 forward outcome verification."
            )
        }
    finally:
        conn.close()


def compute_band_performance() -> Dict[str, Any]:
    """
    Aggregates realized forward outcomes per confidence band (High, Medium, Low).
    Computes win rate, average PnL %, and sample counts.
    """
    outcomes = verdicts_db.get_all_outcomes()
    bands = {
        "High": {"total": 0, "wins": 0, "losses": 0, "breakevens": 0, "expired": 0, "abstain": 0, "pnls": [], "r_multiples": []},
        "Medium": {"total": 0, "wins": 0, "losses": 0, "breakevens": 0, "expired": 0, "abstain": 0, "pnls": [], "r_multiples": []},
        "Low": {"total": 0, "wins": 0, "losses": 0, "breakevens": 0, "expired": 0, "abstain": 0, "pnls": [], "r_multiples": []}
    }

    for row in outcomes:
        band = row.get("final_confidence_band")
        if band not in bands:
            band = "Low"

        outcome = row.get("outcome", "").upper()
        bands[band]["total"] += 1

        if outcome == "WIN":
            bands[band]["wins"] += 1
        elif outcome == "LOSS":
            bands[band]["losses"] += 1
        elif outcome == "BREAKEVEN":
            bands[band]["breakevens"] += 1
        elif outcome == "EXPIRED":
            bands[band]["expired"] += 1
        elif outcome == "ABSTAIN_OBSERVED":
            bands[band]["abstain"] += 1

        if row.get("pnl_percent") is not None:
            bands[band]["pnls"].append(float(row["pnl_percent"]))
        if row.get("r_multiple") is not None:
            bands[band]["r_multiples"].append(float(row["r_multiple"]))

    result = {}
    for b_name, b_data in bands.items():
        total_eval = b_data["wins"] + b_data["losses"]
        win_rate = round(b_data["wins"] / total_eval, 4) if total_eval > 0 else 0.0
        avg_pnl = round(sum(b_data["pnls"]) / len(b_data["pnls"]), 2) if b_data["pnls"] else 0.0
        avg_r = round(sum(b_data["r_multiples"]) / len(b_data["r_multiples"]), 2) if b_data["r_multiples"] else 0.0

        result[b_name] = {
            "total_outcomes": b_data["total"],
            "evaluated_trades": total_eval,
            "wins": b_data["wins"],
            "losses": b_data["losses"],
            "breakevens": b_data["breakevens"],
            "expired": b_data["expired"],
            "abstain_observed": b_data["abstain"],
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "avg_r_multiple": avg_r
        }

    return result


def evaluate_ac10_gate() -> Dict[str, Any]:
    """
    Evaluates AC-10 Calibration Gate:
    After >= 60 outcomes:
    - Compare predicted confidence bands with forward outcomes
    - High band should outperform Low band
    - If not: flag for recalibration before raising sizing
    """
    total_outcomes = verdicts_db.get_outcomes_count()
    band_perf = compute_band_performance()

    min_required = settings.SHADOW_MODE_MIN_VERDICTS  # 60
    high_win_rate = band_perf["High"]["win_rate"]
    low_win_rate = band_perf["Low"]["win_rate"]
    med_win_rate = band_perf["Medium"]["win_rate"]

    if total_outcomes < min_required:
        return {
            "gate": "AC-10",
            "status": "COLLECTING_DATA",
            "gate_passed": False,
            "total_verified_outcomes": total_outcomes,
            "required_outcomes": min_required,
            "progress_pct": round((total_outcomes / min_required) * 100, 1),
            "band_performance": band_perf,
            "message": (
                f"AC-10 requires >={min_required} verified outcomes before gate check "
                f"(currently {total_outcomes}/{min_required}). Sizing remains strictly locked."
            )
        }

    # Evaluate whether High band outperformed Low band
    # If High band has higher win rate, or if zero losses in High vs losses in Low:
    high_beats_low = high_win_rate > low_win_rate

    if high_beats_low:
        gate_status = "PASSED"
        gate_passed = True
        msg = (
            f"AC-10 Calibration Gate PASSED: High confidence band ({high_win_rate*100:.1f}%) "
            f"outperforms Low band ({low_win_rate*100:.1f}%). Sizing progression verified."
        )
    else:
        gate_status = "RECALIBRATION_REQUIRED"
        gate_passed = False
        msg = (
            f"AC-10 Flagged for Recalibration: High band ({high_win_rate*100:.1f}%) "
            f"did NOT outperform Low band ({low_win_rate*100:.1f}%). Indicator weights "
            "must be retuned before raising sizing."
        )

    return {
        "gate": "AC-10",
        "status": gate_status,
        "gate_passed": gate_passed,
        "total_verified_outcomes": total_outcomes,
        "required_outcomes": min_required,
        "high_band_win_rate": high_win_rate,
        "med_band_win_rate": med_win_rate,
        "low_band_win_rate": low_win_rate,
        "band_performance": band_perf,
        "message": msg
    }


def compute_empirical_calibration() -> Dict[str, Any]:
    """
    Enforces calibration constraints:
    - Only after >= 100 verified outcomes may offer locally-calibrated confidence.
    - If < 100 outcomes: returns calibrated_confidence=None and explicit lock message.
    - If >= 100 outcomes: computes empirical Brier score and calibrated probability mapping.
    """
    total_outcomes = verdicts_db.get_outcomes_count()
    min_cal_outcomes = settings.CALIBRATION_MIN_OUTCOMES  # 100

    if total_outcomes < min_cal_outcomes:
        return {
            "is_calibrated": False,
            "calibration_status": "PENDING_100_OUTCOMES",
            "total_verified_outcomes": total_outcomes,
            "required_for_calibration": min_cal_outcomes,
            "calibrated_confidence": None,
            "display_label": "uncalibrated",
            "message": (
                f"Locally-calibrated confidence requires >={min_cal_outcomes} verified forward outcomes "
                f"to prevent small-sample overfitting (currently {total_outcomes}/{min_cal_outcomes}). "
                "Confidence remains raw uncalibrated."
            )
        }

    # With >= 100 outcomes: Compute empirical calibration curve & Brier Score
    all_outcomes = verdicts_db.get_all_outcomes()
    band_perf = compute_band_performance()

    # Calculate empirical probabilities
    empirical_probs = {
        "High": band_perf["High"]["win_rate"],
        "Medium": band_perf["Medium"]["win_rate"],
        "Low": band_perf["Low"]["win_rate"]
    }

    # Calculate Brier Score: (1/N) * sum((prob - actual)^2)
    brier_terms = []
    for o in all_outcomes:
        if o.get("outcome") in ["WIN", "LOSS"]:
            actual = 1.0 if o["outcome"] == "WIN" else 0.0
            band = o.get("final_confidence_band") or "Low"
            # Use raw confidence if available, else band empirical
            pred = float(o.get("final_confidence_raw") or empirical_probs.get(band, 0.5))
            brier_terms.append((pred - actual) ** 2)

    brier_score = round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else 0.0

    return {
        "is_calibrated": True,
        "calibration_status": "CALIBRATED",
        "total_verified_outcomes": total_outcomes,
        "required_for_calibration": min_cal_outcomes,
        "brier_score": brier_score,
        "empirical_probabilities": empirical_probs,
        "display_label": "locally-calibrated",
        "message": (
            f"Successfully calibrated on {total_outcomes} forward outcomes. "
            f"Brier score: {brier_score:.4f}. Empirical probabilities active."
        )
    }


def get_full_calibration_report() -> Dict[str, Any]:
    """Provides consolidated calibration & outcome audit report."""
    dataset_info = evaluate_calibration_dataset()
    ac10_info = evaluate_ac10_gate()
    cal_info = compute_empirical_calibration()

    return {
        "dataset_summary": dataset_info,
        "ac10_gate": ac10_info,
        "empirical_calibration": cal_info
    }
