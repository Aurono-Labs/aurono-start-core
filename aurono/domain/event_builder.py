from .decision_outcomes import DecisionOutcome, OutcomeType
from .events import (
    StrategyBuyIntentCreated,
    StrategySellIntentCreated,
    StrategyDecisionRejected,
    StrategyNoOp,
)
from . import reasons


def build_domain_event(
    *,
    outcome: DecisionOutcome,
    strategy_id: int,
    symbol: str,
    occurred_at,
):
    """
    Deterministic mapping from DecisionOutcome → DomainEvent.
    """

    if outcome.type == OutcomeType.BUY:
        return StrategyBuyIntentCreated(
            occurred_at=occurred_at,
            strategy_id=strategy_id,
            symbol=symbol,
            intent=outcome.intent,
            price_hint=outcome.metrics["close_price"],
        )

    if outcome.type == OutcomeType.SELL:
        return StrategySellIntentCreated(
            occurred_at=occurred_at,
            strategy_id=strategy_id,
            symbol=symbol,
            intent=outcome.intent,
            price_hint=outcome.metrics["close_price"],
        )

    if outcome.type == OutcomeType.NONE:
        return StrategyNoOp(
            occurred_at=occurred_at,
            strategy_id=strategy_id,
            symbol=symbol,
            reason_code=reasons.NO_SIGNAL,
            metrics=outcome.metrics,
        )

    # REJECTED
    return StrategyDecisionRejected(
        occurred_at=occurred_at,
        strategy_id=strategy_id,
        symbol=symbol,
        reason_code=outcome.reason_code,
        metrics=outcome.metrics,
    )
