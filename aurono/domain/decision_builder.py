from .types import Decision
from .decision_outcomes import DecisionOutcome, OutcomeType
from . import reasons


def build_outcome(decision: Decision) -> DecisionOutcome:
    """
    Canonical mapping from internal Decision → external Outcome.
    """

    if decision.action == "BUY":
        return DecisionOutcome(
            type=OutcomeType.BUY,
            intent=decision.order_intent,
            reason_code=None,
            metrics=decision.metrics,
        )

    if decision.action == "SELL":
        return DecisionOutcome(
            type=OutcomeType.SELL,
            intent=decision.order_intent,
            reason_code=None,
            metrics=decision.metrics,
        )

    # HOLD
    if decision.reason_code == reasons.NO_SIGNAL:
        return DecisionOutcome(
            type=OutcomeType.NONE,
            intent=None,
            reason_code=reasons.NO_SIGNAL,
            metrics=decision.metrics,
        )

    # HOLD with reason → rejected
    return DecisionOutcome(
        type=OutcomeType.REJECTED,
        intent=None,
        reason_code=decision.reason_code,
        metrics=decision.metrics,
    )
