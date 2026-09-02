# tests/execution/unit/test_sizing_engine.py

"""Unit tests for aurono.execution.sizing.engine.size_buy/size_sell.

The buy and sell sizing functions both treat `quote_eur_requested` as a
fee-inclusive ceiling: total capital outflow on a buy stays ≤ buy_eur, and
post-fee proceeds on a sell stay ≥ sell_eur. These tests pin that contract.
"""

from decimal import Decimal

import pytest

from aurono.execution.sizing.engine import size_buy, size_sell


# ============================================================
# size_buy — fee-inclusive ceiling
# ============================================================


def test_size_buy_total_outflow_does_not_exceed_buy_eur():
    """Regression for the SUI-EUR case: with buy_eur=€10 and a 0.3% fee, the
    sum of asset cost + fee must stay at or below €10. The pre-fix engine
    sized so asset cost = €10, then added fee on top (€10.03 outflow).
    """
    result = size_buy(
        quote_eur_requested=Decimal("10"),
        free_eur=Decimal("100"),
        price_used=Decimal("1.0929"),
        tick_size=Decimal("0.000001"),
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0.003"),
    )
    assert result
    total_outflow = result["quote_eur_used"] + result["max_fee_eur"]
    assert total_outflow <= Decimal("10"), (
        f"total_outflow={total_outflow} must be ≤ buy_eur=10"
    )


def test_size_buy_zero_fee_matches_pre_change_behavior():
    """With fee_rate=0 the engine has no fee buffer, so quote_used should
    equal the requested amount (modulo tick flooring). This guards against
    accidental shrinkage on fee-free venues.
    """
    result = size_buy(
        quote_eur_requested=Decimal("10"),
        free_eur=Decimal("100"),
        price_used=Decimal("2"),
        tick_size=Decimal("0.0001"),
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0"),
    )
    assert result
    assert result["quote_eur_used"] == Decimal("10")
    assert result["base_units"] == Decimal("5")
    assert result["max_fee_eur"] == Decimal("0")


def test_size_buy_free_eur_constraint_still_binds_when_tight():
    """When free_eur is the tighter constraint (smaller than buy_eur), the
    final outflow must respect free_eur (no overdraw).
    """
    result = size_buy(
        quote_eur_requested=Decimal("50"),
        free_eur=Decimal("10"),
        price_used=Decimal("1"),
        tick_size=Decimal("0.0001"),
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0.003"),
    )
    assert result
    total_outflow = result["quote_eur_used"] + result["max_fee_eur"]
    assert total_outflow <= Decimal("10"), (
        f"total_outflow={total_outflow} must be ≤ free_eur=10"
    )


def test_size_buy_rejects_when_below_min_order_size():
    """If the fee-adjusted, tick-floored units fall under min_order_size,
    sizing must reject (return empty dict) rather than emit a tiny order.
    """
    result = size_buy(
        quote_eur_requested=Decimal("1"),
        free_eur=Decimal("100"),
        price_used=Decimal("100"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),  # too high for €1 budget
        fee_rate=Decimal("0.003"),
    )
    assert result == {}


def test_size_buy_higher_fee_shrinks_acquired_units():
    """A higher fee rate must reduce the units acquired (since more of the
    budget goes to fees), but total outflow must still be ≤ buy_eur.
    """
    low_fee = size_buy(
        quote_eur_requested=Decimal("100"),
        free_eur=Decimal("1000"),
        price_used=Decimal("10"),
        tick_size=Decimal("0.0001"),
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0.001"),
    )
    high_fee = size_buy(
        quote_eur_requested=Decimal("100"),
        free_eur=Decimal("1000"),
        price_used=Decimal("10"),
        tick_size=Decimal("0.0001"),
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0.01"),
    )
    assert low_fee["base_units"] > high_fee["base_units"]
    for r in (low_fee, high_fee):
        total = r["quote_eur_used"] + r["max_fee_eur"]
        assert total <= Decimal("100")


# ============================================================
# Buy/sell symmetry
# ============================================================


def test_size_buy_sell_symmetry_around_buy_eur_and_sell_eur():
    """The semantic contract:
      - buy_eur is the maximum total capital outflow (cost + fee ≤ buy_eur).
      - sell_eur is the minimum proceeds (price × units × (1 − fee_rate) ≥ sell_eur).
    Both should hit their boundary at the same tick resolution given the
    same fee rate and price.
    """
    fee = Decimal("0.003")
    price = Decimal("100")
    tick = Decimal("0.0001")

    buy = size_buy(
        quote_eur_requested=Decimal("50"),
        free_eur=Decimal("1000"),
        price_used=price,
        tick_size=tick,
        min_order_size=Decimal("0"),
        fee_rate=fee,
    )
    sell = size_sell(
        quote_eur_requested=Decimal("50"),
        free_units=Decimal("100"),
        price_used=price,
        tick_size=tick,
        min_order_size=Decimal("0"),
        fee_rate=fee,
    )

    # Buy: total outflow ≤ requested (strict — buy floor reduces outflow).
    assert buy["quote_eur_used"] + buy["max_fee_eur"] <= Decimal("50")
    # Sell: post-fee proceeds ≈ requested. Flooring units to tick can leave a
    # gap of up to (tick × price) below the request; tolerate that.
    sell_proceeds = sell["quote_eur_used"] - sell["max_fee_eur"]
    tick_gap = tick * price
    assert sell_proceeds >= Decimal("50") - tick_gap


# ============================================================
# size_sell — unchanged behavior, pinned for symmetry
# ============================================================


def test_size_sell_proceeds_meet_or_exceed_request():
    """size_sell aims for proceeds AFTER fees ≈ quote_eur_requested. Flooring
    units to tick can leave a gap of up to (tick × price) below the request;
    pin that bound here so any future engine change keeps the buy/sell
    symmetry.
    """
    price = Decimal("1.0929")
    tick = Decimal("0.000001")
    result = size_sell(
        quote_eur_requested=Decimal("10"),
        free_units=Decimal("100"),
        price_used=price,
        tick_size=tick,
        min_order_size=Decimal("0"),
        fee_rate=Decimal("0.003"),
    )
    assert result
    proceeds = result["quote_eur_used"] - result["max_fee_eur"]
    assert proceeds >= Decimal("10") - (tick * price)


# ============================================================
# Input validation (unchanged behavior, pinned)
# ============================================================


@pytest.mark.parametrize("bad_field, bad_value", [
    ("quote_eur_requested", Decimal("0")),
    ("quote_eur_requested", Decimal("-1")),
    ("free_eur", Decimal("0")),
    ("free_eur", Decimal("-1")),
    ("price_used", Decimal("0")),
    ("price_used", Decimal("-1")),
    ("fee_rate", Decimal("-0.001")),
])
def test_size_buy_validates_inputs(bad_field: str, bad_value: Decimal):
    kwargs = {
        "quote_eur_requested": Decimal("10"),
        "free_eur": Decimal("100"),
        "price_used": Decimal("1"),
        "tick_size": Decimal("0.01"),
        "min_order_size": Decimal("0"),
        "fee_rate": Decimal("0.003"),
    }
    kwargs[bad_field] = bad_value
    with pytest.raises(ValueError):
        size_buy(**kwargs)
