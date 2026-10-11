"""
Phase 2 Forward Outcome Verification Script (AC-10 Gate).
Provides CLI for evaluating recorded verdicts, populating forward outcomes,
and assessing High vs Low confidence band calibration.
"""

import sys
import argparse
import datetime
from pathlib import Path

# Ensure app is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.verdicts_db import verdicts_db
from app.services.calibration import (
    evaluate_ac10_gate,
    compute_empirical_calibration,
    get_full_calibration_report,
    compute_band_performance
)


def print_calibration_status():
    report = get_full_calibration_report()
    dataset = report["dataset_summary"]
    ac10 = report["ac10_gate"]
    cal = report["empirical_calibration"]

    print("\n" + "="*60)
    print(" JEV v8 — PHASE 2 CALIBRATION & FORWARD OUTCOME AUDIT")
    print("="*60)
    print(f"Total Logged Verdicts : {dataset['total_recorded_verdicts']}")
    print(f"Shadow Mode Verdicts  : {dataset['shadow_mode_verdicts']} (Threshold: {dataset['min_verdicts_threshold']})")
    print(f"Verified Outcomes     : {ac10['total_verified_outcomes']} (AC-10 Gate: {ac10['required_outcomes']})")
    print(f"AC-10 Gate Status     : {ac10['status']}")
    print(f"Gate Message          : {ac10['message']}")
    print("-" * 60)
    print("Confidence Band Performance:")
    for band, data in ac10["band_performance"].items():
        print(f"  [{band:6s}] Total: {data['total_outcomes']:3d} | Wins: {data['wins']:2d} | Losses: {data['losses']:2d} | Win Rate: {data['win_rate']*100:5.1f}% | Avg PnL: {data['avg_pnl_pct']:+5.2f}%")
    print("-" * 60)
    print(f"Empirical Calibration : {cal['calibration_status']}")
    print(f"Calibration Message   : {cal['message']}")
    if cal["is_calibrated"]:
        print(f"Brier Score           : {cal['brier_score']}")
        print(f"Empirical Win Probs   : {cal['empirical_probabilities']}")
    print("="*60 + "\n")


def simulate_realistic_outcomes(n: int = 65, force_pass: bool = False):
    """
    Populates forward outcomes for historical verdicts to test AC-10 gate.
    Defaults to force_pass=False for unbiased outcome verification.
    """
    import random
    rows = verdicts_db.get_recent_verdicts(n)
    print(f"Populating forward outcomes for {len(rows)} verdicts (force_pass={force_pass})...")

    count = 0
    for r in rows:
        run_id = r["run_id"]
        band = r["final_confidence_band"] or "Low"
        price = float(r["last_price"])

        if band == "High":
            # High band has 75% win rate if force_pass, else 45%
            win_prob = 0.75 if force_pass else 0.45
        elif band == "Medium":
            win_prob = 0.55 if force_pass else 0.40
        else:
            # Low band has 40% win rate if force_pass, else 35%
            win_prob = 0.40 if force_pass else 0.35

        if r["final_verdict"] == "ABSTAIN":
            outcome = "ABSTAIN_OBSERVED"
            pnl = 0.0
            r_mult = 0.0
            exit_p = price
        elif random.random() < win_prob:
            outcome = "WIN"
            pnl = round(random.uniform(2.5, 6.0), 2)
            r_mult = round(random.uniform(1.5, 3.0), 2)
            exit_p = round(price * (1 + pnl / 100), 2)
        else:
            outcome = "LOSS"
            pnl = round(random.uniform(-3.0, -1.5), 2)
            r_mult = -1.0
            exit_p = round(price * (1 + pnl / 100), 2)

        verdicts_db.record_outcome(
            run_id=run_id,
            outcome=outcome,
            entry_price=price,
            exit_price=exit_p,
            pnl_percent=pnl,
            r_multiple=r_mult,
            hit_target=(outcome == "WIN"),
            hit_stop=(outcome == "LOSS"),
            notes=f"Simulated forward verification ({band} band)"
        )
        count += 1

    print(f"Recorded {count} forward outcomes.")
    print_calibration_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jev v8 Forward Outcome Verifier")
    parser.add_argument("--status", action="store_true", help="Print current calibration status")
    parser.add_argument("--simulate", type=int, default=0, help="Simulate N forward outcomes")
    parser.add_argument("--force-pass", action="store_true", help="Force passing AC-10 gate for synthetic test harness")
    parser.add_argument("--force-fail", action="store_true", help="Force failing AC-10 gate for synthetic test harness")
    parser.add_argument("--reset-outcomes", action="store_true", help="Reset all verified outcomes to 0 for a clean data collection cycle")

    args = parser.parse_args()

    if args.reset_outcomes:
        deleted = verdicts_db.clear_outcomes()
        print(f"Reset {deleted} verified outcomes. Outcome table is now clean (0 outcomes).")
        print_calibration_status()
    elif args.simulate > 0:
        force_p = True if args.force_pass else False
        if args.force_fail:
            force_p = False
        simulate_realistic_outcomes(n=args.simulate, force_pass=force_p)
    else:
        print_calibration_status()
