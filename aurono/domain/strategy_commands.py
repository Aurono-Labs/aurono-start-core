# aurono/domain/strategy_commands.py

"""
Strategy management commands.

Each command validates inputs and emits events through emit_event_runtime().
Commands also populate identity tables for fast lookups.
"""

import json
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, Optional

from aurono.db.connect import connect as db_connect


REQUIRED_PARAMETERS = {"symbol", "exchange", "timeframe", "buy_drop_pct", "sell_rise_pct", "buy_eur"}


class CommandError(Exception):
    """Raised when a command fails validation."""
    pass


def _emit(db_path: Path, *, event_type: str, payload: Dict[str, Any], envelope: Dict[str, Any]) -> str:
    from aurono.events.runtime import emit_event_runtime
    return emit_event_runtime(
        event_db_path=db_path,
        ledger_db_path=db_path,
        event_type=event_type,
        actor_type="user",
        actor_id="api",
        aurono_device_id="api",
        payload=payload,
        envelope=envelope,
    )


def _get_strategy_state(db_path: Path, strategy_id: str) -> Optional[Dict[str, Any]]:
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM strategy_state_projection WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _insert_identity(db_path: Path, *, strategy_id: str, name: str, timestamp: str):
    conn = db_connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO strategy (strategy_id, name, created_at) VALUES (?, ?, ?)",
            (strategy_id, name, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_version_identity(db_path: Path, *, version_id: str, strategy_id: str,
                              parameters: Dict[str, Any], timestamp: str):
    conn = db_connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO strategy_version (strategy_version_id, strategy_id, parameters_json, created_at) VALUES (?, ?, ?, ?)",
            (version_id, strategy_id, json.dumps(parameters), timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def _upsert_strategy_projection(db_path: Path, strategy_id: str, event_type: str,
                                  version_id: Optional[str] = None):
    """Update strategy_state_projection after event emission."""
    from aurono.projections.store_sqlite import upsert_strategy_state

    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        state = {
            "strategy_id": strategy_id,
            "active_version_id": None,
            "status": "active",
            "last_evaluated_at": None,
        }

    if event_type == "StrategyCreated":
        state["status"] = "active"
    elif event_type == "StrategyActivated":
        state["active_version_id"] = version_id
        state["status"] = "active"
    elif event_type == "StrategyPaused":
        state["status"] = "paused"
    elif event_type == "StrategyArchived":
        state["status"] = "archived"

    upsert_strategy_state(db_path, state)


def _upsert_capital_projection(db_path: Path, strategy_id: str, amount: Decimal):
    """Update capital_balance_projection after CapitalCredited."""
    from aurono.projections.store_sqlite import load_capital_balance, upsert_capital_balance
    from datetime import datetime, timezone

    existing = load_capital_balance(db_path, strategy_id, "EUR")
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing["available"] = float(Decimal(str(existing["available"])) + amount)
        existing["updated_at"] = now
        upsert_capital_balance(db_path, existing)
    else:
        upsert_capital_balance(db_path, {
            "strategy_id": strategy_id,
            "currency": "EUR",
            "available": float(amount),
            "reserved": 0.0,
            "updated_at": now,
        })


# ============================================================
# Commands
# ============================================================

def _upsert_inventory_projection(db_path: Path, strategy_id: str, asset: str, units: Decimal):
    """Update inventory_balance_projection after InventoryBootstrapped."""
    from aurono.projections.store_sqlite import load_inventory_balance, upsert_inventory_balance
    from datetime import datetime, timezone

    existing = load_inventory_balance(db_path, strategy_id, asset)
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing["quantity"] = float(Decimal(str(existing["quantity"])) + units)
        existing["updated_at"] = now
        upsert_inventory_balance(db_path, existing)
    else:
        upsert_inventory_balance(db_path, {
            "strategy_id": strategy_id,
            "asset": asset,
            "quantity": float(units),
            "reserved": 0.0,
            "updated_at": now,
        })


def create_strategy(
    db_path: Path,
    *,
    name: str,
    parameters: Dict[str, Any],
    initial_capital_eur: Decimal,
    initial_inventory: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Create a new strategy with its first version and initial capital/inventory.

    Emits: StrategyCreated, StrategyVersionCreated, StrategyActivated,
           and optionally CapitalCredited and/or InventoryBootstrapped.
    Returns: {strategy_id, strategy_version_id}
    """
    if not name or not name.strip():
        raise CommandError("Strategy name is required")

    missing = REQUIRED_PARAMETERS - parameters.keys()
    if missing:
        raise CommandError(f"Missing required parameters: {missing}")

    has_inventory = (
        initial_inventory is not None
        and Decimal(str(initial_inventory.get("units", 0))) > 0
    )

    if initial_capital_eur < 0:
        raise CommandError("Initial capital must be positive")

    if initial_capital_eur == 0 and not has_inventory:
        raise CommandError("Initial capital or inventory is required")

    strategy_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())

    # 1. StrategyCreated
    _emit(db_path, event_type="StrategyCreated",
          payload={"name": name},
          envelope={"strategy_id": strategy_id})

    # 2. StrategyVersionCreated
    _emit(db_path, event_type="StrategyVersionCreated",
          payload={"parameters": parameters, "previous_version_id": None},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # 3. StrategyActivated
    _emit(db_path, event_type="StrategyActivated",
          payload={},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # 4. CapitalCredited (if capital > 0)
    if initial_capital_eur > 0:
        _emit(db_path, event_type="CapitalCredited",
              payload={"amount": initial_capital_eur, "currency": "EUR"},
              envelope={"strategy_id": strategy_id})

    # 5. InventoryBootstrapped (if inventory provided)
    if has_inventory:
        inv_symbol = initial_inventory["symbol"]
        # Ensure symbol is a pair (e.g. "BTC-EUR"), not just a base asset
        if "-" not in inv_symbol:
            inv_symbol = f"{inv_symbol}-EUR"
        units = Decimal(str(initial_inventory["units"]))
        acb_price = Decimal(str(initial_inventory.get("acb_price", 0)))

        # InventoryBootstrapped requires actor_type='system'
        from aurono.events.runtime import emit_event_runtime
        emit_event_runtime(
            event_db_path=db_path,
            ledger_db_path=db_path,
            event_type="InventoryBootstrapped",
            actor_type="system",
            actor_id="strategy_create",
            aurono_device_id="api",
            payload={"initial_units": units, "acb_price": acb_price},
            envelope={"strategy_id": strategy_id, "symbol": inv_symbol},
        )

    # Update projections
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    _insert_identity(db_path, strategy_id=strategy_id, name=name, timestamp=now)
    _insert_version_identity(db_path, version_id=version_id, strategy_id=strategy_id,
                              parameters=parameters, timestamp=now)
    # Projections are updated inline by emit_event_runtime — no manual upsert needed

    if has_inventory:
        inv_sym = initial_inventory["symbol"]
        asset = inv_sym.split("-")[0] if "-" in inv_sym else inv_sym
        # Projections updated inline by emit_event_runtime

    return {"strategy_id": strategy_id, "strategy_version_id": version_id}


def create_version(
    db_path: Path,
    *,
    strategy_id: str,
    parameters: Dict[str, Any],
) -> Dict[str, str]:
    """
    Create a new version for an existing strategy.

    Emits: StrategyVersionCreated, StrategyActivated.
    Returns: {strategy_version_id}
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Cannot create version for archived strategy")

    missing = REQUIRED_PARAMETERS - parameters.keys()
    if missing:
        raise CommandError(f"Missing required parameters: {missing}")

    previous_version_id = state.get("active_version_id")
    version_id = str(uuid.uuid4())

    # 1. StrategyVersionCreated
    _emit(db_path, event_type="StrategyVersionCreated",
          payload={"parameters": parameters, "previous_version_id": previous_version_id},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # 2. StrategyActivated
    _emit(db_path, event_type="StrategyActivated",
          payload={},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # Update projections
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    _insert_version_identity(db_path, version_id=version_id, strategy_id=strategy_id,
                              parameters=parameters, timestamp=now)
    # Projections updated inline by emit_event_runtime

    return {"strategy_version_id": version_id}


def pause_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    reason: Optional[str] = None,
) -> None:
    """
    Pause a strategy.

    Emits: StrategyPaused.
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] != "active":
        raise CommandError(f"Cannot pause strategy in '{state['status']}' state")

    payload = {}
    if reason:
        payload["reason"] = reason

    _emit(db_path, event_type="StrategyPaused",
          payload=payload,
          envelope={"strategy_id": strategy_id})

    # Projections updated inline by emit_event_runtime


def activate_strategy(
    db_path: Path,
    *,
    strategy_id: str,
) -> None:
    """
    Activate (resume) a paused strategy.

    Emits: StrategyActivated.
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] != "paused":
        raise CommandError(f"Cannot activate strategy in '{state['status']}' state")

    version_id = state.get("active_version_id")
    if not version_id:
        raise CommandError("No active version to activate")

    _emit(db_path, event_type="StrategyActivated",
          payload={},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # Projections updated inline by emit_event_runtime


def archive_strategy(
    db_path: Path,
    *,
    strategy_id: str,
) -> None:
    """
    Archive a strategy.

    Auto-withdraws remaining EUR capital (free + reserved) before archiving.
    Asset holdings stay on the exchange but are no longer tracked.

    Emits: CapitalDebited (if capital > 0), then StrategyArchived.
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Strategy is already archived")

    # Auto-withdraw remaining EUR capital
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT available, reserved FROM capital_balance_projection WHERE strategy_id = ? AND currency = 'EUR'",
            (strategy_id,),
        ).fetchone()
    finally:
        conn.close()

    if row:
        available = Decimal(str(row["available"]))
        reserved = Decimal(str(row["reserved"]))
        total_eur = available + reserved
        if total_eur > 0:
            _emit(db_path, event_type="CapitalDebited",
                  payload={"amount": total_eur, "currency": "EUR"},
                  envelope={"strategy_id": strategy_id})
            # Projections updated inline by emit_event_runtime

    _emit(db_path, event_type="StrategyArchived",
          payload={},
          envelope={"strategy_id": strategy_id})

    # Projections updated inline by emit_event_runtime


def deposit_capital(
    db_path: Path,
    *,
    strategy_id: str,
    amount: Decimal,
) -> Dict[str, Any]:
    """
    Deposit additional capital into a strategy.

    Emits: CapitalCredited.
    Returns: {available, reserved}
    """
    if amount <= 0:
        raise CommandError("Amount must be greater than zero")

    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Cannot deposit capital into archived strategy")

    _emit(db_path, event_type="CapitalCredited",
          payload={"amount": amount, "currency": "EUR"},
          envelope={"strategy_id": strategy_id})

    # Projections updated inline by emit_event_runtime

    from aurono.projections.store_sqlite import load_capital_balance
    bal = load_capital_balance(db_path, strategy_id, "EUR")
    return {"available": bal["available"], "reserved": bal["reserved"]}


def withdraw_capital(
    db_path: Path,
    *,
    strategy_id: str,
    amount: Decimal,
) -> Dict[str, Any]:
    """
    Withdraw idle capital from a strategy.

    Emits: CapitalDebited.
    Returns: {available, reserved}
    """
    if amount <= 0:
        raise CommandError("Amount must be greater than zero")

    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Cannot withdraw capital from archived strategy")

    from aurono.projections.store_sqlite import load_capital_balance
    bal = load_capital_balance(db_path, strategy_id, "EUR")
    available = Decimal(str(bal["available"])) if bal else Decimal("0")

    if amount > available:
        raise CommandError(
            f"Insufficient available capital: requested {amount} EUR but only {available} EUR available"
        )

    _emit(db_path, event_type="CapitalDebited",
          payload={"amount": amount, "currency": "EUR"},
          envelope={"strategy_id": strategy_id})

    # Projections updated inline by emit_event_runtime

    bal = load_capital_balance(db_path, strategy_id, "EUR")
    return {"available": bal["available"], "reserved": bal["reserved"]}
