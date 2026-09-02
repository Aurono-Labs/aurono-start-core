# aurono/ledger/build_state.py

from decimal import Decimal
from aurono.ledger.state import LedgerState

def build_ledger_state(conn, *, strategy_id: int, symbol: str) -> LedgerState:
    # ----------------------------
    # Initialize state
    # ----------------------------
    free_eur = Decimal("0")
    position_units = Decimal("0")
    position_cost_eur = Decimal("0")

    buy_reservations = {}
    sell_reservations = {}

    # ----------------------------
    # Interleaved ledger reduction
    # ----------------------------
    # Capital and inventory entries are merged into one chronological stream.
    # This ensures proportional cost reduction during SELL "consume" only
    # sees BUY debits from prior events, not future ones.
    rows = conn.execute(
        """
        SELECT 'C' AS ledger, amount AS value, kind, trade_id, timestamp_utc, event_id
        FROM capital_ledger
        WHERE strategy_id = ?
        UNION ALL
        SELECT 'I' AS ledger, quantity AS value, kind, trade_id, timestamp_utc, event_id
        FROM inventory_ledger
        WHERE strategy_id = ? AND asset = ?
        ORDER BY timestamp_utc, event_id
        """,
        (str(strategy_id), str(strategy_id), symbol),
    ).fetchall()

    for ledger, value, kind, trade_id, _ts, _eid in rows:
        val = Decimal(str(value))

        if ledger == "C":
            # --- Capital entry ---
            if kind == "cost_basis":
                position_cost_eur += val
                continue

            free_eur += val

            if kind == "reserve":
                if trade_id is None:
                    raise ValueError("Capital reserve without trade_id")
                buy_reservations[trade_id] = buy_reservations.get(trade_id, Decimal("0")) + (-val)

            elif kind == "release":
                if trade_id is None:
                    raise ValueError("Capital release without trade_id")
                buy_reservations[trade_id] = buy_reservations.get(trade_id, Decimal("0")) - val

            elif kind == "debit":
                # Buy-fill consumed cost. Always carries trade_id.
                # Withdrawals (CapitalDebited) use kind='withdraw' and do NOT touch cost basis.
                position_cost_eur += (-val)

            elif kind == "withdraw":
                # User withdrawal. free_eur was already adjusted above; do not touch cost basis.
                pass

        else:
            # --- Inventory entry ---
            if kind == "reserve":
                sell_reservations[trade_id] = sell_reservations.get(trade_id, Decimal("0")) + (-val)

            elif kind == "consume":
                sell_reservations[trade_id] = sell_reservations.get(trade_id, Decimal("0")) - val
                if position_units > 0:
                    cost_per_unit = position_cost_eur / position_units
                    position_cost_eur -= val * cost_per_unit
                position_units -= val

            elif kind == "release":
                sell_reservations[trade_id] = sell_reservations.get(trade_id, Decimal("0")) - val

            elif kind == "increase":
                position_units += val

            else:
                position_units += val

    reserved_eur = sum(buy_reservations.values())
    reserved_units = sum(sell_reservations.values())

    # ----------------------------
    # Final state
    # ----------------------------
    return LedgerState(
        strategy_id=strategy_id,
        symbol=symbol,
        free_eur=Decimal(free_eur),
        position_units=Decimal(position_units),
        position_cost_eur=Decimal(position_cost_eur),
        reserved_eur=Decimal(reserved_eur),
        reserved_units=Decimal(reserved_units),
        buy_reservations=buy_reservations,
        sell_reservations=sell_reservations,
    )
