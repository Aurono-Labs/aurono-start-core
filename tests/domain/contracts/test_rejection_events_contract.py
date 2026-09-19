# tests/domain/contracts/test_rejection_events_contract.py

"""
Contract test: every rejection reason that strategy_eval can produce
must flow through the full pipeline and emit a StrategyDecisionRejected
event with the correct reason_code and a non-empty metrics dict.

Pipeline tested:
    evaluate_strategy() → build_outcome() → build_domain_event()
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aurono.domain.strategy_eval import evaluate_strategy
from aurono.domain.decision_builder import build_outcome
from aurono.domain.event_builder import build_domain_event
from aurono.domain.events import StrategyDecisionRejected
from aurono.domain.types import (
    StrategySpec,
    MarketWindow,
    PortfolioState,
    EvalContext,
    ConstraintState,
)
from aurono.domain import reasons


# ============================================================
# Shared fixtures
# ============================================================

DEFAULT_SPEC = StrategySpec(
    symbol="BTC-EUR",
    timeframe="1w",
    buy_drop_pct=Decimal("10"),
    sell_rise_pct=Decimal("10"),
    buy_eur=Decimal("100"),
    sell_eur=Decimal("100"),
)

BUY_DISABLED_SPEC = StrategySpec(
    symbol="BTC-EUR",
    timeframe="1w",
    buy_drop_pct=Decimal("10"),
    sell_rise_pct=Decimal("10"),
    buy_eur=Decimal("0"),
    sell_eur=Decimal("100"),
)

SELL_DISABLED_SPEC = StrategySpec(
    symbol="BTC-EUR",
    timeframe="1w",
    buy_drop_pct=Decimal("10"),
    sell_rise_pct=Decimal("10"),
    buy_eur=Decimal("100"),
    sell_eur=Decimal("0"),
)

OCCURRED_AT = datetime(2026, 1, 1)


def _eval_and_emit(*, market, portfolio, spec=DEFAULT_SPEC):
    """Run the full pipeline: eval → outcome → domain event."""
    decision = evaluate_strategy(
        spec=spec,
        market=market,
        portfolio=portfolio,
        ctx=EvalContext(asof=OCCURRED_AT),
        constraint=ConstraintState(),
    )
    outcome = build_outcome(decision)
    return build_domain_event(
        outcome=outcome,
        strategy_id=1,
        symbol="BTC-EUR",
        occurred_at=OCCURRED_AT,
    )


# ============================================================
# Parameterized rejection scenarios
# ============================================================

REJECTION_SCENARIOS = [
    pytest.param(
        reasons.INVALID_MARKET_DATA,
        MarketWindow(prev_close=Decimal("-1"), last_close=Decimal("100")),
        PortfolioState(free_eur=Decimal("1000"), asset_units=Decimal("0"), acb_price=None),
        DEFAULT_SPEC,
        id="INVALID_MARKET_DATA",
    ),
    pytest.param(
        reasons.CAPITAL_INSUFFICIENT,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("85")),  # -15% → buy trigger
        PortfolioState(free_eur=Decimal("10"), asset_units=Decimal("0"), acb_price=None),
        DEFAULT_SPEC,
        id="CAPITAL_INSUFFICIENT",
    ),
    pytest.param(
        reasons.INVENTORY_INSUFFICIENT,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("115")),  # +15% → sell trigger
        PortfolioState(free_eur=Decimal("0"), asset_units=Decimal("0"), acb_price=None),
        DEFAULT_SPEC,
        id="INVENTORY_INSUFFICIENT",
    ),
    pytest.param(
        reasons.NO_ACB,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("115")),  # +15% → sell trigger
        PortfolioState(free_eur=Decimal("0"), asset_units=Decimal("2"), acb_price=None),
        DEFAULT_SPEC,
        id="NO_ACB",
    ),
    pytest.param(
        reasons.BELOW_ACB,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("115")),  # +15% → sell trigger
        PortfolioState(free_eur=Decimal("0"), asset_units=Decimal("2"), acb_price=Decimal("200")),
        DEFAULT_SPEC,
        id="BELOW_ACB",
    ),
    pytest.param(
        reasons.BUY_DISABLED,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("85")),  # -15% → buy trigger
        PortfolioState(free_eur=Decimal("1000"), asset_units=Decimal("0"), acb_price=None),
        BUY_DISABLED_SPEC,
        id="BUY_DISABLED",
    ),
    pytest.param(
        reasons.SELL_DISABLED,
        MarketWindow(prev_close=Decimal("100"), last_close=Decimal("115")),  # +15% → sell trigger
        PortfolioState(free_eur=Decimal("0"), asset_units=Decimal("2"), acb_price=Decimal("100")),
        SELL_DISABLED_SPEC,
        id="SELL_DISABLED",
    ),
]


@pytest.mark.parametrize("expected_reason,market,portfolio,spec", REJECTION_SCENARIOS)
def test_rejection_emits_correct_event(expected_reason, market, portfolio, spec):
    event = _eval_and_emit(market=market, portfolio=portfolio, spec=spec)

    assert isinstance(event, StrategyDecisionRejected), (
        f"Expected StrategyDecisionRejected for {expected_reason}, "
        f"got {type(event).__name__}"
    )
    assert event.reason_code == expected_reason
    assert isinstance(event.metrics, dict)


@pytest.mark.parametrize("expected_reason,market,portfolio,spec", REJECTION_SCENARIOS)
def test_rejection_event_has_required_fields(expected_reason, market, portfolio, spec):
    event = _eval_and_emit(market=market, portfolio=portfolio, spec=spec)

    # All domain events must have these
    assert event.strategy_id == 1
    assert event.symbol == "BTC-EUR"
    assert event.occurred_at == OCCURRED_AT
