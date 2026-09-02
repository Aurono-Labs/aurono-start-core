from datetime import datetime
from decimal import Decimal

from aurono.domain.strategy_eval import evaluate_strategy
from aurono.domain.types import (
    StrategySpec,
    MarketWindow,
    PortfolioState,
    EvalContext,
    ConstraintState,
)
from aurono.domain import reasons


def test_sell_triggered_when_price_above_acb():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),  # +20%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.order_intent is not None
    assert decision.order_intent.side == "sell"

def test_sell_triggered_when_rise_and_inventory_ok():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("1.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.order_intent is not None
    assert decision.order_intent.side == "sell"
    assert decision.order_intent.quote_eur == Decimal("100")
    assert decision.order_intent.base_units is None


def test_sell_blocked_min_position():
    """Sell rejected when remaining position would drop below min_position_units."""
    # Price = 120, sell_eur = 100 → sell_units = 100/120 ≈ 0.833
    # Position = 1.0, remaining = 1.0 - 0.833 = 0.167 < 0.5 → blocked
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0.5"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),  # +20% → sell signal
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("1.0"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.MIN_POSITION_BREACH


def test_sell_allowed_above_min_position():
    """Sell passes when remaining position stays above min_position_units."""
    # Price = 120, sell_eur = 100 → sell_units = 100/120 ≈ 0.833
    # Position = 2.0, remaining = 2.0 - 0.833 = 1.167 > 0.5 → allowed
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0.5"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2.0"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED


def test_min_position_zero_means_no_constraint():
    """min_position_units=0 should not block any sell."""
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("0.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"


def test_min_position_none_means_no_constraint():
    """min_position_units=None (unset) should not block any sell."""
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("0.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
