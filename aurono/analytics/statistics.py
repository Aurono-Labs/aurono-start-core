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

    Two different P&L numbers come out of here and they answer different
    questions. Both are correct; using one where the other belongs is the
    defect this docstring exists to prevent.

    `unrealized_pnl` is `mark_value - position_cost_eur`: how far the position
    sits from its average cost basis. That is the sell floor the ACB-protection
    rule acts on, so it is the right number wherever the label says
    "unrealized".

    `total_pnl_eur` is `portfolio_value - net_invested_eur`: what the strategy
    has actually made since it started. It is the right number wherever the
    label says "overall", "total" or "performance".

    They diverge on a strategy that started with a position already in hand.
    `position_cost_eur` holds `initial_units * acb_price`, a price the user
    paid before Aurono existed, while `net_invested_eur` holds the market value
    at bootstrap - the fiat that actually came in. Measuring performance
    against the former charges the strategy for a gap it did not create. On
    the dev instance that inverted the verdict on the largest position: a BTC
    strategy 179 days old with zero trades read -68.32 against cost basis and
    +73.42 against what came in.
    """
    from aurono.analytics.benchmarks import net_injected_capital
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

    # What the user actually put in: cash credits net of withdrawals, plus the
    # market value of any bootstrapped starting position. Falls back to the
    # cost_basis row per bootstrap when no mark exists, so an unbackfilled
    # strategy reads exactly as it did before this shipped.
    net_invested = net_injected_capital(conn, str(strategy_id))

    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "mark_price": mark_price,
        "portfolio_value": p_value,
        "unrealized_pnl": u_pnl,
        "net_invested_eur": net_invested,
        "total_pnl_eur": p_value - net_invested,
        "position_units": state.position_units,
        "acb_price": state.acb_price,
        "free_eur": state.free_eur,
        "reserved_eur": state.reserved_eur,
        "reserved_units": state.reserved_units,
        "round_trip_stats": stats,
        "drawdown": dd,
        "pnl_records": pnl_records,
    }
