# aurono/reports/generator.py

"""
Report data aggregation.

Pure functions that collect strategy performance data from existing
projections and analytics — no new data sources needed.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from aurono.analytics.pnl import compute_realized_pnl
from aurono.analytics.statistics import strategy_summary, round_trip_statistics
from aurono.ledger.build_state import build_ledger_state


@dataclass
class StrategyReport:
    strategy_id: str
    name: str
    status: str
    symbol: str
    timeframe: str
    portfolio_value: float
    unrealized_pnl: float
    realized_pnl: float
    free_eur: float
    position_units: float
    acb_price: float
    round_trips: int
    win_rate: float
    last_evaluated_at: Optional[str] = None
    period_fills: int = 0           # fills (BUY + SELL) in report period
    period_realized_pnl: float = 0  # realized P&L in period
    # Portfolio value at the earliest in-period snapshot (0 if none exists).
    period_start_value: float = 0
    # portfolio_value - period_start_value. For strategies with no trades in
    # the period this equals the unrealized-P&L drift; for strategies that
    # traded, it captures the net value change regardless of source.
    period_unrealized_delta: float = 0
    # True if any StrategyCreated/Activated/Paused/Archived event fired in the
    # period — used by the digest classifier to always surface the strategy.
    state_changes_in_period: bool = False


@dataclass
class TradeEntry:
    """A completed trade in the report period."""
    strategy_name: str
    symbol: str
    side: str
    units: float
    price: float
    fee: float
    timestamp: str


@dataclass
class BlockedSignal:
    """A buy/sell signal that couldn't execute."""
    strategy_name: str
    symbol: str
    action: str
    reason: str
    close_price: float
    change_pct: float
    timestamp: str


@dataclass
class EvalIssue:
    """An evaluation error or skip in the report period."""
    strategy_name: str
    timeframe: str
    outcome: str       # "skipped" or "error"
    detail: str        # skip_reason or error_message
    timestamp: str


@dataclass
class EvalHealth:
    """Evaluation health summary for the report period."""
    total: int
    evaluated: int
    issues: List[EvalIssue] = field(default_factory=list)


@dataclass
class ReportData:
    generated_at: str
    period_label: str               # "Daily Report" or "Weekly Report"
    period_start: str
    period_end: str
    strategies: List[StrategyReport] = field(default_factory=list)
    total_portfolio_value: float = 0
    total_unrealized_pnl: float = 0
    total_realized_pnl: float = 0
    # Period-scoped totals — what the hero line should lead with.
    period_portfolio_delta: float = 0
    period_realized_pnl: float = 0
    milestones: List[Dict[str, str]] = field(default_factory=list)
    trades: List[TradeEntry] = field(default_factory=list)
    blocked_signals: List[BlockedSignal] = field(default_factory=list)
    eval_health: Optional[EvalHealth] = None


def _get_mark_price(conn: sqlite3.Connection, strategy_id: str, symbol: str) -> Optional[Decimal]:
    """Get latest mark price from snapshots."""
    row = conn.execute(
        """
        SELECT close_price FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND close_price IS NOT NULL
        ORDER BY timestamp_utc DESC LIMIT 1
        """,
        (strategy_id,),
    ).fetchone()
    if row and row["close_price"]:
        return Decimal(str(row["close_price"]))
    return None


def _get_period_start_value(
    conn: sqlite3.Connection, strategy_id: str, period_start: str,
) -> float:
    """
    Portfolio value at the start of the report period.

    Prefers the latest snapshot *before* period_start (so the delta compares
    against state entering the period). Falls back to the earliest snapshot
    *within* the period for strategies that have no prior history. Returns 0
    when no snapshots exist at all — the caller treats zero as "no baseline",
    which skips the mover rule for this strategy.
    """
    row = conn.execute(
        """
        SELECT portfolio_value_eur FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND timestamp_utc < ? AND portfolio_value_eur IS NOT NULL
        ORDER BY timestamp_utc DESC LIMIT 1
        """,
        (strategy_id, period_start),
    ).fetchone()
    if row and row["portfolio_value_eur"] is not None:
        return float(row["portfolio_value_eur"])

    row = conn.execute(
        """
        SELECT portfolio_value_eur FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND timestamp_utc >= ? AND portfolio_value_eur IS NOT NULL
        ORDER BY timestamp_utc ASC LIMIT 1
        """,
        (strategy_id, period_start),
    ).fetchone()
    if row and row["portfolio_value_eur"] is not None:
        return float(row["portfolio_value_eur"])
    return 0.0


def _has_state_change(
    conn: sqlite3.Connection, strategy_id: str, period_start: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM events
        WHERE strategy_id = ?
          AND timestamp_utc >= ?
          AND event_type IN ('StrategyCreated', 'StrategyActivated', 'StrategyPaused', 'StrategyArchived')
        LIMIT 1
        """,
        (strategy_id, period_start),
    ).fetchone()
    return row is not None


def generate_report(
    conn: sqlite3.Connection,
    *,
    frequency: str,
    now: Optional[datetime] = None,
) -> ReportData:
    """
    Generate a performance report for all non-archived strategies.

    Args:
        conn: Database connection (row_factory must be sqlite3.Row)
        frequency: "daily" or "weekly"
        now: Current timestamp (default: utcnow)
    """
    now = now or datetime.now(timezone.utc)

    if frequency == "daily":
        period_start = (now - timedelta(days=1)).isoformat()
        period_label = "Daily Report"
    else:
        period_start = (now - timedelta(weeks=1)).isoformat()
        period_label = "Weekly Report"

    period_end = now.isoformat()

    # Get all non-archived strategies
    rows = conn.execute(
        """
        SELECT
            ssp.strategy_id,
            s.name,
            ssp.status,
            ssp.last_evaluated_at,
            sv.parameters_json
        FROM strategy_state_projection ssp
        JOIN strategy s ON s.strategy_id = ssp.strategy_id
        JOIN strategy_version sv ON sv.strategy_version_id = ssp.active_version_id
        WHERE ssp.status != 'archived'
        """,
    ).fetchall()

    strategies: List[StrategyReport] = []
    total_pv = 0.0
    total_upnl = 0.0
    total_rpnl = 0.0

    for row in rows:
        params = json.loads(row["parameters_json"])
        symbol = params.get("symbol", "")
        timeframe = params.get("timeframe", "")
        base_asset = symbol.split("-", 1)[0] if "-" in symbol else symbol

        mark_price = _get_mark_price(conn, row["strategy_id"], symbol)
        if mark_price is None:
            mark_price = Decimal("0")

        # Build ledger state for current values
        state = build_ledger_state(conn, strategy_id=row["strategy_id"], symbol=base_asset)

        from aurono.analytics.pnl import (
            unrealized_pnl as compute_unrealized,
            portfolio_value as compute_portfolio_value,
        )
        u_pnl = float(compute_unrealized(state, mark_price))
        p_value = float(compute_portfolio_value(state, mark_price))

        # Realized P&L
        pnl_records = compute_realized_pnl(conn, strategy_id=row["strategy_id"], symbol=symbol)
        stats = round_trip_statistics(pnl_records)

        # Period-specific realized P&L (from sell fills)
        period_records = [r for r in pnl_records if r.timestamp_utc >= period_start]
        period_rpnl = float(sum(r.realized_pnl_eur for r in period_records))

        # Period fill count — all filled orders (buy + sell). Named "fills" (not
        # "trades") because a fill is the execution event; a trade may exist
        # without a fill. See domain_terminology.md §3.3.
        period_fill_count = conn.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE strategy_id = ? AND event_type = 'OrderFullyFilled'
              AND timestamp_utc >= ?
            """,
            (row["strategy_id"], period_start),
        ).fetchone()[0]

        period_start_value = _get_period_start_value(conn, row["strategy_id"], period_start)
        period_value_delta = p_value - period_start_value if period_start_value > 0 else 0.0
        state_changed = _has_state_change(conn, row["strategy_id"], period_start)

        strat_report = StrategyReport(
            strategy_id=row["strategy_id"],
            name=row["name"] or row["strategy_id"][:8],
            status=row["status"],
            symbol=symbol,
            timeframe=timeframe,
            portfolio_value=p_value,
            unrealized_pnl=u_pnl,
            realized_pnl=float(stats["total_realized_pnl"]),
            free_eur=float(state.free_eur),
            position_units=float(state.position_units),
            acb_price=float(state.acb_price) if state.acb_price else 0.0,
            round_trips=stats["round_trips"],
            win_rate=float(stats["win_rate"]),
            last_evaluated_at=row["last_evaluated_at"],
            period_fills=period_fill_count,
            period_realized_pnl=period_rpnl,
            period_start_value=period_start_value,
            period_unrealized_delta=period_value_delta,
            state_changes_in_period=state_changed,
        )
        strategies.append(strat_report)

        total_pv += p_value
        total_upnl += u_pnl
        total_rpnl += float(stats["total_realized_pnl"])

    # Milestones from the period
    from aurono.analytics.milestones import detect_all_milestones
    all_milestones = detect_all_milestones(conn)
    period_milestones = [
        {"label": m.label, "description": m.description, "achieved_at": m.achieved_at}
        for m in all_milestones
        if m.achieved_at >= period_start
    ]

    # Trades in the period (OrderFullyFilled events)
    trade_rows = conn.execute(
        """
        SELECT e.timestamp_utc, e.strategy_id, e.symbol, e.side, e.payload_json,
               s.name AS strategy_name
        FROM events e
        LEFT JOIN strategy s ON e.strategy_id = s.strategy_id
        WHERE e.event_type = 'OrderFullyFilled'
          AND e.timestamp_utc >= ?
        ORDER BY e.timestamp_utc DESC
        """,
        (period_start,),
    ).fetchall()

    trades: List[TradeEntry] = []
    for tr in trade_rows:
        payload = json.loads(tr["payload_json"])
        trades.append(TradeEntry(
            strategy_name=tr["strategy_name"] or tr["strategy_id"][:8],
            symbol=tr["symbol"] or "",
            side=tr["side"] or "",
            units=float(payload.get("filled_units", 0)),
            price=float(payload.get("avg_price", 0)),
            fee=float(payload.get("fee_eur", 0)),
            timestamp=tr["timestamp_utc"],
        ))

    # Blocked signals: decision events where a buy/sell signal was blocked
    blocked_reasons = {
        "CAPITAL_INSUFFICIENT": "BUY",
        "INVENTORY_INSUFFICIENT": "SELL",
        "BELOW_ACB": "SELL",
        "SELL_BELOW_MIN_POSITION": "SELL",
        "COOLDOWN_ACTIVE": None,  # could be either
        "NO_ACB": "SELL",
    }
    blocked_rows = conn.execute(
        """
        SELECT e.timestamp_utc, e.strategy_id, e.symbol, e.payload_json,
               s.name AS strategy_name
        FROM events e
        LEFT JOIN strategy s ON e.strategy_id = s.strategy_id
        WHERE e.event_type = 'DecisionObserved'
          AND e.timestamp_utc >= ?
        ORDER BY e.timestamp_utc DESC
        """,
        (period_start,),
    ).fetchall()

    blocked: List[BlockedSignal] = []
    for br in blocked_rows:
        payload = json.loads(br["payload_json"])
        reason = payload.get("reason_code", "")
        if reason not in blocked_reasons:
            continue
        # Metrics are nested inside payload.metrics
        metrics = payload.get("metrics", {})
        # The intended action (BUY/SELL) — not HOLD
        intended_action = blocked_reasons.get(reason) or "BUY"
        blocked.append(BlockedSignal(
            strategy_name=br["strategy_name"] or br["strategy_id"][:8],
            symbol=br["symbol"] or "",
            action=intended_action,
            reason=reason,
            close_price=float(metrics.get("close_price", 0)),
            change_pct=float(metrics.get("change_pct", 0)),
            timestamp=br["timestamp_utc"],
        ))

    # Evaluation health from evaluation_log table
    eval_health: Optional[EvalHealth] = None
    try:
        eval_total_row = conn.execute(
            "SELECT COUNT(*) FROM evaluation_log WHERE cycle_ts >= ?",
            (period_start,),
        ).fetchone()
        eval_total = eval_total_row[0] if eval_total_row else 0

        eval_ok_row = conn.execute(
            "SELECT COUNT(*) FROM evaluation_log WHERE cycle_ts >= ? AND outcome = 'evaluated'",
            (period_start,),
        ).fetchone()
        eval_ok = eval_ok_row[0] if eval_ok_row else 0

        issue_rows = conn.execute(
            """
            SELECT cycle_ts, strategy_name, timeframe, outcome, skip_reason, error_message
            FROM evaluation_log
            WHERE cycle_ts >= ? AND outcome IN ('skipped', 'error')
            ORDER BY cycle_ts DESC
            """,
            (period_start,),
        ).fetchall()

        eval_issues: List[EvalIssue] = []
        for ir in issue_rows:
            detail = ir["error_message"] or ir["skip_reason"] or ""
            eval_issues.append(EvalIssue(
                strategy_name=ir["strategy_name"],
                timeframe=ir["timeframe"],
                outcome=ir["outcome"],
                detail=detail,
                timestamp=ir["cycle_ts"],
            ))

        eval_health = EvalHealth(
            total=eval_total,
            evaluated=eval_ok,
            issues=eval_issues,
        )
    except Exception:
        pass  # evaluation_log table may not exist yet

    period_portfolio_delta = sum(s.period_unrealized_delta for s in strategies)
    period_realized_pnl_total = sum(s.period_realized_pnl for s in strategies)

    return ReportData(
        generated_at=now.isoformat(),
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        strategies=strategies,
        total_portfolio_value=total_pv,
        total_unrealized_pnl=total_upnl,
        total_realized_pnl=total_rpnl,
        period_portfolio_delta=period_portfolio_delta,
        period_realized_pnl=period_realized_pnl_total,
        milestones=period_milestones,
        trades=trades,
        blocked_signals=blocked,
        eval_health=eval_health,
    )
