import logging
import time
from typing import Dict, Any, Optional
from app.core.config import settings
from app.services.mock_jev import mock_jev_adapter
from app.services.fail_closed import check_jev_fallback
from app.core.verdicts_db import verdicts_db

logger = logging.getLogger(__name__)


class TypeSafeService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.TYPESAFE_API_KEY
        # Phase 0-2: ENABLE_JEV is strictly False by default. Zero real Jev credits burned without explicit opt-in.
        self.enable_jev = getattr(settings, "ENABLE_JEV", False)

    async def evaluate_state(
        self,
        compact_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Phase 2 TypeSafe Adapter.
        - By default (ENABLE_JEV=False), uses mock_jev_adapter() for zero cost.
        - When ENABLE_JEV=True:
          1. Enforces cost guardrail ($20 hard block, $16 warning).
          2. Limits to exactly 2 questions: TradeAction and DataQuality.
          3. Passes ONLY compact categorical state (saving tokens, zero raw numbers).
          4. Verifies response against check_jev_fallback().
          5. Fails closed on any API, auth, or network failure.
        """
        if not self.enable_jev:
            logger.info("Using mock_jev_adapter() (ENABLE_JEV=False by default).")
            return mock_jev_adapter(compact_state)

        # 1. Budget Guardrail: Check monthly cumulative cost ($20 budget, $16 warning, $20 hard block)
        monthly_cost = verdicts_db.get_monthly_cumulative_cost()
        if monthly_cost >= settings.MONTHLY_BUDGET_USD:
            logger.error(
                f"Monthly cost guardrail breached (${monthly_cost:.2f} >= ${settings.MONTHLY_BUDGET_USD:.2f}). "
                "Hard-blocking external Jev API call to prevent credit exhaustion."
            )
            return {
                "TradeAction": {"choice": "ABSTAIN", "confidence": 0.0, "probabilities": {}},
                "DataQuality": {"choice": "poor", "confidence": 0.0},
                "is_fallback": True,
                "model_ver": "cost-guardrail-blocked",
                "latency_ms": 0
            }
        elif monthly_cost >= 0.8 * settings.MONTHLY_BUDGET_USD:
            logger.warning(
                f"Cost warning: Monthly cumulative cost (${monthly_cost:.2f}) reached 80% of budget (${settings.MONTHLY_BUDGET_USD:.2f})."
            )

        # 2. Check API Key
        if not self.api_key or not self.api_key.strip():
            logger.warning("ENABLE_JEV is True but no TYPESAFE_API_KEY set. Triggering fail-closed fallback.")
            return {
                "TradeAction": {"choice": "ABSTAIN", "confidence": 0.0, "probabilities": {}},
                "DataQuality": {"choice": "poor", "confidence": 0.0},
                "is_fallback": True,
                "model_ver": "fallback-unconfigured",
                "latency_ms": 0
            }

        # 3. Real TypeSafe Jev API call: 2 questions max (TradeAction + DataQuality)
        start_t = time.time()
        try:
            from typesafe_sdk import AsyncTypeSafeClient, Choice

            questions = {
                "TradeAction": Choice(
                    instructions="Given the compact market, derivatives, and social sentiment state, select the immediate trade action for BTC/USD.",
                    criteria={
                        "BUY": "Favorable risk-to-reward long entry with positive upside expectation and structural alignment.",
                        "HOLD": "Holding existing position; trend remains intact but no new edge to scale.",
                        "SELL": "Exit long position / defensive de-risk.",
                        "ABSTAIN": "No actionable edge, mixed signals, elevated risk, or consolidating chop."
                    }
                ),
                "DataQuality": Choice(
                    instructions="Rate the overall consistency and reliability of the categorical input data.",
                    criteria={
                        "reliable": "Data is consistent across indicators with low contradiction.",
                        "mixed": "Some indicators conflict or show divergent regime transitions.",
                        "poor": "Extreme contradictions, stale components, or unavailable metrics."
                    }
                )
            }

            async with AsyncTypeSafeClient(api_key=self.api_key) as client:
                response = await client.system_one(state=compact_state, questions=questions)

            latency_ms = int((time.time() - start_t) * 1000)

            action_ans = response.answers.get("TradeAction")
            quality_ans = response.answers.get("DataQuality")

            raw_choice = getattr(action_ans, "choice", "ABSTAIN")
            raw_conf = float(getattr(action_ans, "confidence", 0.0))
            raw_probs = getattr(action_ans, "probabilities", {}) or {}

            probs_dict = {str(k): float(v) for k, v in raw_probs.items()} if isinstance(raw_probs, dict) else {}

            qual_choice = getattr(quality_ans, "choice", "mixed")
            qual_conf = float(getattr(quality_ans, "confidence", 0.5))

            model_version = getattr(response, "model", "typesafe-jev-v8")

            jev_result = {
                "TradeAction": {
                    "choice": raw_choice,
                    "confidence": raw_conf,
                    "probabilities": probs_dict
                },
                "DataQuality": {
                    "choice": qual_choice,
                    "confidence": qual_conf
                },
                "is_fallback": False,
                "model_ver": str(model_version),
                "latency_ms": latency_ms
            }

            # 4. Fallback verification: Run check_jev_fallback() to prevent synthetic/pseudo-precision contamination
            is_valid, fb_reason = check_jev_fallback(jev_result)
            if not is_valid:
                logger.error(f"Jev returned fixed/synthetic fallback response ({fb_reason})! Flagging fail-closed.")
                jev_result["is_fallback"] = True

            return jev_result

        except Exception as e:
            latency_ms = int((time.time() - start_t) * 1000)
            logger.error(f"Error invoking TypeSafe Jev API: {e}", exc_info=True)
            return {
                "TradeAction": {"choice": "ABSTAIN", "confidence": 0.0, "probabilities": {}},
                "DataQuality": {"choice": "poor", "confidence": 0.0},
                "is_fallback": True,
                "model_ver": "typesafe-error",
                "latency_ms": latency_ms
            }


typesafe_service = TypeSafeService()
