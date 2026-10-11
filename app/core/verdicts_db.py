import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
import datetime

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "verdicts.db"


def resolve_db_path() -> Path:
    """
    Resolves the verdicts DB path. JEV_VERDICTS_DB_PATH overrides the default so that
    test suites and sandbox runs are hermetic and never mutate the committed verdicts.db.
    """
    override = os.environ.get("JEV_VERDICTS_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


class VerdictsDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else resolve_db_path()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency & reliability
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_db(self) -> None:
        """Initializes verdicts table with immutable log schema."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS verdicts (
                        run_id TEXT PRIMARY KEY,
                        state_hash TEXT NOT NULL,
                        ts_utc TEXT NOT NULL,
                        ts_local_myt TEXT NOT NULL,
                        profile TEXT NOT NULL,
                        rule_ver TEXT NOT NULL,
                        shadow_mode INTEGER NOT NULL,
                        final_verdict TEXT NOT NULL,
                        final_confidence_band TEXT,
                        final_confidence_raw REAL,
                        data_gates_passed INTEGER NOT NULL,
                        is_fallback INTEGER NOT NULL,
                        is_synthetic INTEGER NOT NULL,
                        market_venue TEXT NOT NULL,
                        market_pair TEXT NOT NULL,
                        last_price REAL NOT NULL,
                        cost_total_usd REAL NOT NULL,
                        monthly_cumulative_usd REAL NOT NULL,
                        log_record_json TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_verdicts_ts ON verdicts (ts_utc DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_verdicts_state_hash ON verdicts (state_hash);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_verdicts_shadow ON verdicts (shadow_mode);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS verdict_outcomes (
                        outcome_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL UNIQUE,
                        recorded_at_utc TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        entry_price REAL,
                        exit_price REAL,
                        pnl_percent REAL,
                        r_multiple REAL,
                        hit_target INTEGER,
                        hit_stop INTEGER,
                        notes TEXT,
                        FOREIGN KEY (run_id) REFERENCES verdicts (run_id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_run_id ON verdict_outcomes (run_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON verdict_outcomes (outcome);")
        finally:
            conn.close()

    def get_monthly_cumulative_cost(self, year_month: Optional[str] = None) -> float:
        """Calculates running sum of total_cost_usd for the current calendar month."""
        if not year_month:
            year_month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(cost_total_usd) as total FROM verdicts WHERE ts_utc LIKE ?",
                (f"{year_month}%",)
            )
            row = cursor.fetchone()
            return float(row["total"] or 0.0) if row else 0.0
        finally:
            conn.close()

    def log_verdict(self, record: Dict[str, Any], state_hash_val: str) -> None:
        """
        Logs one immutable row conforming to v8_verdict_log schema.
        Calculates and stores running monthly_cumulative_usd.
        """
        run_id = record["run_id"]
        ts_utc = record["ts_utc"]
        ts_local_myt = record.get("ts_local_myt", "")
        profile = record["profile"]
        rule_ver = record["rule_ver"]
        shadow_mode = 1 if record.get("shadow_mode", True) else 0

        market = record.get("market", {})
        venue = market.get("venue", "kraken")
        pair = market.get("pair", "BTC/USD")
        last_price = float(market.get("last_price", 0.0))
        is_fallback = 1 if market.get("is_fallback") else 0
        is_synthetic = 1 if market.get("is_synthetic") else 0

        det = record.get("deterministic", {})
        final_verdict = det.get("final_verdict", "NO_VERDICT")
        final_confidence_band = det.get("final_confidence_band")
        final_confidence_raw = det.get("final_confidence_raw")
        data_gates_passed = 1 if det.get("data_gates_passed") else 0

        cost_obj = record.get("cost", {})
        cost_total = float(cost_obj.get("total_cost_usd", 0.0))

        # Calculate current running monthly cumulative cost
        ym = ts_utc[:7] if len(ts_utc) >= 7 else None
        prev_monthly = self.get_monthly_cumulative_cost(ym)
        monthly_cumulative = round(prev_monthly + cost_total, 4)

        # Update record's cost block before storing
        cost_obj["monthly_cumulative_usd"] = monthly_cumulative
        record["cost"] = cost_obj

        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO verdicts (
                        run_id, state_hash, ts_utc, ts_local_myt, profile, rule_ver,
                        shadow_mode, final_verdict, final_confidence_band, final_confidence_raw,
                        data_gates_passed, is_fallback, is_synthetic, market_venue, market_pair,
                        last_price, cost_total_usd, monthly_cumulative_usd, log_record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(run_id),
                    str(state_hash_val),
                    str(ts_utc),
                    str(ts_local_myt),
                    str(profile),
                    str(rule_ver),
                    shadow_mode,
                    str(final_verdict),
                    final_confidence_band,
                    final_confidence_raw,
                    data_gates_passed,
                    is_fallback,
                    is_synthetic,
                    str(venue),
                    str(pair),
                    last_price,
                    cost_total,
                    monthly_cumulative,
                    json.dumps(record)
                ))
        finally:
            conn.close()

    def get_verdict_count(self) -> int:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM verdicts")
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            conn.close()

    def get_recent_verdicts(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM verdicts ORDER BY ts_utc DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def record_outcome(
        self,
        run_id: str,
        outcome: str,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl_percent: Optional[float] = None,
        r_multiple: Optional[float] = None,
        hit_target: Optional[bool] = None,
        hit_stop: Optional[bool] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Records or updates a forward outcome for an existing verdict row.
        outcome: 'WIN' | 'LOSS' | 'BREAKEVEN' | 'EXPIRED' | 'ABSTAIN_OBSERVED'
        """
        import uuid
        valid_outcomes = {"WIN", "LOSS", "BREAKEVEN", "EXPIRED", "ABSTAIN_OBSERVED"}
        norm_outcome = outcome.strip().upper()
        if norm_outcome not in valid_outcomes:
            raise ValueError(f"Invalid outcome '{outcome}'. Must be one of {valid_outcomes}")

        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        outcome_id = f"out_{uuid.uuid4()}"

        conn = self._get_connection()
        try:
            with conn:
                # Verify run_id exists
                cursor = conn.cursor()
                cursor.execute("SELECT run_id, last_price, final_verdict FROM verdicts WHERE run_id = ?", (run_id,))
                verdict_row = cursor.fetchone()
                if not verdict_row:
                    raise KeyError(f"Verdict run_id '{run_id}' not found in database.")

                entry = entry_price if entry_price is not None else float(verdict_row["last_price"])
                ht = 1 if hit_target else (0 if hit_target is False else None)
                hs = 1 if hit_stop else (0 if hit_stop is False else None)

                conn.execute("""
                    INSERT INTO verdict_outcomes (
                        outcome_id, run_id, recorded_at_utc, outcome,
                        entry_price, exit_price, pnl_percent, r_multiple,
                        hit_target, hit_stop, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        recorded_at_utc = excluded.recorded_at_utc,
                        outcome = excluded.outcome,
                        entry_price = excluded.entry_price,
                        exit_price = excluded.exit_price,
                        pnl_percent = excluded.pnl_percent,
                        r_multiple = excluded.r_multiple,
                        hit_target = excluded.hit_target,
                        hit_stop = excluded.hit_stop,
                        notes = excluded.notes
                """, (
                    outcome_id, run_id, now_utc, norm_outcome,
                    entry, exit_price, pnl_percent, r_multiple,
                    ht, hs, notes or ""
                ))

            return {
                "outcome_id": outcome_id,
                "run_id": run_id,
                "recorded_at_utc": now_utc,
                "outcome": norm_outcome,
                "pnl_percent": pnl_percent,
                "r_multiple": r_multiple
            }
        finally:
            conn.close()

    def get_outcome(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves verified outcome for a specific run_id if recorded."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM verdict_outcomes WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_outcomes_count(self) -> int:
        """Returns total number of verified outcomes recorded."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM verdict_outcomes")
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            conn.close()

    def get_all_outcomes(self) -> List[Dict[str, Any]]:
        """Returns all recorded outcomes joined with their verdict details."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    o.outcome_id, o.run_id, o.recorded_at_utc, o.outcome,
                    o.entry_price, o.exit_price, o.pnl_percent, o.r_multiple,
                    o.hit_target, o.hit_stop, o.notes,
                    v.final_verdict, v.final_confidence_band, v.final_confidence_raw,
                    v.shadow_mode, v.ts_utc, v.profile
                FROM verdict_outcomes o
                JOIN verdicts v ON o.run_id = v.run_id
                ORDER BY o.recorded_at_utc DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_verdicts_with_outcomes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns recent verdicts with outcome status populated."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    v.run_id, v.ts_utc, v.profile, v.shadow_mode,
                    v.final_verdict, v.final_confidence_band, v.final_confidence_raw,
                    v.last_price, v.cost_total_usd,
                    o.outcome, o.pnl_percent, o.r_multiple, o.recorded_at_utc as outcome_recorded_at
                FROM verdicts v
                LEFT JOIN verdict_outcomes o ON v.run_id = o.run_id
                ORDER BY v.ts_utc DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def clear_outcomes(self) -> int:
        """Clears all records in verdict_outcomes table (for resetting calibration collection)."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM verdict_outcomes")
                return cursor.rowcount
        finally:
            conn.close()


verdicts_db = VerdictsDatabase()
