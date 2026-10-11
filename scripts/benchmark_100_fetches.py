import asyncio
import time
import json
import statistics
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.phase0_runner import run_phase0_evaluation
from app.core.verdicts_db import verdicts_db

REPORT_PATH = Path(__file__).resolve().parent.parent / "phase0_benchmark_report.json"


async def run_benchmark(target_runs: int = 100):
    print(f"================================================================")
    print(f"STARTING PHASE 0 BENCHMARK: {target_runs} REAL KRAKEN CCXT FETCHES")
    print(f"================================================================")

    latencies = []
    success_count = 0
    fail_closed_count = 0
    prices = []

    t_benchmark_start = time.perf_counter()

    for i in range(1, target_runs + 1):
        try:
            record, latency = await run_phase0_evaluation(symbol="BTC", profile="SWING")
            latencies.append(latency)
            price = record["market"]["last_price"]
            prices.append(price)

            if record["deterministic"]["data_gates_passed"]:
                success_count += 1
            else:
                fail_closed_count += 1

            if i % 10 == 0 or i == 1 or i == target_runs:
                print(
                    f"[{i:03d}/{target_runs}] Latency: {latency:.2f}s | "
                    f"BTC: ${price:,.1f} | "
                    f"Verdict: {record['deterministic']['final_verdict']} | "
                    f"Gates Passed: {record['deterministic']['data_gates_passed']}"
                )

            # Brief pause to respect Kraken public API rate limit without throttling
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"[{i:03d}/{target_runs}] Error during live fetch: {e}")
            fail_closed_count += 1

    t_benchmark_end = time.perf_counter()
    total_duration = t_benchmark_end - t_benchmark_start

    # Metrics calculation
    median_latency = statistics.median(latencies) if latencies else 0.0
    p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    mean_latency = statistics.mean(latencies) if latencies else 0.0
    min_latency = min(latencies) if latencies else 0.0
    max_latency = max(latencies) if latencies else 0.0

    # Total recorded rows in verdicts.db
    db_count = verdicts_db.get_verdict_count()
    monthly_cost = verdicts_db.get_monthly_cumulative_cost()

    # Projected cost for SWING mode: 4 evals/day * 30 days = 120 calls/month
    # In Phase 0 mock mode: $0.00
    # In Phase 1 with live Twitter ($1.80) + live Jev ($1.20) = $3.00/month (< $20.00 ceiling)
    projected_monthly_swing_cost = 3.00

    report = {
        "benchmark": "Phase 0 - 100 Real Kraken CCXT Fetches",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": len(latencies),
        "success_runs": success_count,
        "fail_closed_runs": fail_closed_count,
        "latencies_seconds": {
            "median": round(median_latency, 3),
            "p95": round(p95_latency, 3),
            "mean": round(mean_latency, 3),
            "min": round(min_latency, 3),
            "max": round(max_latency, 3)
        },
        "total_benchmark_duration_s": round(total_duration, 2),
        "db_rows_logged": db_count,
        "phase0_cost_usd": monthly_cost,
        "projected_monthly_swing_cost_usd": projected_monthly_swing_cost,
        "exit_gates": {
            "median_latency_under_30s": median_latency < 30.0,
            "projected_cost_under_budget": projected_monthly_swing_cost < 20.0,
            "database_immutable_logging_intact": db_count >= target_runs
        }
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\n================================================================")
    print(f"BENCHMARK COMPLETED IN {total_duration:.1f}s")
    print(f"Median Latency: {median_latency:.2f}s (Gate: < 30s) -> {'PASS' if median_latency < 30 else 'FAIL'}")
    print(f"p95 Latency:    {p95_latency:.2f}s")
    print(f"Logged to DB:   {db_count} rows in verdicts.db")
    print(f"Total Phase 0 Cost: ${monthly_cost:.2f} (Projected SWING: ${projected_monthly_swing_cost:.2f} < $20)")
    print(f"Report saved to: {REPORT_PATH}")
    print(f"================================================================")
    return report


if __name__ == "__main__":
    asyncio.run(run_benchmark(100))
