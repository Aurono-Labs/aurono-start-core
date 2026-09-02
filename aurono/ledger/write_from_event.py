# aurono/ledger/write_from_event.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from decimal import Decimal, InvalidOperation
import sqlite3
import uuid

from aurono.db.connect import connect as db_connect


# ============================================================
# Event classifications (LOCKED)
# ============================================================
# These maps define which event types affect which ledger and how.
# They are intentionally explicit and small. Additions should be deliberate.

# Capital ledger: signed EUR movements, plus a "kind" label
#
# `kind` semantics (LOCKED):
#   credit     — deposit (CapitalCredited) or sell proceeds (FundsReleased SELL)
#   debit      — buy-fill consumed cost (FundsReleased BUY); always carries trade_id
#   withdraw   — user withdrawal (CapitalDebited); affects free cash only, never position_cost_eur
#   reserve    — funds locked for an open trade
#   release    — reservation cleared (full or surplus return)
#   cost_basis — bootstrap-only seed of position_cost_eur (InventoryBootstrapped)
CAPITAL_EVENT_MAP: Dict[str, Tuple[str, int]] = {
    # ledger-level
    "CapitalCredited": ("credit", +1),
    "CapitalDebited": ("withdraw", -1),
    "CapitalReserved": ("reserve", -1),
    "CapitalReleased": ("release", +1),

    # execution-level
    "FundsReserved": ("reserve", -1),
    # NOTE: FundsReleased is handled specially in write_ledger_entries
    # (may produce 1-2 entries depending on consumed_eur).
}

# Inventory ledger: signed asset movements, plus a "kind" label
INVENTORY_EVENT_MAP: Dict[str, Tuple[str, int]] = {
    # ledger-level
    "InventoryIncreased": ("increase", +1),
    "InventoryDecreased": ("consume", +1),   # clears sell reservation (units consumed, not returned)
    "InventoryBootstrapped": ("bootstrap", +1),  # initial position setup

    # execution-level
    "InventoryReserved": ("reserve", -1),
    "InventoryReleased": ("release", +1),
}


# ============================================================
# SQL (kept in one place)
# ============================================================

CAPITAL_LEDGER_INSERT_SQL = """
INSERT INTO capital_ledger (
    entry_id,
    strategy_id,
    currency,
    amount,
    kind,
    trade_id,
    event_id,
    timestamp_utc
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

INVENTORY_LEDGER_INSERT_SQL = """
INSERT INTO inventory_ledger (
    entry_id,
    strategy_id,
    asset,
    quantity,
    kind,
    trade_id,
    event_id,
    timestamp_utc
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


# ============================================================
# Public entry point
# ============================================================

def write_ledger_entries(event: Dict[str, Any], *, db_path: Path) -> None:
    """
    Append ledger entries derived from a single validated runtime event.

    Guarantees:
    - Never mutates existing rows
    - 0..N rows per event
    - Safe for replay and live operation

    Notes:
    - This function is intentionally strict: if an event type is mapped to a
      ledger but required fields are missing, it raises.
    """

    et = event.get("event_type")
    if not et:
        raise ValueError("Ledger write: event missing 'event_type'")

    if et == "FundsReleased":
        _write_funds_released(event, db_path=db_path)
    elif et == "InventoryBootstrapped":
        _write_inventory_bootstrapped(event, db_path=db_path)
    elif et in CAPITAL_EVENT_MAP:
        params = _capital_ledger_params(event)
        _execute_one(db_path, CAPITAL_LEDGER_INSERT_SQL, params)

    if et in INVENTORY_EVENT_MAP:
        params = _inventory_ledger_params(event)
        _execute_one(db_path, INVENTORY_LEDGER_INSERT_SQL, params)


# ============================================================
# FundsReleased: special multi-entry handler
# ============================================================

def _write_funds_released(event: Dict[str, Any], *, db_path: Path) -> None:
    """
    FundsReleased settlement writes 1-2 capital ledger entries.

    BUY fill (consumed > 0):
      1. "release" for (consumed + released) → clears full reservation
      2. "debit" for consumed → removes spent EUR from free, tracks cost basis
      Net: free += released only, reservation clears fully

    BUY cancel (consumed == 0):
      1. "release" for released → clears reservation, restores free

    SELL proceeds (consumed == 0, no buy reservation):
      1. "credit" for released → adds proceeds to free
    """
    payload = _require_payload(event)

    released = _to_decimal(payload.get("released_eur", "0"), field="FundsReleased.released_eur")
    consumed = _to_decimal(payload.get("consumed_eur", "0"), field="FundsReleased.consumed_eur")

    side = str(event.get("side", "")).upper()
    strategy_id = _require_str(event, "strategy_id")
    trade_id = event.get("trade_id")
    event_id = _require_str(event, "event_id")
    timestamp = _require_str(event, "timestamp_utc")

    if side == "BUY":
        # Entry 1: release full reservation (consumed + released)
        total_release = consumed + released
        if total_release > 0:
            _execute_one(db_path, CAPITAL_LEDGER_INSERT_SQL, (
                _new_id(), strategy_id, "EUR",
                float(total_release), "release",
                trade_id, event_id, timestamp,
            ))
        # Entry 2: debit consumed EUR (cost of fill)
        if consumed > 0:
            _execute_one(db_path, CAPITAL_LEDGER_INSERT_SQL, (
                _new_id(), strategy_id, "EUR",
                float(-consumed), "debit",
                trade_id, event_id, timestamp,
            ))
    else:
        # SELL: proceeds are income (credit to free)
        if released > 0:
            _execute_one(db_path, CAPITAL_LEDGER_INSERT_SQL, (
                _new_id(), strategy_id, "EUR",
                float(released), "credit",
                trade_id, event_id, timestamp,
            ))


# ============================================================
# InventoryBootstrapped: inventory entry + cost_basis capital entry
# ============================================================

def _write_inventory_bootstrapped(event: Dict[str, Any], *, db_path: Path) -> None:
    """
    InventoryBootstrapped writes a cost_basis capital entry alongside the
    inventory entry (handled by the generic INVENTORY_EVENT_MAP path).

    The cost_basis entry sets position_cost_eur = initial_units * acb_price
    without affecting free_eur.
    """
    payload = _require_payload(event)

    initial_units = _to_decimal(
        payload.get("initial_units", "0"),
        field="InventoryBootstrapped.initial_units",
    )
    acb_price = _to_decimal(
        payload.get("acb_price", "0"),
        field="InventoryBootstrapped.acb_price",
    )

    if initial_units > 0 and acb_price > 0:
        cost_basis = initial_units * acb_price
        _execute_one(db_path, CAPITAL_LEDGER_INSERT_SQL, (
            _new_id(),
            _require_str(event, "strategy_id"),
            "EUR",
            float(cost_basis),
            "cost_basis",
            None,  # no trade_id for bootstrap
            _require_str(event, "event_id"),
            _require_str(event, "timestamp_utc"),
        ))


# ============================================================
# Normalization: event → insert params
# ============================================================
def _capital_ledger_params(event: Dict[str, Any]) -> Tuple[str, str, str, float, str, Optional[str], str, str]:
    kind, sign = CAPITAL_EVENT_MAP[event["event_type"]]
    payload = _require_payload(event)

    # Support both legacy ledger-level events and execution-level events.
    # - ledger-level: payload.amount (+ payload.currency)
    # - execution-level reserve: payload.reserved_eur
    # - execution-level release: payload.released_eur (surplus refunded to free)
    # NOTE: Use explicit None checks — Decimal("0") is falsy and breaks `or`.
    raw_amount = payload.get("amount")
    if raw_amount is None:
        raw_amount = payload.get("reserved_eur")
    if raw_amount is None:
        raw_amount = payload.get("released_eur")

    if raw_amount is None:
        raise ValueError(f"Capital event missing amount fields: {event['event_type']}")

    amount = _to_decimal(
        raw_amount,
        field=f"{event['event_type']}.amount/reserved_eur/released_eur",
    ) * Decimal(sign)

    currency = str(payload.get("currency", "EUR"))

    if currency != "EUR":
        raise ValueError(
            f"Capital ledger: currency must be 'EUR', got '{currency}' "
            f"(event: {event['event_type']})"
        )

    return (
        _new_id(),
        _require_str(event, "strategy_id"),
        currency,
        float(amount),
        kind,
        event.get("trade_id"),
        _require_str(event, "event_id"),
        _require_str(event, "timestamp_utc"),
    )

# ============================================================
# Base-asset extractor helper
# ============================================================

def _base_asset_from_symbol(symbol: str) -> str:
    """
    Extract base asset from a trading symbol.

    Examples:
    - BTC-EUR  -> BTC
    - ETH-USD  -> ETH
    - SOL-USDC -> SOL
    """
    if "-" in symbol:
        return symbol.split("-", 1)[0]
    return symbol

def _inventory_ledger_params(event: Dict[str, Any]) -> Tuple[str, str, str, float, str, Optional[str], str, str]:
    kind, sign = INVENTORY_EVENT_MAP[event["event_type"]]
    payload = _require_payload(event)

    # Support both legacy ledger-level events and execution-level events.
    # - ledger-level: payload.quantity
    # - execution-level: payload.reserved_units / payload.released_units
    # NOTE: Use explicit None checks — Decimal("0") is falsy and breaks `or`.
    raw_qty = payload.get("quantity")
    if raw_qty is None:
        raw_qty = payload.get("reserved_units")
    if raw_qty is None:
        raw_qty = payload.get("released_units")
    if raw_qty is None:
        raw_qty = payload.get("initial_units")

    if raw_qty is None:
        raise ValueError(f"Inventory event missing quantity fields: {event['event_type']}")

    qty = _to_decimal(
        raw_qty,
        field=f"{event['event_type']}.quantity/reserved_units/released_units",
    ) * Decimal(sign)

    # Asset symbol is stored on the runtime event dict (top-level).
    symbol = _require_str(event, "symbol")

    if "-" not in symbol:
        raise ValueError(
            f"Inventory ledger: symbol must contain '-' (e.g. 'BTC-EUR'), "
            f"got '{symbol}' (event: {event['event_type']})"
        )

    asset = _base_asset_from_symbol(symbol)

    return (
        _new_id(),
        _require_str(event, "strategy_id"),
        asset,
        float(qty),
        kind,
        event.get("trade_id"),
        _require_str(event, "event_id"),
        _require_str(event, "timestamp_utc"),
    )


# ============================================================
# SQLite execution helper
# ============================================================

def _execute_one(db_path: Path, sql: str, params: tuple) -> None:
    conn = db_connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# Small validation + conversion helpers
# ============================================================

def _require_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"Ledger write: event missing dict 'payload' ({event.get('event_type')})")
    return payload


def _require_str(event: Dict[str, Any], key: str) -> str:
    v = event.get(key)
    if v is None:
        raise ValueError(f"Ledger write: event missing '{key}' ({event.get('event_type')})")
    return str(v)


def _to_decimal(value: Any, *, field: str) -> Decimal:
    try:
        # Avoid Decimal(float) pitfalls by stringifying
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"Ledger write: invalid decimal for {field}: {value!r}") from e


def _new_id() -> str:
    return str(uuid.uuid4())


