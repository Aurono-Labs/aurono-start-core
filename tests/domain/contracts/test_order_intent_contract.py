# tests/domain/contracts/test_order_intent_contract.py

"""
Contract tests for OrderIntent.

OrderIntent is the boundary between domain decisions and execution.
It must carry ONLY intent data — never exchange-computed fields.
"""

import dataclasses
from decimal import Decimal

import pytest

from aurono.domain.types import OrderIntent


# ============================================================
# Field composition — allowed fields only
# ============================================================

ALLOWED_FIELDS = {"side", "symbol", "quote_eur", "base_units", "notes"}

FORBIDDEN_FIELDS = {
    "price_used",
    "price",
    "fees",
    "fee_eur",
    "tick_rounding",
    "rounding_delta_units",
    "notional_eur",
    "amount_asset",
    "price_source",
    "exchange",
}


def test_order_intent_contains_only_allowed_fields():
    """
    OrderIntent must contain only intent-level fields.
    No execution-computed fields are permitted.
    """
    actual = set(OrderIntent.__annotations__)
    assert actual == ALLOWED_FIELDS, (
        f"OrderIntent fields changed. "
        f"Added: {actual - ALLOWED_FIELDS}, Removed: {ALLOWED_FIELDS - actual}"
    )


def test_order_intent_excludes_execution_fields():
    """
    Regression guard: execution-computed fields must never leak
    into OrderIntent.
    """
    annotations = OrderIntent.__annotations__
    for field in FORBIDDEN_FIELDS:
        assert field not in annotations, (
            f"Forbidden field '{field}' found in OrderIntent"
        )


# ============================================================
# Immutability
# ============================================================

def test_order_intent_is_frozen():
    """OrderIntent must be an immutable (frozen) dataclass."""
    assert dataclasses.is_dataclass(OrderIntent)
    assert OrderIntent.__dataclass_params__.frozen


# ============================================================
# BUY intent validation
# ============================================================

def test_buy_intent_requires_quote_eur():
    with pytest.raises(ValueError, match="requires quote_eur"):
        OrderIntent(side="buy", symbol="BTC-EUR")


def test_buy_intent_rejects_base_units():
    with pytest.raises(ValueError, match="must not define base_units"):
        OrderIntent(
            side="buy",
            symbol="BTC-EUR",
            quote_eur=Decimal("100"),
            base_units=Decimal("0.5"),
        )


def test_buy_intent_valid():
    intent = OrderIntent(
        side="buy", symbol="BTC-EUR", quote_eur=Decimal("100")
    )
    assert intent.side == "buy"
    assert intent.quote_eur == Decimal("100")
    assert intent.base_units is None


# ============================================================
# SELL intent validation
# ============================================================

def test_sell_intent_requires_units_or_eur():
    with pytest.raises(ValueError, match="requires base_units or quote_eur"):
        OrderIntent(side="sell", symbol="BTC-EUR")


def test_sell_intent_rejects_both_units_and_eur():
    with pytest.raises(ValueError, match="must not define both"):
        OrderIntent(
            side="sell",
            symbol="BTC-EUR",
            base_units=Decimal("0.5"),
            quote_eur=Decimal("100"),
        )


def test_sell_intent_valid_by_units():
    intent = OrderIntent(
        side="sell", symbol="BTC-EUR", base_units=Decimal("0.5")
    )
    assert intent.side == "sell"
    assert intent.base_units == Decimal("0.5")
    assert intent.quote_eur is None


def test_sell_intent_valid_by_eur():
    intent = OrderIntent(
        side="sell", symbol="BTC-EUR", quote_eur=Decimal("100")
    )
    assert intent.side == "sell"
    assert intent.quote_eur == Decimal("100")
    assert intent.base_units is None
