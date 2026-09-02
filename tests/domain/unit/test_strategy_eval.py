from decimal import Decimal
from datetime import datetime

from aurono.domain.strategy_eval import evaluate_strategy
from aurono.domain.types import (
    StrategySpec,
    MarketWindow,
    PortfolioState,
    EvalContext,
    ConstraintState,
)
from aurono.domain import reasons

def test_buy_triggered_when_drop_and_capital_ok():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),  # −15%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED
    assert decision.order_intent is not None

def test_buy_rejected_when_capital_insufficient():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("50"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.CAPITAL_INSUFFICIENT

def test_sell_blocked_below_acb():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("110"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("120"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.BELOW_ACB

def test_hold_no_signal():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("102"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("100"),
            asset_units=Decimal("1"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.NO_SIGNAL

