# aurono/projections/incremental.py

from typing import Dict, Any, Tuple
from pathlib import Path

from aurono.projections.event_groups import (
    INVENTORY_BALANCE_EVENTS,
    TRADE_LIFECYCLE_EVENTS,
    base_asset,
)
from aurono.projections.reducers import (
    reduce_strategy_state,
    reduce_capital_balance,
    reduce_inventory_balance,
    reduce_trade_state,
    reduce_portfolio_snapshot,
)
from aurono.projections.store_sqlite import (
    upsert_strategy_state,
    upsert_capital_balance,
    upsert_inventory_balance,
    upsert_trade_state,
    upsert_portfolio_snapshot,
)


# ============================================================
# Incremental Projection Application
# ============================================================

def apply_event_to_projections(
    *,
    projection_db_path: Path,
    event: Dict[str, Any],
) -> None:
    """
    Apply a single event to projections.

    Rules:
    - Must behave identically to replay reducer logic
    - Must not read ledger tables
    - Must not infer missing state
    """

    et = event["event_type"]

    # ---------------- strategy state ----------------
    if event.get("strategy_id") and et.startswith("Strategy"):
        sid = event["strategy_id"]
        prev = _load_strategy_state(projection_db_path, sid)
        next_state = reduce_strategy_state(prev, event)
        upsert_strategy_state(projection_db_path, next_state)

    # ---------------- capital ----------------
    if et.startswith("Capital") or et.startswith("Funds"):
        payload = event["payload"]
        currency = payload.get("currency", "EUR")
        key = (event["strategy_id"], currency)
        prev = _load_capital_balance(projection_db_path, key)
        next_state = reduce_capital_balance(prev, event)
        upsert_capital_balance(projection_db_path, next_state)

    # ---------------- inventory ----------------
    if et in INVENTORY_BALANCE_EVENTS:
        # Inventory projection uses base asset (e.g. "BTC"), not pair ("BTC-EUR").
        # The reducer normalises too, so no need to rewrite the event here.
        key = (event["strategy_id"], base_asset(event.get("symbol")))
        prev = _load_inventory_balance(projection_db_path, key)
        next_state = reduce_inventory_balance(prev, event)
        upsert_inventory_balance(projection_db_path, next_state)

    # ---------------- trade ----------------
    if event.get("trade_id") and et in TRADE_LIFECYCLE_EVENTS:
        tid = event["trade_id"]
        prev = _load_trade_state(projection_db_path, tid)
        next_state = reduce_trade_state(prev, event)
        upsert_trade_state(projection_db_path, next_state)

    # ---------------- portfolio snapshot ----------------
    if et == "DecisionObserved":
        snapshot = reduce_portfolio_snapshot(None, event)
        upsert_portfolio_snapshot(projection_db_path, snapshot)

    # Capital events create a snapshot so deposits/withdrawals are immediately
    # visible in the graph (instead of waiting for the next evaluation).
    if et in ("CapitalCredited", "CapitalDebited"):
        snapshot = _capital_event_snapshot(projection_db_path, event)
        if snapshot:
            upsert_portfolio_snapshot(projection_db_path, snapshot)


# ============================================================
# Capital-event snapshot helper
# ============================================================

def _capital_event_snapshot(
    db_path: Path, event: Dict[str, Any]
) -> Dict[str, Any] | None:
    """
    Build a portfolio snapshot triggered by CapitalCredited/CapitalDebited.

    Uses the last known snapshot for this strategy to get close_price, asset state,
    and reserved amounts, then adjusts free_eur based on the capital event.
    Returns None if no prior snapshot exists (nothing to base the snapshot on).
    """
    from decimal import Decimal
    from aurono.projections.store_sqlite import load_portfolio_snapshots

    strategy_id = event["strategy_id"]
    snapshots = load_portfolio_snapshots(db_path, strategy_id)
    amount = Decimal(str(event["payload"]["amount"]))

    if not snapshots:
        # New strategy — no prior snapshot. Create one from just the capital.
        # symbol/timeframe come from the strategy version if available.
        symbol, timeframe = _lookup_strategy_symbol_timeframe(db_path, strategy_id)
        free_eur = amount if event["event_type"] == "CapitalCredited" else -amount
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": event["timestamp_utc"],
            "action": None,
            "reason_code": None,
            "close_price": Decimal("0"),
            "free_eur": free_eur,
            "asset_units": Decimal("0"),
            "acb_price": None,
            "reserved_eur": Decimal("0"),
            "reserved_units": Decimal("0"),
            "portfolio_value_eur": free_eur,
            "unrealized_pnl_eur": Decimal("0"),
        }

    last = snapshots[-1]
    close_price = Decimal(str(last.get("close_price", 0)))
    asset_units = Decimal(str(last.get("asset_units", 0)))
    acb_price_raw = last.get("acb_price")
    acb_price = Decimal(str(acb_price_raw)) if acb_price_raw else None
    reserved_eur = Decimal(str(last.get("reserved_eur", 0)))
    reserved_units = Decimal(str(last.get("reserved_units", 0)))

    # Compute new free_eur from last snapshot + capital event
    prev_free = Decimal(str(last.get("free_eur", 0)))
    if event["event_type"] == "CapitalCredited":
        free_eur = prev_free + amount
    else:
        free_eur = prev_free - amount

    total_eur = free_eur + reserved_eur
    total_units = asset_units + reserved_units
    portfolio_val = total_eur + total_units * close_price
    unrealized = (
        total_units * (close_price - acb_price)
        if acb_price and total_units > 0
        else Decimal("0")
    )

    return {
        "strategy_id": strategy_id,
        "symbol": last.get("symbol", ""),
        "timeframe": last.get("timeframe", ""),
        "timestamp_utc": event["timestamp_utc"],
        "action": None,
        "reason_code": None,
        "close_price": close_price,
        "free_eur": free_eur,
        "asset_units": asset_units,
        "acb_price": acb_price,
        "reserved_eur": reserved_eur,
        "reserved_units": reserved_units,
        "portfolio_value_eur": portfolio_val,
        "unrealized_pnl_eur": unrealized,
    }


def _lookup_strategy_symbol_timeframe(db_path: Path, strategy_id: str) -> tuple:
    """Look up symbol and timeframe from the strategy version parameters."""
    import json
    import sqlite3
    from aurono.db.connect import connect as db_connect
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT sv.parameters_json FROM strategy_version sv
            JOIN strategy_state_projection ssp ON sv.strategy_version_id = ssp.active_version_id
            WHERE ssp.strategy_id = ?
            """,
            (strategy_id,),
        ).fetchone()
        if row:
            params = json.loads(row["parameters_json"])
            return params.get("symbol", ""), params.get("timeframe", "")
        return "", ""
    except sqlite3.OperationalError:
        # Identity tables may not exist (e.g. projection-only DB)
        return "", ""
    finally:
        conn.close()


# ============================================================
# Load helpers (projection reads only)
# ============================================================

def _coerce_decimals(row: Dict[str, Any] | None, fields: list) -> Dict[str, Any] | None:
    """Coerce float fields back to Decimal after SQLite load."""
    if row is None:
        return None
    from decimal import Decimal
    for f in fields:
        if f in row and isinstance(row[f], float):
            row[f] = Decimal(str(row[f]))
    return row


def _load_strategy_state(db_path: Path, strategy_id: str) -> Dict[str, Any] | None:
    from aurono.projections.store_sqlite import load_strategy_state
    return load_strategy_state(db_path, strategy_id)


def _load_capital_balance(
    db_path: Path,
    key: Tuple[str, str],
) -> Dict[str, Any] | None:
    from aurono.projections.store_sqlite import load_capital_balance
    strategy_id, currency = key
    row = load_capital_balance(db_path, strategy_id, currency)
    return _coerce_decimals(row, ["available", "reserved"])


def _load_inventory_balance(
    db_path: Path,
    key: Tuple[str, str],
) -> Dict[str, Any] | None:
    from aurono.projections.store_sqlite import load_inventory_balance
    strategy_id, asset = key
    row = load_inventory_balance(db_path, strategy_id, asset)
    return _coerce_decimals(row, ["quantity", "reserved"])


def _load_trade_state(db_path: Path, trade_id: str) -> Dict[str, Any] | None:
    from aurono.projections.store_sqlite import load_trade_state
    row = load_trade_state(db_path, trade_id)
    return _coerce_decimals(row, ["filled_quantity", "avg_price"])
