# aurono/analytics/statistics.py

"""
Portfolio and round-trip statistics computed from P&L records and snapshot series.
"""

from decimal import Decimal
from typing import List, Dict, Optional

from aurono.analytics.pnl import RoundTripPnL


def round_trip_statistics(pnl_records: List[RoundTripPnL]) -> Dict[str, object]:
    """
    Compute round-trip-level statistics from realized P&L records.

    A round trip is a completed BUY→…→SELL pair (see domain_terminology.md §3.4).
    This function counts round trips, not trades: a single BUY attempt that has
    not yet been closed with a matching SELL is a trade but not a round trip.

    Returns:
        round_trips: int
        winners: int
        losers: int
        breakeven: int
        win_rate: Decimal (0-1)
        total_realized_pnl: Decimal
        avg_win: Decimal (0 if no winners)
        avg_loss: Decimal (0 if no losers)
        profit_factor: Decimal | None (None if no losses)
        best_trade: Decimal
        worst_trade: Decimal
    """
    if not pnl_records:
        return {
            "round_trips": 0,
            "winners": 0,
            "losers": 0,
            "breakeven": 0,
            "win_rate": Decimal("0"),
            "total_realized_pnl": Decimal("0"),
            "avg_win": Decimal("0"),
            "avg_loss": Decimal("0"),
            "profit_factor": None,
            "best_trade": Decimal("0"),
            "worst_trade": Decimal("0"),
        }

    pnls = [r.realized_pnl_eur for r in pnl_records]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]

    total_wins = sum(wins) if wins else Decimal("0")
    total_losses = sum(abs(l) for l in losses) if losses else Decimal("0")

    return {
        "round_trips": len(pnl_records),
        "winners": len(wins),
        "losers": len(losses),
        "breakeven": len(breakeven),
        "win_rate": Decimal(str(len(wins))) / Decimal(str(len(pnl_records))),
        "total_realized_pnl": sum(pnls),
        "avg_win": total_wins / Decimal(str(len(wins))) if wins else Decimal("0"),
        "avg_loss": -(total_losses / Decimal(str(len(losses)))) if losses else Decimal("0"),
        "profit_factor": total_wins / total_losses if total_losses > 0 else None,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
    }


def max_drawdown(
    value_series: List[tuple],
) -> Dict[str, object]:
    """
    Compute maximum drawdown from a portfolio value time series.

    Args:
        value_series: [(timestamp_utc, portfolio_value_eur), ...]

    Returns:
        max_drawdown_eur: Decimal (positive number)
        max_drawdown_pct: Decimal (0-1)
        peak_value: Decimal
        trough_value: Decimal
        peak_timestamp: str
        trough_timestamp: str
    """
    if not value_series:
        return {
            "max_drawdown_eur": Decimal("0"),
            "max_drawdown_pct": Decimal("0"),
            "peak_value": Decimal("0"),
            "trough_value": Decimal("0"),
            "peak_timestamp": None,
            "trough_timestamp": None,
        }

    peak_val = Decimal(str(value_series[0][1]))
    peak_ts = value_series[0][0]

    dd_eur = Decimal("0")
    dd_pct = Decimal("0")
    dd_peak_val = peak_val
    dd_peak_ts = peak_ts
    dd_trough_val = peak_val
    dd_trough_ts = peak_ts

    for ts, val_raw in value_series:
        val = Decimal(str(val_raw))

        if val > peak_val:
            peak_val = val
            peak_ts = ts

        if peak_val > 0:
            current_dd = peak_val - val
            current_dd_pct = current_dd / peak_val

            if current_dd > dd_eur:
                dd_eur = current_dd
                dd_pct = current_dd_pct
                dd_peak_val = peak_val
                dd_peak_ts = peak_ts
                dd_trough_val = val
                dd_trough_ts = ts

    return {
        "max_drawdown_eur": dd_eur,
        "max_drawdown_pct": dd_pct,
        "peak_value": dd_peak_val,
        "trough_value": dd_trough_val,
        "peak_timestamp": dd_peak_ts,
        "trough_timestamp": dd_trough_ts,
    }


def strategy_summary(
    conn,
    *,
    strategy_id: int,
    symbol: str,
    mark_price: Decimal,
) -> Dict[str, object]:
    """
    Full strategy analytics: realized P&L + unrealized + drawdown + trade stats.
    """
    from aurono.analytics.pnl import (
        compute_realized_pnl,
        unrealized_pnl as compute_unrealized,
        portfolio_value as compute_portfolio_value,
    )
    from aurono.ledger.build_state import build_ledger_state

    base_asset = symbol.split("-", 1)[0] if "-" in symbol else symbol

    # Realized P&L
    pnl_records = compute_realized_pnl(conn, strategy_id=strategy_id, symbol=symbol)
    stats = round_trip_statistics(pnl_records)

    # Current state
    state = build_ledger_state(conn, strategy_id=strategy_id, symbol=base_asset)
    u_pnl = compute_unrealized(state, mark_price)
    p_value = compute_portfolio_value(state, mark_price)

    # Drawdown from snapshots (if available)
    snapshots = conn.execute(
        """
        SELECT timestamp_utc, portfolio_value_eur
        FROM portfolio_snapshot_projection
        WHERE strategy_id = ?
        ORDER BY timestamp_utc
        """,
        (str(strategy_id),),
    ).fetchall()

    dd = max_drawdown(snapshots)

    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "mark_price": mark_price,
        "portfolio_value": p_value,
        "unrealized_pnl": u_pnl,
        "position_units": state.position_units,
        "acb_price": state.acb_price,
        "free_eur": state.free_eur,
        "reserved_eur": state.reserved_eur,
        "reserved_units": state.reserved_units,
        "round_trip_stats": stats,
        "drawdown": dd,
        "pnl_records": pnl_records,
    }
