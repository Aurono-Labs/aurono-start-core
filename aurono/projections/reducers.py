# aurono/projections/reducers.py

from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

from aurono.projections.event_groups import base_asset


# ============================================================
# Strategy State Reducer
# ============================================================

def reduce_strategy_state(state: Dict[str, Any] | None, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    state shape:
    {
        strategy_id: str,
        active_version_id: Optional[str],
        status: "active" | "paused" | "archived",
        last_evaluated_at: Optional[str]
    }
    """
    if state is None:
        state = {
            "strategy_id": event.get("strategy_id"),
            "active_version_id": None,
            "status": "active",
            "last_evaluated_at": None,
        }

    et = event["event_type"]

    # A created strategy is not runnable until StrategyActivated. Every stream
    # written before start-on-hold emits Created, VersionCreated, Activated in
    # that order, so it replays to exactly the state it always did; what this
    # buys is that a strategy created on hold is never "active" in between.
    if et == "StrategyCreated":
        state["status"] = "paused"

    # The version is current as soon as it exists, whether or not the
    # strategy is running - an edit to a paused strategy must not need a
    # resume to take effect.
    elif et == "StrategyVersionCreated":
        state["active_version_id"] = event.get("strategy_version_id")

    elif et == "StrategyActivated":
        state["active_version_id"] = event.get("strategy_version_id")
        state["status"] = "active"

    elif et == "StrategyPaused":
        state["status"] = "paused"

    elif et == "StrategyResumed":
        state["status"] = "active"

    elif et == "StrategyArchived":
        state["status"] = "archived"

    elif et == "StrategyEvaluationCompleted":
        state["last_evaluated_at"] = event["timestamp_utc"]

    elif et == "DecisionObserved":
        state["last_evaluated_at"] = event["timestamp_utc"]

    return state


# ============================================================
# Capital Balance Reducer
# ============================================================

def reduce_capital_balance(
    state: Dict[str, Any] | None,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """
    state shape:
    {
        strategy_id: str,
        currency: str,
        available: Decimal,
        reserved: Decimal,
        updated_at: str
    }
    """
    payload = _payload(event)
    et = event["event_type"]

    if state is None:
        currency = payload.get("currency", "EUR")
        state = {
            "strategy_id": event.get("strategy_id"),
            "currency": currency,
            "available": Decimal("0"),
            "reserved": Decimal("0"),
            "updated_at": event["timestamp_utc"],
        }

    if et.startswith("Capital"):
        amount = Decimal(payload["amount"])

        if et == "CapitalCredited":
            state["available"] += amount
        elif et == "CapitalDebited":
            state["available"] -= amount
        elif et == "CapitalReserved":
            state["available"] -= amount
            state["reserved"] += amount
        elif et == "CapitalReleased":
            state["reserved"] -= amount
            state["available"] += amount

    elif et == "FundsReserved":
        amount = Decimal(payload["reserved_eur"])
        state["available"] -= amount
        state["reserved"] += amount

    elif et == "FundsReleased":
        released = Decimal(payload["released_eur"])
        consumed = Decimal(payload.get("consumed_eur", "0"))
        # consumed: EUR spent on fill — deduct from reserved
        state["reserved"] -= consumed
        # released: surplus refund — move from reserved to available
        release_from_reserved = min(state["reserved"], released)
        state["reserved"] -= release_from_reserved
        state["available"] += released

    state["updated_at"] = event["timestamp_utc"]
    return state


# ============================================================
# Inventory Balance Reducer
# ============================================================

def reduce_inventory_balance(
    state: Dict[str, Any] | None,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """
    state shape:
    {
        strategy_id: str,
        asset: str,
        quantity: Decimal,
        reserved: Decimal,
        updated_at: str
    }
    """
    payload = _payload(event)
    et = event["event_type"]

    if state is None:
        state = {
            "strategy_id": event.get("strategy_id"),
            # Normalise here rather than in the callers: a caller-side fix
            # leaves the same footgun for the next dispatcher.
            "asset": base_asset(event.get("symbol")),
            "quantity": Decimal("0"),
            "reserved": Decimal("0"),
            "updated_at": event["timestamp_utc"],
        }

    if et == "InventoryBootstrapped":
        qty = Decimal(payload["initial_units"])
    elif et in ("InventoryIncreased", "InventoryDecreased"):
        qty = Decimal(payload["quantity"])
    elif et == "InventoryReserved":
        qty = Decimal(payload["reserved_units"])
    elif et == "InventoryReleased":
        qty = Decimal(payload["released_units"])
    else:
        qty = Decimal("0")

    if et == "InventoryBootstrapped":
        state["quantity"] += qty
    elif et == "InventoryIncreased":
        state["quantity"] += qty
    elif et == "InventoryDecreased":
        # Fill: consume from reserved
        state["reserved"] -= qty
    elif et == "InventoryReserved":
        # Lock units: move from quantity to reserved
        state["quantity"] -= qty
        state["reserved"] += qty
    elif et == "InventoryReleased":
        # Cancel: return units from reserved to quantity
        state["reserved"] -= qty
        state["quantity"] += qty

    state["updated_at"] = event["timestamp_utc"]
    return state


# ============================================================
# Trade State Reducer
# ============================================================

def reduce_trade_state(state: Dict[str, Any] | None, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    state shape:
    {
        trade_id: str,
        strategy_id: str,
        strategy_version_id: str,
        symbol: str,
        exchange: Optional[str],
        side: str,

        state: str,
        external_order_id: Optional[str],
        filled_quantity: Decimal,
        avg_price: Decimal,

        created_at: str,
        updated_at: str
    }
    """
    et = event["event_type"]
    payload = _payload(event)

    if state is None:
        if et == "TradeIntentCreated":
            side = payload["side"]
        elif et == "OrderSized":
            side = payload["side"]
        else:
            raise RuntimeError(f"Trade state must start with TradeIntentCreated or OrderSized, got {et}")

        state = {
            "trade_id": event["trade_id"],
            "strategy_id": event["strategy_id"],
            "strategy_version_id": event.get("strategy_version_id"),
            "symbol": event.get("symbol"),
            "exchange": event.get("exchange"),
            "side": side,

            "state": "sized" if et == "OrderSized" else "intent",
            "external_order_id": None,
            "filled_quantity": Decimal("0"),
            "avg_price": Decimal("0"),

            "created_at": event["timestamp_utc"],
            "updated_at": event["timestamp_utc"],
        }
        return state

    # ---- lifecycle transitions ----

    if et == "TradeIntentCancelled":
        state["state"] = "cancelled"

    elif et == "OrderSubmitted":
        state["state"] = "submitted"
        state["exchange"] = event.get("exchange")

    elif et in ("OrderAcceptedByExchange", "OrderAccepted"):
        state["state"] = "accepted"
        state["external_order_id"] = payload.get("external_order_id") or payload.get("exchange_order_id")

    elif et in ("OrderRejectedByExchange", "OrderRejected"):
        state["state"] = "rejected"

    elif et == "OrderPartiallyFilled":
        state["state"] = "partial"
        _apply_fill(state, payload)

    elif et == "OrderFullyFilled":
        state["state"] = "filled"
        _apply_fill(state, payload)

    elif et == "OrderCancelled":
        state["state"] = "cancelled"

    state["updated_at"] = event["timestamp_utc"]
    return state


# ============================================================
# Portfolio Snapshot Reducer
# ============================================================

def reduce_portfolio_snapshot(
    state: Dict[str, Any] | None,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stateless reducer: each DecisionObserved produces an independent snapshot.

    state shape:
    {
        strategy_id: str,
        symbol: str,
        timeframe: str,
        timestamp_utc: str,
        action: str,
        reason_code: str,
        close_price: Decimal,
        free_eur: Decimal,
        asset_units: Decimal,
        acb_price: Decimal | None,
        reserved_eur: Decimal,
        reserved_units: Decimal,
        portfolio_value_eur: Decimal,
        unrealized_pnl_eur: Decimal,
    }
    """
    payload = _payload(event)
    metrics = payload.get("metrics", {})

    close_price = Decimal(str(metrics.get("close_price", 0)))
    free_eur = Decimal(str(metrics.get("free_eur", 0)))
    asset_units = Decimal(str(metrics.get("asset_units", 0)))
    acb_price_raw = metrics.get("acb_price")
    acb_price = Decimal(str(acb_price_raw)) if acb_price_raw else None
    reserved_eur = Decimal(str(metrics.get("reserved_eur", 0)))
    reserved_units = Decimal(str(metrics.get("reserved_units", 0)))

    total_eur = free_eur + reserved_eur
    total_units = asset_units + reserved_units
    portfolio_val = total_eur + total_units * close_price
    unrealized = (
        total_units * (close_price - acb_price)
        if acb_price and total_units > 0
        else Decimal("0")
    )

    return {
        "strategy_id": event.get("strategy_id"),
        "symbol": event.get("symbol"),
        "timeframe": event.get("timeframe"),
        "timestamp_utc": event["timestamp_utc"],
        "action": payload.get("action"),
        "reason_code": payload.get("reason_code"),
        "close_price": close_price,
        "free_eur": free_eur,
        "asset_units": asset_units,
        "acb_price": acb_price,
        "reserved_eur": reserved_eur,
        "reserved_units": reserved_units,
        "portfolio_value_eur": portfolio_val,
        "unrealized_pnl_eur": unrealized,
    }


# ============================================================
# Helpers
# ============================================================

def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Payload is already JSON-decoded by rebuild layer.
    """
    return event["payload"]


def _apply_fill(state: Dict[str, Any], payload: Dict[str, Any]):
    filled = Decimal(payload.get("filled_quantity") or payload["filled_units"])
    price = Decimal(payload.get("average_price") or payload["avg_price"])

    prev_qty = state["filled_quantity"]
    prev_cost = prev_qty * state["avg_price"]

    new_cost = prev_cost + (filled * price)
    new_qty = prev_qty + filled

    state["filled_quantity"] = new_qty
    if new_qty > 0:
        state["avg_price"] = new_cost / new_qty
