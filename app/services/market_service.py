import ccxt.async_support as ccxt
import logging
from typing import Dict, Any, List, Optional, Tuple
import time
import datetime
from app.services.fail_closed import MarketSnapshot, PRIMARY_VENUE, PAIR

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 3600,
    "1d": 24 * 3600
}


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI) across price series.
    Returns None if history is insufficient (< period + 1). Never returns 50.0 default.
    """
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def filter_closed_candles(raw_candles: List[List[float]], timeframe: str, now_ts: float) -> List[Dict[str, Any]]:
    """
    Enforces no-lookahead bias by strictly discarding forming (unclosed) candles.
    Guarantees that only completely finished candles are evaluated.
    Chief Engineer Phase 2 Tightening:
    - Slice off forming candle with raw_candles[:-1].
    - Explicitly verify candle_close_ts <= now_ts (where candle_close_ts = candle_open_ts + timeframe_seconds).
    """
    timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
    closed = []
    for c in raw_candles or []:
        candle_open_ts = int(c[0] // 1000)
        candle_close_ts = candle_open_ts + timeframe_seconds
        # Enforce closed candle: close_ts <= now_ts (forming candles have close_ts > now_ts)
        if candle_close_ts <= now_ts:
            closed.append({
                "time": candle_open_ts,
                "close_time": candle_close_ts,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })
    return closed


class MarketService:
    def __init__(self, timeout_ms: int = 10000):
        self.timeout_ms = timeout_ms

    async def fetch_market_snapshot(
        self,
        symbol: str = "BTC",
        profile: str = "SWING"
    ) -> Tuple[MarketSnapshot, Dict[str, Any]]:
        """
        Fetches live Kraken spot market data, closed OHLCV candles, and perpetual funding rate.
        Enforces:
        - Closed candles only (discards forming candles)
        - Multi-timeframe warmup history (4h, 1d, 1h for SWING; 15m, 1h, 4h for DAY_CONTEXT)
        - Real staleness counters
        - Zero synthetic fallback data (fails closed on error)
        """
        sym = symbol.upper().replace("$", "")
        pair = f"{sym}/USD"
        fetch_start_ts = time.time()
        fetch_start_dt = datetime.datetime.fromtimestamp(fetch_start_ts, datetime.timezone.utc)

        exchange = ccxt.kraken({"enableRateLimit": True, "timeout": self.timeout_ms})
        futures_exchange = ccxt.krakenfutures({"enableRateLimit": True, "timeout": self.timeout_ms})

        try:
            # 1. Fetch Spot Ticker
            ticker = await exchange.fetch_ticker(pair)
            price = float(ticker.get("last") or ticker.get("close") or 0.0)
            ticker_ts = ticker.get("timestamp")

            # 2. Fetch Multi-Timeframe OHLCV Candles with full warmup
            if profile == "SWING":
                raw_4h = await exchange.fetch_ohlcv(pair, timeframe="4h", limit=350)
                raw_1d = await exchange.fetch_ohlcv(pair, timeframe="1d", limit=300)
                raw_1h = await exchange.fetch_ohlcv(pair, timeframe="1h", limit=300)
                raw_15m = []
            else:  # DAY_CONTEXT
                raw_15m = await exchange.fetch_ohlcv(pair, timeframe="15m", limit=350)
                raw_1h = await exchange.fetch_ohlcv(pair, timeframe="1h", limit=300)
                raw_4h = await exchange.fetch_ohlcv(pair, timeframe="4h", limit=300)
                raw_1d = await exchange.fetch_ohlcv(pair, timeframe="1d", limit=300)

            # Filter closed candles only
            candles_4h = filter_closed_candles(raw_4h, "4h", fetch_start_ts)
            candles_1d = filter_closed_candles(raw_1d, "1d", fetch_start_ts)
            candles_1h = filter_closed_candles(raw_1h, "1h", fetch_start_ts)
            candles_15m = filter_closed_candles(raw_15m, "15m", fetch_start_ts)

            # Primary candle close timestamp
            primary_candles = candles_4h if profile == "SWING" else candles_15m
            if primary_candles:
                last_candle_close_epoch = primary_candles[-1]["close_time"]
                last_candle_close_dt = datetime.datetime.fromtimestamp(
                    last_candle_close_epoch, datetime.timezone.utc
                )
            else:
                last_candle_close_dt = fetch_start_dt

            # 3. Fetch Real Perpetuals Funding Rate & Open Interest
            funding_rate_pct = None
            open_interest_usd = None
            freshness_funding_s = 0.0

            try:
                futures_pair = f"{sym}/USD:USD"
                fr_info = await futures_exchange.fetch_funding_rate(futures_pair)
                funding_received_ts = time.time()
                raw_rate = fr_info.get("fundingRate")
                if raw_rate is not None:
                    # Kraken Futures raw funding rate is 1-hour interval ('interval': '1h').
                    # Standardize to 8-hour rate (industry standard for perpetual funding percentage)
                    funding_rate_pct = float(raw_rate) * 8.0 * 100.0

                # ccxt derives `timestamp` from Kraken Futures `info.lastTime` (last trade on the
                # perpetual ticker) - a genuine exchange-side liveness timestamp for this feed.
                # Age is measured at the moment the response is received. (Measuring against
                # fetch_start_ts - captured before the spot/OHLCV calls - made the newer tick go
                # negative and get clamped to a fake 0.0s.)
                fr_ts = fr_info.get("timestamp")
                if fr_ts:
                    freshness_funding_s = max(0.0, funding_received_ts - (fr_ts / 1000.0))
                else:
                    # Fail-closed: missing exchange timestamp must be treated as stale, never 0.0s fresh
                    freshness_funding_s = 999999.0

                info_block = fr_info.get("info", {})
                raw_oi = info_block.get("openInterest")
                if raw_oi is not None:
                    # In Kraken Futures BTC/USD:USD, raw openInterest is denominated in BTC base units.
                    # Multiplying by spot price yields total USD notional open interest.
                    oi_val = float(raw_oi)
                    open_interest_usd = round(oi_val * price, 2) if price > 0 else round(oi_val, 2)
            except Exception as fe:
                logger.warning(f"Kraken Futures fetch failed for {sym}: {fe}")
                freshness_funding_s = 999999.0

            fetch_end_ts = time.time()
            ticker_age_s = max(0.0, fetch_end_ts - (ticker_ts / 1000.0)) if ticker_ts else (fetch_end_ts - fetch_start_ts)
            # Freshness of the captured market quote at the conclusion of fetch
            freshness_market_s = round(max(ticker_age_s, fetch_end_ts - fetch_start_ts), 2)

            snapshot = MarketSnapshot(
                venue=PRIMARY_VENUE,
                pair=pair,
                last_price=price,
                last_candle_close_ts=last_candle_close_dt,
                freshness_market_s=freshness_market_s,
                freshness_funding_s=round(freshness_funding_s, 2),
                is_fallback=False,
                is_synthetic=False,
                candle_count_15m=len(candles_15m),
                candle_count_1h=len(candles_1h),
                candle_count_4h=len(candles_4h),
                candle_count_1d=len(candles_1d),
                profile=profile
            )

            raw_payload = {
                "venue": PRIMARY_VENUE,
                "pair": pair,
                "price": price,
                "last_candle_close_ts": last_candle_close_dt.isoformat(),
                "freshness_market_s": freshness_market_s,
                "freshness_funding_s": round(freshness_funding_s, 2),
                "funding_rate_pct": funding_rate_pct,
                "open_interest_usd": open_interest_usd,
                "candle_count_15m": len(candles_15m),
                "candle_count_1h": len(candles_1h),
                "candle_count_4h": len(candles_4h),
                "candle_count_1d": len(candles_1d),
                "candles_4h": candles_4h,
                "candles_1h": candles_1h,
                "candles_1d": candles_1d,
                "candles_15m": candles_15m,
                "is_fallback": False,
                "is_synthetic": False,
                "fetched_at": int(fetch_start_ts)
            }

            return snapshot, raw_payload

        except Exception as e:
            logger.error(f"CCXT Kraken live fetch failed for {pair}: {e}. Triggering fail-closed fallback flag.")
            fallback_snapshot = MarketSnapshot(
                venue=PRIMARY_VENUE,
                pair=pair,
                last_price=0.0,
                last_candle_close_ts=fetch_start_dt,
                freshness_market_s=999999.0,
                freshness_funding_s=999999.0,
                is_fallback=True,
                is_synthetic=False,
                candle_count_15m=0,
                candle_count_1h=0,
                candle_count_4h=0,
                candle_count_1d=0,
                profile=profile
            )
            fallback_payload = {
                "venue": PRIMARY_VENUE,
                "pair": pair,
                "price": 0.0,
                "last_candle_close_ts": fetch_start_dt.isoformat(),
                "freshness_market_s": 999999.0,
                "freshness_funding_s": 999999.0,
                "funding_rate_pct": None,
                "open_interest_usd": None,
                "candle_count_15m": 0,
                "candle_count_1h": 0,
                "candle_count_4h": 0,
                "candle_count_1d": 0,
                "candles_4h": [],
                "candles_1h": [],
                "candles_1d": [],
                "candles_15m": [],
                "is_fallback": True,
                "is_synthetic": False,
                "error": str(e),
                "fetched_at": int(fetch_start_ts)
            }
            return fallback_snapshot, fallback_payload

        finally:
            await exchange.close()
            await futures_exchange.close()


market_service = MarketService()
