# aurono/projections/store_sqlite.py

import sqlite3
from pathlib import Path
from typing import Dict, Any

from aurono.db.connect import connect as db_connect


# ============================================================
# Schema
# ============================================================

def init_projection_store(db_path: Path) -> None:
    conn = db_connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_state_projection (
                strategy_id        TEXT PRIMARY KEY,
                active_version_id  TEXT,
                status             TEXT NOT NULL,
                last_evaluated_at  TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS capital_balance_projection (
                strategy_id  TEXT NOT NULL,
                currency     TEXT NOT NULL,
                available    REAL NOT NULL,
                reserved     REAL NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (strategy_id, currency)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_balance_projection (
                strategy_id  TEXT NOT NULL,
                asset        TEXT NOT NULL,
                quantity     REAL NOT NULL,
                reserved     REAL NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (strategy_id, asset)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_state_projection (
                trade_id            TEXT PRIMARY KEY,
                strategy_id         TEXT NOT NULL,
                strategy_version_id TEXT NOT NULL,
                symbol              TEXT NOT NULL,
                exchange            TEXT,
                side                TEXT NOT NULL,
                state               TEXT NOT NULL,
                external_order_id   TEXT,
                filled_quantity     REAL NOT NULL,
                avg_price           REAL NOT NULL,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshot_projection (
                strategy_id          TEXT NOT NULL,
                symbol               TEXT NOT NULL,
                timeframe            TEXT NOT NULL,
                timestamp_utc        TEXT NOT NULL,
                action               TEXT,
                reason_code          TEXT,
                close_price          REAL,
                free_eur             REAL,
                asset_units          REAL,
                acb_price            REAL,
                reserved_eur         REAL DEFAULT 0,
                reserved_units       REAL DEFAULT 0,
                portfolio_value_eur  REAL,
                unrealized_pnl_eur   REAL,
                PRIMARY KEY (strategy_id, timestamp_utc)
            )
            """
        )

        # Migration: add reserved columns if missing (existing DBs)
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(portfolio_snapshot_projection)")
        }
        if "reserved_eur" not in existing_cols:
            conn.execute("ALTER TABLE portfolio_snapshot_projection ADD COLUMN reserved_eur REAL DEFAULT 0")
        if "reserved_units" not in existing_cols:
            conn.execute("ALTER TABLE portfolio_snapshot_projection ADD COLUMN reserved_units REAL DEFAULT 0")

        conn.commit()
    finally:
        conn.close()


# ============================================================
# Upserts
# ============================================================

def upsert_strategy_state(db_path: Path, row: Dict[str, Any]) -> None:
    _upsert(
        db_path,
        """
        INSERT INTO strategy_state_projection
        VALUES (:strategy_id, :active_version_id, :status, :last_evaluated_at)
        ON CONFLICT(strategy_id) DO UPDATE SET
            active_version_id=excluded.active_version_id,
            status=excluded.status,
            last_evaluated_at=excluded.last_evaluated_at
        """,
        row,
    )


def upsert_capital_balance(db_path: Path, row: Dict[str, Any]) -> None:
    _upsert(
        db_path,
        """
        INSERT INTO capital_balance_projection
        VALUES (:strategy_id, :currency, :available, :reserved, :updated_at)
        ON CONFLICT(strategy_id, currency) DO UPDATE SET
            available=excluded.available,
            reserved=excluded.reserved,
            updated_at=excluded.updated_at
        """,
        row,
    )


def upsert_inventory_balance(db_path: Path, row: Dict[str, Any]) -> None:
    _upsert(
        db_path,
        """
        INSERT INTO inventory_balance_projection
        VALUES (:strategy_id, :asset, :quantity, :reserved, :updated_at)
        ON CONFLICT(strategy_id, asset) DO UPDATE SET
            quantity=excluded.quantity,
            reserved=excluded.reserved,
            updated_at=excluded.updated_at
        """,
        row,
    )


def upsert_trade_state(db_path: Path, row: Dict[str, Any]) -> None:
    _upsert(
        db_path,
        """
        INSERT INTO trade_state_projection
        VALUES (
            :trade_id, :strategy_id, :strategy_version_id, :symbol,
            :exchange, :side, :state, :external_order_id,
            :filled_quantity, :avg_price, :created_at, :updated_at
        )
        ON CONFLICT(trade_id) DO UPDATE SET
            state=excluded.state,
            external_order_id=excluded.external_order_id,
            filled_quantity=excluded.filled_quantity,
            avg_price=excluded.avg_price,
            updated_at=excluded.updated_at
        """,
        row,
    )


def upsert_portfolio_snapshot(db_path: Path, row: Dict[str, Any]) -> None:
    _upsert(
        db_path,
        """
        INSERT INTO portfolio_snapshot_projection
        (strategy_id, symbol, timeframe, timestamp_utc,
         action, reason_code, close_price,
         free_eur, asset_units, acb_price,
         reserved_eur, reserved_units,
         portfolio_value_eur, unrealized_pnl_eur)
        VALUES (
            :strategy_id, :symbol, :timeframe, :timestamp_utc,
            :action, :reason_code, :close_price,
            :free_eur, :asset_units, :acb_price,
            :reserved_eur, :reserved_units,
            :portfolio_value_eur, :unrealized_pnl_eur
        )
        ON CONFLICT(strategy_id, timestamp_utc) DO UPDATE SET
            symbol=excluded.symbol,
            timeframe=excluded.timeframe,
            action=excluded.action,
            reason_code=excluded.reason_code,
            close_price=excluded.close_price,
            free_eur=excluded.free_eur,
            asset_units=excluded.asset_units,
            acb_price=excluded.acb_price,
            reserved_eur=excluded.reserved_eur,
            reserved_units=excluded.reserved_units,
            portfolio_value_eur=excluded.portfolio_value_eur,
            unrealized_pnl_eur=excluded.unrealized_pnl_eur
        """,
        row,
    )


def _upsert(db_path: Path, sql: str, row: Dict[str, Any]) -> None:
    from decimal import Decimal
    coerced = {
        k: float(v) if isinstance(v, Decimal) else v
        for k, v in row.items()
    }
    conn = db_connect(db_path)
    try:
        conn.execute(sql, coerced)
        conn.commit()
    finally:
        conn.close()


# ============================================================
# Load helpers (for incremental projector)
# ============================================================

def load_strategy_state(db_path: Path, strategy_id: str):
    return _load_one(
        db_path,
        "SELECT * FROM strategy_state_projection WHERE strategy_id=?",
        (strategy_id,),
    )


def load_capital_balance(db_path: Path, strategy_id: str, currency: str):
    return _load_one(
        db_path,
        """
        SELECT * FROM capital_balance_projection
        WHERE strategy_id=? AND currency=?
        """,
        (strategy_id, currency),
    )


def load_inventory_balance(db_path: Path, strategy_id: str, asset: str):
    return _load_one(
        db_path,
        """
        SELECT * FROM inventory_balance_projection
        WHERE strategy_id=? AND asset=?
        """,
        (strategy_id, asset),
    )


def load_trade_state(db_path: Path, trade_id: str):
    return _load_one(
        db_path,
        "SELECT * FROM trade_state_projection WHERE trade_id=?",
        (trade_id,),
    )


def load_portfolio_snapshots(db_path: Path, strategy_id: str):
    """Load all snapshots for a strategy, ordered by time."""
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT * FROM portfolio_snapshot_projection
            WHERE strategy_id=?
            ORDER BY timestamp_utc
            """,
            (strategy_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _load_one(db_path: Path, sql: str, params: tuple):
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
