from decimal import Decimal
from datetime import datetime

from aurono.domain.types import (
    StrategySpec,
    MarketWindow,
    PortfolioState,
    EvalContext,
    ConstraintState,
)
from aurono.domain.strategy_eval import evaluate_strategy
from aurono.domain import reasons


def test_buy_fails_if_allocated_capital_insufficient():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("5"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("90"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("50"),   # < buy_eur
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.CAPITAL_INSUFFICIENT
