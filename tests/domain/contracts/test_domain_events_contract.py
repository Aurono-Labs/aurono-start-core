from datetime import datetime
from decimal import Decimal

from aurono.domain.event_builder import build_domain_event
from aurono.domain.decision_outcomes import DecisionOutcome, OutcomeType
from aurono.domain.types import OrderIntent
from aurono.domain import reasons
from aurono.domain.events import (
    StrategyBuyIntentCreated,
    StrategyDecisionRejected,
)

def test_buy_outcome_creates_buy_intent_event():
    outcome = DecisionOutcome(
        type=OutcomeType.BUY,
        intent=OrderIntent(
            side="buy",
            symbol="BTCEUR",
            quote_eur=Decimal("100"),
        ),
        reason_code=None,
        metrics={
            "close_price": Decimal("32000"),
        },
    )

    event = build_domain_event(
        outcome=outcome,
        strategy_id=1,
        symbol="BTCEUR",
        occurred_at=datetime(2026, 1, 1),
    )

    assert isinstance(event, StrategyBuyIntentCreated)
    assert event.price_hint == Decimal("32000")
    assert event.intent.quote_eur == Decimal("100")

def test_rejected_outcome_creates_rejection_event():
    outcome = DecisionOutcome(
        type=OutcomeType.REJECTED,
        intent=None,
        reason_code=reasons.CAPITAL_INSUFFICIENT,
        metrics={"free_eur": 10},
    )

    event = build_domain_event(
        outcome=outcome,
        strategy_id=1,
        symbol="BTCEUR",
        occurred_at=datetime(2026, 1, 1),
    )

    assert isinstance(event, StrategyDecisionRejected)
    assert event.reason_code == reasons.CAPITAL_INSUFFICIENT

def test_intent_events_always_have_positive_price_hint():
    outcome = DecisionOutcome(
        type=OutcomeType.BUY,
        intent=OrderIntent(
            side="buy",
            symbol="BTCEUR",
            quote_eur=Decimal("100"),
        ),
        reason_code=None,
        metrics={"close_price": Decimal("1")},
    )

    event = build_domain_event(
        outcome=outcome,
        strategy_id=1,
        symbol="BTCEUR",
        occurred_at=datetime(2026, 1, 1),
    )

    assert event.price_hint > 0

