from decimal import Decimal
from .base import EventSchema, EnvelopeRule


def _capital_currency_invariant(payload: dict):
    currency = payload.get("currency")
    if currency != "EUR":
        raise ValueError(
            f"Capital event currency must be 'EUR', got '{currency}'"
        )


CapitalCredited = EventSchema(
    event_type="CapitalCredited",
    domain="capital",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "amount": Decimal,
        "currency": str,
    },
    payload_optional={},
    payload_invariant=_capital_currency_invariant,
)

CapitalDebited = EventSchema(
    event_type="CapitalDebited",
    domain="capital",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "amount": Decimal,
        "currency": str,
    },
    payload_optional={},
    payload_invariant=_capital_currency_invariant,
)

CapitalReserved = EventSchema(
    event_type="CapitalReserved",
    domain="capital",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "trade_id"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "currency": str,
        "amount": Decimal,
    },
    payload_optional={},
    payload_invariant=_capital_currency_invariant,
)