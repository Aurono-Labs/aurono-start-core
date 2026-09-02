from aurono.domain.types import Decision

def test_hold_must_have_reason_code():
    d = Decision(
        action="HOLD",
        reason_code="NO_SIGNAL",
        metrics={},
        order_intent=None,
    )

    assert d.reason_code is not None
