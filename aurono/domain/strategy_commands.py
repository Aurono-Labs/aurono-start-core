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
from aurono.domain.strategy_params import REQUIRED_PARAMETERS, validate_parameters


class CommandError(Exception):
    """Raised when a command fails validation."""
    pass


def check_parameters(parameters: Dict[str, Any]) -> None:
    """Raise CommandError naming every missing or invalid parameter."""
    missing = REQUIRED_PARAMETERS - parameters.keys()
    if missing:
        raise CommandError(f"Missing required parameters: {', '.join(sorted(missing))}")
    errs = validate_parameters(parameters)
    if errs:
        raise CommandError("Invalid parameters: " + " ".join(errs[k] for k in sorted(errs)))


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

def create_strategy(
    db_path: Path,
    *,
    name: str,
    parameters: Dict[str, Any],
    initial_capital_eur: Decimal,
    initial_inventory: Optional[Dict[str, Any]] = None,
    bootstrap_mark: Optional[Dict[str, Any]] = None,
    start_paused: bool = False,
) -> Dict[str, str]:
    """
    Create a new strategy with its first version and initial capital/inventory.

    Emits: StrategyCreated, StrategyVersionCreated, then StrategyActivated -
           or, with `start_paused`, StrategyPaused(reason="created_paused")
           so it places nothing until the user resumes it - and optionally
           CapitalCredited, InventoryBootstrapped and InventoryBootstrapMarked.
    Returns: {strategy_id, strategy_version_id}

    `bootstrap_mark` carries the market price of the starting position,
    resolved by the caller from its own candle store: this module has no
    market-data dependency by design, so pricing is the caller's job. Shape
    is `{price: Decimal, source_timeframe: str, source_timestamp_ms: int,
    source_lag_seconds: int}`, where the source fields record which candle
    the price came from so the derivation stays auditable.

    When it is None, no InventoryBootstrapMarked is emitted and the
    benchmark base falls back to cost basis for this strategy. That is the
    honest outcome when no candle sits close enough to the bootstrap to
    price it.
    """
    if not name or not name.strip():
        raise CommandError("Strategy name is required")

    check_parameters(parameters)

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

    # 3. StrategyActivated, or held. The projection does not treat a created
    # strategy as runnable until it is activated, so the paused path never
    # passes through a state the scheduler would pick up.
    if start_paused:
        _emit(db_path, event_type="StrategyPaused",
              payload={"reason": "created_paused"},
              envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})
    else:
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
        bootstrap_event_id = emit_event_runtime(
            event_db_path=db_path,
            ledger_db_path=db_path,
            event_type="InventoryBootstrapped",
            actor_type="system",
            actor_id="strategy_create",
            aurono_device_id="api",
            payload={"initial_units": units, "acb_price": acb_price},
            envelope={"strategy_id": strategy_id, "symbol": inv_symbol},
        )

        # 6. InventoryBootstrapMarked: what the position was actually worth.
        # acb_price is the sell floor the strategy trades against; it is not
        # the money that came in, and the two can diverge badly. Return
        # figures need this one, ACB needs the other.
        if bootstrap_mark is not None:
            mark_price = Decimal(str(bootstrap_mark["price"]))
            emit_event_runtime(
                event_db_path=db_path,
                ledger_db_path=db_path,
                event_type="InventoryBootstrapMarked",
                actor_type="system",
                actor_id="strategy_create",
                aurono_device_id="api",
                payload={
                    "bootstrap_event_id": bootstrap_event_id,
                    "units": units,
                    "mark_price": mark_price,
                    "mark_value_eur": units * mark_price,
                    "source_timeframe": bootstrap_mark["source_timeframe"],
                    "source_timestamp_ms": bootstrap_mark["source_timestamp_ms"],
                    "source_lag_seconds": bootstrap_mark["source_lag_seconds"],
                },
                envelope={"strategy_id": strategy_id, "symbol": inv_symbol},
            )

    # Update projections
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    _insert_identity(db_path, strategy_id=strategy_id, name=name, timestamp=now)
    _insert_version_identity(db_path, version_id=version_id, strategy_id=strategy_id,
                              parameters=parameters, timestamp=now)
    # Projections are updated inline by emit_event_runtime — no manual upsert needed

    return {"strategy_id": strategy_id, "strategy_version_id": version_id}


def create_version(
    db_path: Path,
    *,
    strategy_id: str,
    parameters: Dict[str, Any],
) -> Dict[str, str]:
    """
    Create a new version for an existing strategy.

    Emits: StrategyVersionCreated, then StrategyActivated only if the strategy
    was already active. A paused strategy stays paused: an edit is not a
    resume, and treating it as one would put a strategy paused for going live
    or for recovery back to trading without anyone pressing Resume.
    Returns: {strategy_version_id}
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Cannot create version for archived strategy")

    check_parameters(parameters)

    previous_version_id = state.get("active_version_id")
    version_id = str(uuid.uuid4())

    # 1. StrategyVersionCreated
    _emit(db_path, event_type="StrategyVersionCreated",
          payload={"parameters": parameters, "previous_version_id": previous_version_id},
          envelope={"strategy_id": strategy_id, "strategy_version_id": version_id})

    # 2. StrategyActivated - only for a strategy that was already running.
    # The projection takes the new version from StrategyVersionCreated, so a
    # paused strategy picks up its new settings without being resumed.
    if state["status"] == "active":
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


def _open_order_refusal(open_trades, status: str) -> str:
    """
    Why archiving was refused, and what the user can do about it.

    Actionable rather than merely correct: there is no cancel endpoint, so
    "cancel it yourself" would be a dead end. An active strategy's open orders
    are force-cancelled by the scheduler once they age out, so waiting works.
    A paused one is out of `get_active_strategies()`, so nothing will settle
    the order until it is resumed - which is the same orphaning this guard
    exists to prevent, reached by a different door.

    The timeout is deliberately not quoted here. `ORDER_TIMEOUT_HOURS` lives in
    `aurono/runtime/evaluator.py`, and importing it would pull the exchange
    clients into this open-designated module's transitive closure and trip
    tests/domain/test_import_boundaries.py. A duplicated literal would drift,
    so the copy stays qualitative.
    """
    detail = ", ".join(
        f"{str(t['side']).lower()} on {t['symbol']}" for t in open_trades
    )
    plural = "an order is" if len(open_trades) == 1 else f"{len(open_trades)} orders are"
    if status == "paused":
        remedy = (
            "Resume the strategy so the order can settle, then archive it."
        )
    else:
        remedy = (
            "Wait for it to fill, or for Aurono to cancel it automatically, "
            "then archive."
        )
    return f"Cannot archive while {plural} open ({detail}). {remedy}"


def archive_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    exit_mark: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Archive a strategy.

    Auto-withdraws idle EUR capital before archiving. Asset holdings stay on
    the exchange but are no longer tracked.

    `exit_mark` carries the market price of whatever the strategy still holds,
    resolved by the caller from its own candle store: this module has no
    market-data dependency by design, so pricing is the caller's job. Shape is
    `{symbol: str, price: Decimal, source_timeframe: str,
    source_timestamp_ms: int, source_lag_seconds: int}` - the bootstrap mark's
    shape plus `symbol`, because `create_strategy` is told which asset it is
    bootstrapping while this command has to look up what is held and therefore
    needs to know which asset the price belongs to. The source fields record
    which candle priced it so the derivation stays auditable.

    When it is None, or the strategy holds nothing, no InventoryExitMarked is
    emitted. That is the honest outcome when no candle sits close enough to
    price the exit, and it leaves the strategy reading exactly as it does
    today rather than inventing a number.

    Refuses while an order is open. Archiving used to withdraw
    `available + reserved` with no such check, and that had two consequences.
    `reduce_capital_balance()` subtracts a CapitalDebited from `available`
    alone, so the committed half came back as a negative balance. And nothing
    settled the order afterwards: `resolve_open_orders()` is reached only
    through `get_active_strategies()`, which filters `status = 'active'`, so
    the reservation stayed open forever and the limit order stayed live on the
    exchange, unattended, while the capital overview reported that money as
    free to reallocate.

    Emits: CapitalDebited (if idle capital > 0), then InventoryExitMarked (if
    coins are still held and a price resolved), then StrategyArchived.
    """
    state = _get_strategy_state(db_path, strategy_id)
    if state is None:
        raise CommandError("Strategy not found")
    if state["status"] == "archived":
        raise CommandError("Strategy is already archived")

    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Broader than resolve_open_orders()'s predicate in two ways, both
        # deliberate. That one also requires external_order_id IS NOT NULL
        # because it needs the id to poll, but a submitted trade with no id yet
        # may still have reached the exchange. And it omits 'partial', which is
        # an order with an unfilled remainder still live on the exchange.
        #
        # 'intent' is deliberately NOT blocking. Nothing is on the exchange at
        # that point, and ExecutionAborted is excluded from
        # TRADE_LIFECYCLE_EVENTS by design, so an aborted flow can leave a
        # trade at 'intent' permanently. Blocking on it would make such a
        # strategy unarchivable forever, and there is no cancel endpoint to
        # escape through - a worse failure than the one this guard prevents.
        open_trades = conn.execute(
            """
            SELECT side, symbol FROM trade_state_projection
            WHERE strategy_id = ? AND state IN ('submitted', 'accepted', 'partial')
            """,
            (strategy_id,),
        ).fetchall()
        row = conn.execute(
            "SELECT available, reserved FROM capital_balance_projection WHERE strategy_id = ? AND currency = 'EUR'",
            (strategy_id,),
        ).fetchone()
        # `quantity` is the free half and `reserved` the locked half, mirroring
        # capital's available/reserved, so what the strategy actually holds is
        # the sum. The guard above means `reserved` is zero on any path that
        # gets as far as archiving, but summing keeps this correct on its own
        # terms rather than depending on that.
        held_rows = conn.execute(
            """
            SELECT asset, quantity + reserved AS units
            FROM inventory_balance_projection
            WHERE strategy_id = ? AND quantity + reserved > 0
            """,
            (strategy_id,),
        ).fetchall()
    finally:
        conn.close()

    if open_trades:
        raise CommandError(_open_order_refusal(open_trades, state["status"]))

    if row:
        # Only idle capital. The guard above means `reserved` is zero here, so
        # this is the same number the old `available + reserved` produced on
        # every path that is still allowed. Withdrawing `available` alone
        # rather than the sum keeps that an invariant of the code instead of a
        # property of the caller, and stops float dust in `reserved` (real
        # rows carry values like 1.15e-14) from pushing `available` negative.
        total_eur = Decimal(str(row["available"]))
        if total_eur > 0:
            _emit(db_path, event_type="CapitalDebited",
                  payload={"amount": total_eur, "currency": "EUR"},
                  envelope={"strategy_id": strategy_id})
            # Projections updated inline by emit_event_runtime

    # What the coins were worth as they left. Emitted before StrategyArchived so
    # that event stays the last word in the strategy's history, and so the
    # Timeline reads in the order things happened: cash withdrawn, position
    # valued, strategy archived.
    #
    # The caller resolves one price, for the strategy's own symbol, so only the
    # matching asset can be marked. A strategy can be bootstrapped with an asset
    # other than the one it trades, and pricing FET units off a BTC-EUR candle
    # would be worse than recording nothing - the same guard create_strategy
    # applies to the bootstrap mark.
    if exit_mark is not None and held_rows:
        mark_symbol = str(exit_mark["symbol"])
        mark_asset = mark_symbol.split("-", 1)[0].upper()
        mark_price = Decimal(str(exit_mark["price"]))
        for held in held_rows:
            if str(held["asset"]).upper() != mark_asset:
                continue
            units = Decimal(str(held["units"]))
            from aurono.events.runtime import emit_event_runtime
            emit_event_runtime(
                event_db_path=db_path,
                ledger_db_path=db_path,
                event_type="InventoryExitMarked",
                actor_type="system",
                actor_id="strategy_archive",
                aurono_device_id="api",
                payload={
                    "units": units,
                    "mark_price": mark_price,
                    "mark_value_eur": units * mark_price,
                    "source_timeframe": exit_mark["source_timeframe"],
                    "source_timestamp_ms": exit_mark["source_timestamp_ms"],
                    "source_lag_seconds": exit_mark["source_lag_seconds"],
                },
                envelope={"strategy_id": strategy_id, "symbol": mark_symbol},
            )

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
