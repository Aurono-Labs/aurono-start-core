from aurono.domain.decision_builder import build_outcome
from aurono.domain.types import Decision
from aurono.domain.decision_outcomes import OutcomeType
from aurono.domain import reasons


def test_hold_no_signal_maps_to_none():
    d = Decision(
        action="HOLD",
        reason_code=reasons.NO_SIGNAL,
        metrics={},
        order_intent=None,
    )

    o = build_outcome(d)

    assert o.type == OutcomeType.NONE
    assert o.reason_code == reasons.NO_SIGNAL
    assert o.intent is None


def test_hold_with_reason_maps_to_rejected():
    d = Decision(
        action="HOLD",
        reason_code=reasons.CAPITAL_INSUFFICIENT,
        metrics={},
        order_intent=None,
    )

    o = build_outcome(d)

    assert o.type == OutcomeType.REJECTED
    assert o.reason_code == reasons.CAPITAL_INSUFFICIENT
    assert o.intent is None
