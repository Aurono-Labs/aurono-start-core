# tests/analytics/test_statistics.py

"""
Phase 12.4 — Round-trip statistics and drawdown tests.
"""

from decimal import Decimal

import pytest

from aurono.analytics.pnl import RoundTripPnL
from aurono.analytics.statistics import round_trip_statistics, max_drawdown


# ============================================================
# Round-trip Statistics Tests
# ============================================================

def _make_pnl(pnl_eur, trade_id="t1"):
    return RoundTripPnL(
        trade_id=trade_id,
        sold_units=Decimal("1"),
        cost_basis_eur=Decimal("100"),
        proceeds_eur=Decimal("100") + pnl_eur,
        realized_pnl_eur=pnl_eur,
        timestamp_utc="2025-01-01T00:00:00",
    )


def test_round_trip_statistics_basic():
    """Mixed wins and losses."""
    records = [
        _make_pnl(Decimal("20"), "t1"),   # win
        _make_pnl(Decimal("-10"), "t2"),   # loss
        _make_pnl(Decimal("30"), "t3"),    # win
        _make_pnl(Decimal("-5"), "t4"),    # loss
    ]
    stats = round_trip_statistics(records)

    assert stats["round_trips"] == 4
    assert stats["winners"] == 2
    assert stats["losers"] == 2
    assert stats["win_rate"] == Decimal("0.5")
    assert stats["total_realized_pnl"] == Decimal("35")
    assert stats["avg_win"] == Decimal("25")    # (20+30)/2
    assert stats["avg_loss"] == Decimal("-7.5")  # -(10+5)/2
    assert stats["profit_factor"] == Decimal("50") / Decimal("15")  # 3.33...
    assert stats["best_trade"] == Decimal("30")
    assert stats["worst_trade"] == Decimal("-10")


def test_round_trip_statistics_all_wins():
    """100% win rate."""
    records = [
        _make_pnl(Decimal("10"), "t1"),
        _make_pnl(Decimal("20"), "t2"),
    ]
    stats = round_trip_statistics(records)

    assert stats["winners"] == 2
    assert stats["losers"] == 0
    assert stats["win_rate"] == Decimal("1")
    assert stats["avg_loss"] == Decimal("0")
    assert stats["profit_factor"] is None  # no losses


def test_round_trip_statistics_all_losses():
    """0% win rate."""
    records = [
        _make_pnl(Decimal("-10"), "t1"),
        _make_pnl(Decimal("-20"), "t2"),
    ]
    stats = round_trip_statistics(records)

    assert stats["winners"] == 0
    assert stats["losers"] == 2
    assert stats["win_rate"] == Decimal("0")
    assert stats["avg_win"] == Decimal("0")
    assert stats["total_realized_pnl"] == Decimal("-30")


def test_round_trip_statistics_no_round_trips():
    """Empty input."""
    stats = round_trip_statistics([])

    assert stats["round_trips"] == 0
    assert stats["win_rate"] == Decimal("0")
    assert stats["total_realized_pnl"] == Decimal("0")
    assert stats["profit_factor"] is None


# ============================================================
# Max Drawdown Tests
# ============================================================

def test_max_drawdown_basic():
    """Peak at 1000, drops to 800, recovers to 1100, drops to 900."""
    series = [
        ("t1", 1000),
        ("t2", 1050),  # new peak
        ("t3", 800),   # trough (dd = 250 from 1050)
        ("t4", 1100),  # new peak
        ("t5", 900),   # dd = 200 from 1100
    ]
    dd = max_drawdown(series)

    assert dd["max_drawdown_eur"] == Decimal("250")
    assert dd["peak_value"] == Decimal("1050")
    assert dd["trough_value"] == Decimal("800")
    assert dd["peak_timestamp"] == "t2"
    assert dd["trough_timestamp"] == "t3"


def test_max_drawdown_monotonic_up():
    """Monotonically increasing -> no drawdown."""
    series = [
        ("t1", 100),
        ("t2", 200),
        ("t3", 300),
    ]
    dd = max_drawdown(series)

    assert dd["max_drawdown_eur"] == Decimal("0")
    assert dd["max_drawdown_pct"] == Decimal("0")


def test_max_drawdown_single_drop():
    """Single continuous drop."""
    series = [
        ("t1", 1000),
        ("t2", 900),
        ("t3", 800),
        ("t4", 700),
    ]
    dd = max_drawdown(series)

    assert dd["max_drawdown_eur"] == Decimal("300")
    assert dd["peak_value"] == Decimal("1000")
    assert dd["trough_value"] == Decimal("700")


def test_max_drawdown_empty():
    """Empty series."""
    dd = max_drawdown([])

    assert dd["max_drawdown_eur"] == Decimal("0")
    assert dd["peak_timestamp"] is None


def test_max_drawdown_pct():
    """Verify percentage calculation."""
    series = [
        ("t1", 1000),
        ("t2", 800),  # 20% drawdown
    ]
    dd = max_drawdown(series)

    assert dd["max_drawdown_pct"] == Decimal("0.2")
