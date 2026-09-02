# aurono/analytics/pnl.py

"""
P&L computation from the interleaved capital + inventory ledger.

Realized P&L is reported per **round trip** — a completed BUY→…→SELL pair
where the SELL has settled (see domain_terminology.md §3.4). Each record is
keyed by the SELL-side trade_id and pairs the consume entry (cost basis at
sale) with the matching credit entry (proceeds).

Unrealized P&L is a simple mark-to-market calculation on open positions.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from aurono.ledger.state import LedgerState


# ============================================================
# Data types
# ============================================================

@dataclass(frozen=True)
class RoundTripPnL:
    """
    Realized P&L for a completed round trip — a BUY→…→SELL pair where the
    SELL side has settled. Keyed by the SELL-side trade_id. Per domain
    terminology §3.4, a round trip is distinct from a "trade": a single BUY
    attempt is one trade but not a round trip until a matching SELL settles.
    """
    trade_id: str
    sold_units: Decimal
    cost_basis_eur: Decimal    # sold_units * ACB at fill time
    proceeds_eur: Decimal      # from FundsReleased credit
    realized_pnl_eur: Decimal  # proceeds - cost_basis
    timestamp_utc: str


# ============================================================
# Realized P&L
# ============================================================

def compute_realized_pnl(conn, *, strategy_id: int, symbol: str) -> List[RoundTripPnL]:
    """
    Compute per-trade realized P&L by replaying the interleaved ledger.

    Uses the same UNION ALL pattern as build_ledger_state() to maintain
    correct chronological ordering of capital and inventory entries.

    At each SELL consume entry, the current ACB is used to compute cost basis.
    The matching credit entry (same trade_id) provides the proceeds.
    """
    # Extract base asset from symbol (e.g. "BTC" from "BTC-EUR")
    base_asset = symbol.split("-", 1)[0] if "-" in symbol else symbol

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
        (str(strategy_id), str(strategy_id), base_asset),
    ).fetchall()

    # Track position state (mirrors build_ledger_state logic)
    position_units = Decimal("0")
    position_cost_eur = Decimal("0")

    # Pending sells: trade_id -> {sold_units, cost_basis_eur}
    pending_sells = {}

    # Credit entries from SELL proceeds: trade_id -> {proceeds_eur, timestamp_utc}
    sell_credits = {}

    results: List[RoundTripPnL] = []

    for ledger, value, kind, trade_id, ts, _eid in rows:
        val = Decimal(str(value))

        if ledger == "C":
            # --- Capital entry ---
            if kind == "cost_basis":
                position_cost_eur += val

            elif kind == "debit":
                # Buy-fill consumed cost. Always carries trade_id.
                # Withdrawals use kind='withdraw' and are ignored here.
                position_cost_eur += (-val)

            elif kind == "withdraw":
                # User withdrawal — does not affect cost basis or units.
                pass

            elif kind == "credit" and trade_id is not None:
                # SELL proceeds — pair with pending sell
                if trade_id in pending_sells:
                    ps = pending_sells.pop(trade_id)
                    pnl = val - ps["cost_basis_eur"]
                    results.append(RoundTripPnL(
                        trade_id=trade_id,
                        sold_units=ps["sold_units"],
                        cost_basis_eur=ps["cost_basis_eur"],
                        proceeds_eur=val,
                        realized_pnl_eur=pnl,
                        timestamp_utc=ts,
                    ))
                else:
                    # Credit arrived before consume (shouldn't happen with
                    # chronological ordering, but handle gracefully)
                    sell_credits[trade_id] = {
                        "proceeds_eur": val,
                        "timestamp_utc": ts,
                    }

        else:
            # --- Inventory entry ---
            if kind == "consume":
                # SELL fill: compute cost basis at current ACB
                if position_units > 0:
                    cost_per_unit = position_cost_eur / position_units
                    cost_basis = val * cost_per_unit
                    position_cost_eur -= cost_basis
                else:
                    cost_basis = Decimal("0")
                position_units -= val

                if trade_id is not None:
                    # Check if credit already arrived
                    if trade_id in sell_credits:
                        sc = sell_credits.pop(trade_id)
                        pnl = sc["proceeds_eur"] - cost_basis
                        results.append(RoundTripPnL(
                            trade_id=trade_id,
                            sold_units=val,
                            cost_basis_eur=cost_basis,
                            proceeds_eur=sc["proceeds_eur"],
                            realized_pnl_eur=pnl,
                            timestamp_utc=sc["timestamp_utc"],
                        ))
                    else:
                        pending_sells[trade_id] = {
                            "sold_units": val,
                            "cost_basis_eur": cost_basis,
                        }

            elif kind == "increase":
                position_units += val

            elif kind in ("bootstrap", None):
                position_units += val

            # reserve and release don't affect position_units or position_cost_eur

    return results


# ============================================================
# Unrealized P&L
# ============================================================

def unrealized_pnl(state: LedgerState, mark_price: Decimal) -> Decimal:
    """Unrealized P&L = mark_value - cost_basis. Includes reserved units (pending sells)."""
    total_units = state.position_units + state.reserved_units
    if total_units <= 0:
        return Decimal("0")
    mark_value = mark_price * total_units
    return mark_value - state.position_cost_eur


def portfolio_value(state: LedgerState, mark_price: Decimal) -> Decimal:
    """Total portfolio value = all EUR (free + reserved) + mark value of all units."""
    total_eur = state.free_eur + state.reserved_eur
    total_units = state.position_units + state.reserved_units
    return total_eur + mark_price * total_units
