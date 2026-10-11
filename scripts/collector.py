"""
Jev v8 Serverless Collector Engine.
Executes periodic evaluation for SWING and DAY_CONTEXT profiles,
updates verdicts.db, and exports static JSON packets for GitHub Pages.
"""

import sys
import json
import asyncio
import datetime
from pathlib import Path

# Ensure project root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.services.phase0_runner import run_v8_evaluation
from app.services.calibration import get_full_calibration_report
from app.core.verdicts_db import verdicts_db


async def run_collector():
    print(f"[{datetime.datetime.now(datetime.timezone.utc).isoformat()}] Starting Jev v8 Serverless Collector...")

    # 1. Run SWING evaluation (Primary)
    print("Evaluating BTC SWING profile...")
    record_swing, lat_swing = await run_v8_evaluation(symbol="BTC", profile="SWING")
    print(f"SWING Verdict: {record_swing['deterministic']['final_verdict']} ({record_swing['deterministic']['final_confidence_band']}) in {lat_swing:.2f}s")

    # 2. Run DAY_CONTEXT evaluation
    print("Evaluating BTC DAY_CONTEXT profile...")
    record_day, lat_day = await run_v8_evaluation(symbol="BTC", profile="DAY_CONTEXT")
    print(f"DAY_CONTEXT Verdict: {record_day['deterministic']['final_verdict']} ({record_day['deterministic']['final_confidence_band']}) in {lat_day:.2f}s")

    # 3. Compile Calibration Report
    cal_report = get_full_calibration_report()

    # 4. Prepare data directory
    data_dir = ROOT_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    myt_tz = datetime.timezone(datetime.timedelta(hours=8))
    now_myt = datetime.datetime.now(myt_tz).strftime("%Y-%m-%d %H:%M:%S MYT")

    packet = {
        "updated_at_utc": now_utc,
        "updated_at_myt": now_myt,
        "symbol": "BTC",
        "venue": "kraken",
        "pair": "BTC/USD",
        "profiles": {
            "SWING": record_swing,
            "DAY_CONTEXT": record_day
        },
        "calibration": cal_report
    }

    # 5. Write static JSON files for GitHub Pages
    decision_path = data_dir / "latest_decision.json"
    cal_path = data_dir / "calibration.json"

    with open(decision_path, "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=2)
    print(f"Saved: {decision_path}")

    with open(cal_path, "w", encoding="utf-8") as f:
        json.dump(cal_report, f, indent=2)
    print(f"Saved: {cal_path}")

    print("Collector run completed successfully.")


if __name__ == "__main__":
    asyncio.run(run_collector())
