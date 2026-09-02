# aurono/analytics/milestones.py

"""
Query-time milestone detection.

Computes milestones on-the-fly from existing projection tables and events.
No new projection table, no changes to the event pipeline.
"""

from dataclasses import dataclass
from typing import List, Optional


# ============================================================
# Data types
# ============================================================

@dataclass(frozen=True)
class Milestone:
    milestone_type: str     # "first_trade", "trade_count", "portfolio_peak", etc.
    milestone_key: str      # unique key e.g. "trade_count_10"
    label: str              # short display label
    description: str        # human sentence
    achieved_at: str        # ISO timestamp
    strategy_id: str


# ============================================================
# Detector functions
# ============================================================

def _detect_first_trade(conn, strategy_id: str, name: str) -> List[Milestone]:
    """First completed round trip (sell fill) for this strategy.

    Uses SELL OrderFullyFilled so the milestone aligns with the round-trip
    count shown on strategy cards (which counts realized P&L records = sell
    fills). See docs/domain_terminology.md §3.4.
    """
    row = conn.execute(
        """
        SELECT timestamp_utc FROM events
        WHERE strategy_id = ? AND event_type = 'OrderFullyFilled'
          AND side = 'sell'
        ORDER BY timestamp_utc ASC
        LIMIT 1
        """,
        (strategy_id,),
    ).fetchone()

    if not row:
        return []

    return [Milestone(
        milestone_type="first_trade",
        milestone_key="first_trade",
        label="First round trip",
        description=f"{name} completed its first round trip",
        achieved_at=row["timestamp_utc"],
        strategy_id=strategy_id,
    )]


_TRADE_THRESHOLDS = [10, 25, 50, 100]


def _detect_trade_count(conn, strategy_id: str, name: str) -> List[Milestone]:
    """Check round-trip count thresholds using ROW_NUMBER to get Nth sell fill timestamp.

    Counts only sell fills to match the round-trip count shown on strategy
    cards. See docs/domain_terminology.md §3.4.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE strategy_id = ? AND event_type = 'OrderFullyFilled' AND side = 'sell'",
        (strategy_id,),
    ).fetchone()[0]

    milestones: List[Milestone] = []
    for threshold in _TRADE_THRESHOLDS:
        if total < threshold:
            break
        # Get the Nth sell fill timestamp
        row = conn.execute(
            """
            SELECT timestamp_utc FROM (
                SELECT timestamp_utc, ROW_NUMBER() OVER (ORDER BY timestamp_utc) AS rn
                FROM events
                WHERE strategy_id = ? AND event_type = 'OrderFullyFilled'
                  AND side = 'sell'
            ) WHERE rn = ?
            """,
            (strategy_id, threshold),
        ).fetchone()
        if row:
            milestones.append(Milestone(
                milestone_type="trade_count",
                milestone_key=f"trade_count_{threshold}",
                label=f"{threshold} round trips",
                description=f"{name} completed {threshold} round trips",
                achieved_at=row["timestamp_utc"],
                strategy_id=strategy_id,
            ))
    return milestones


def _detect_portfolio_peak(conn, strategy_id: str, name: str) -> List[Milestone]:
    """Fire if latest snapshot has BOTH higher portfolio value AND more asset
    units than at the previous all-time-high snapshot.

    This ensures ATH only fires when the strategy genuinely accumulated more
    coins at a higher total value — not from price drift on a static position,
    deposits, or post-sell peaks.
    """
    # Need at least 2 snapshots and at least one completed trade
    count = conn.execute(
        "SELECT COUNT(*) FROM portfolio_snapshot_projection WHERE strategy_id = ? AND portfolio_value_eur IS NOT NULL",
        (strategy_id,),
    ).fetchone()[0]
    if count < 2:
        return []

    trade_count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE strategy_id = ? AND event_type = 'OrderFullyFilled' AND side = 'sell'",
        (strategy_id,),
    ).fetchone()[0]
    if trade_count == 0:
        return []

    # Get latest snapshot
    latest = conn.execute(
        """
        SELECT portfolio_value_eur, asset_units, timestamp_utc
        FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND portfolio_value_eur IS NOT NULL
        ORDER BY timestamp_utc DESC LIMIT 1
        """,
        (strategy_id,),
    ).fetchone()
    if not latest or latest["portfolio_value_eur"] is None:
        return []

    # Find the previous peak: the snapshot with the highest portfolio value,
    # excluding the latest one.  If no previous peak, compare against the
    # earliest snapshot (creation state).
    previous_peak = conn.execute(
        """
        SELECT portfolio_value_eur, asset_units
        FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND portfolio_value_eur IS NOT NULL
          AND timestamp_utc < ?
        ORDER BY portfolio_value_eur DESC
        LIMIT 1
        """,
        (strategy_id, latest["timestamp_utc"]),
    ).fetchone()

    if not previous_peak:
        return []

    prev_value = previous_peak["portfolio_value_eur"] or 0
    prev_units = previous_peak["asset_units"] or 0

    # Both conditions must be met: more value AND more units
    if latest["portfolio_value_eur"] <= prev_value:
        return []
    if (latest["asset_units"] or 0) <= prev_units:
        return []

    return [Milestone(
        milestone_type="portfolio_peak",
        milestone_key="portfolio_peak",
        label="All-time high",
        description=f"{name} reached a new all-time high",
        achieved_at=latest["timestamp_utc"],
        strategy_id=strategy_id,
    )]


# ============================================================
# Aggregators
# ============================================================

def detect_milestones(conn, strategy_id: str, name: str) -> List[Milestone]:
    """Run all detectors for a single strategy.

    Each detector is isolated — a failure in one doesn't prevent the others
    from returning results.
    """
    import logging
    logger = logging.getLogger(__name__)

    detectors = [
        _detect_first_trade,
        _detect_trade_count,
        _detect_portfolio_peak,
    ]

    results: List[Milestone] = []
    for detector in detectors:
        try:
            results.extend(detector(conn, strategy_id, name))
        except Exception:
            logger.warning(
                "Milestone detector %s failed for strategy %s",
                detector.__name__, strategy_id, exc_info=True,
            )
    return results


def detect_all_milestones(conn) -> List[Milestone]:
    """Run milestone detection across all strategies."""
    rows = conn.execute(
        """
        SELECT p.strategy_id, s.name
        FROM strategy_state_projection p
        LEFT JOIN strategy s ON p.strategy_id = s.strategy_id
        """
    ).fetchall()

    all_milestones: List[Milestone] = []
    for row in rows:
        name = row["name"] or row["strategy_id"][:8]
        all_milestones.extend(detect_milestones(conn, row["strategy_id"], name))

    return all_milestones
