# tests/analytics/test_portfolio_value_reserved.py

"""
Tests that portfolio_value() and unrealized_pnl() correctly include
reserved EUR and reserved asset units.
"""

from decimal import Decimal
from aurono.analytics.pnl import portfolio_value, unrealized_pnl
from aurono.ledger.state import LedgerState


def _state(**overrides) -> LedgerState:
    defaults = dict(
        strategy_id=1,
        symbol="BTC",
        free_eur=Decimal("100"),
        position_units=Decimal("0"),
        position_cost_eur=Decimal("0"),
        reserved_eur=Decimal("0"),
        reserved_units=Decimal("0"),
    )
    defaults.update(overrides)
    return LedgerState(**defaults)


def test_portfolio_value_includes_reserved_eur():
    """Reserved EUR (locked for pending buy) counts toward portfolio value."""
    state = _state(free_eur=Decimal("80"), reserved_eur=Decimal("20"))
    mark = Decimal("50000")
    val = portfolio_value(state, mark)
    assert val == Decimal("100")  # 80 + 20 + 0*50000


def test_portfolio_value_includes_reserved_units():
    """Reserved units (locked for pending sell) valued at mark price."""
    state = _state(
        free_eur=Decimal("100"),
        position_units=Decimal("1"),
        position_cost_eur=Decimal("40000"),
        reserved_units=Decimal("0.5"),
    )
    mark = Decimal("50000")
    val = portfolio_value(state, mark)
    # 100 + 0 + 50000 * (1 + 0.5) = 100 + 75000 = 75100
    assert val == Decimal("75100")


def test_portfolio_value_both_reserved():
    """Both EUR and units reserved — everything still counts."""
    state = _state(
        free_eur=Decimal("50"),
        reserved_eur=Decimal("50"),
        position_units=Decimal("1"),
        position_cost_eur=Decimal("40000"),
        reserved_units=Decimal("0.5"),
    )
    mark = Decimal("50000")
    val = portfolio_value(state, mark)
    # (50 + 50) + 50000 * (1 + 0.5) = 100 + 75000 = 75100
    assert val == Decimal("75100")


def test_unrealized_pnl_includes_reserved_units():
    """Unrealized P&L covers position_units + reserved_units."""
    state = _state(
        position_units=Decimal("1"),
        position_cost_eur=Decimal("40000"),
        reserved_units=Decimal("0.5"),
    )
    mark = Decimal("50000")
    pnl = unrealized_pnl(state, mark)
    # mark_value = 50000 * 1.5 = 75000, cost = 40000 -> pnl = 35000
    assert pnl == Decimal("35000")


def test_unrealized_pnl_zero_when_no_position_and_no_reserved():
    state = _state(position_units=Decimal("0"), reserved_units=Decimal("0"))
    assert unrealized_pnl(state, Decimal("50000")) == Decimal("0")


def test_unrealized_pnl_only_reserved_units():
    """When all units are reserved (pending sell), still compute unrealized."""
    state = _state(
        position_units=Decimal("0"),
        position_cost_eur=Decimal("40000"),
        reserved_units=Decimal("1"),
    )
    mark = Decimal("50000")
    pnl = unrealized_pnl(state, mark)
    # total_units = 0 + 1 = 1, mark_value = 50000, cost = 40000
    assert pnl == Decimal("10000")
