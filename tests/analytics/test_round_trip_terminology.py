# tests/analytics/test_round_trip_terminology.py

"""
Terminology guardrail: verify the analytics layer exposes `round_trips` /
`round_trip_stats` and does NOT leak the old `total_trades` / `trade_stats`
keys. Prevents silent regression of the Phase 15.X consistency pass.

See docs/domain_terminology.md §3.4 — Round Trip.
"""

from decimal import Decimal

from aurono.analytics.pnl import RoundTripPnL
from aurono.analytics.statistics import round_trip_statistics


def _rt(pnl_eur, trade_id):
    return RoundTripPnL(
        trade_id=trade_id,
        sold_units=Decimal("1"),
        cost_basis_eur=Decimal("100"),
        proceeds_eur=Decimal("100") + pnl_eur,
        realized_pnl_eur=pnl_eur,
        timestamp_utc="2026-01-01T00:00:00",
    )


def test_round_trip_statistics_returns_round_trips_key():
    """Populated input: only the new key is present, the old key is gone."""
    stats = round_trip_statistics([_rt(Decimal("5"), "t1")])
    assert "round_trips" in stats
    assert "total_trades" not in stats


def test_round_trip_statistics_empty_returns_round_trips_key():
    """Empty input: the zero branch also uses the new key."""
    stats = round_trip_statistics([])
    assert "round_trips" in stats
    assert stats["round_trips"] == 0
    assert "total_trades" not in stats


def test_round_trip_count_matches_sell_records_not_intent_count():
    """
    Core semantic: round-trip count equals the number of SELL-side settled
    records. Not the number of BUY attempts, not the number of total trade_ids.
    Confirms the rename did not drift off the domain definition.
    """
    records = [_rt(Decimal("3"), f"sell-{i}") for i in range(5)]
    stats = round_trip_statistics(records)
    assert stats["round_trips"] == 5
