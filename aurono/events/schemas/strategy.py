from .base import EventSchema, EnvelopeRule

StrategyCreated = EventSchema(
    event_type="StrategyCreated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"strategy_version_id", "trade_id"},
    ),
    payload={
        "name": str,
    },
    payload_optional={}
)

StrategyVersionCreated = EventSchema(
    event_type="StrategyVersionCreated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "parameters": dict,
        "previous_version_id": (str, type(None)),
    },
    payload_optional={}
)

StrategyActivated = EventSchema(
    event_type="StrategyActivated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={},
    payload_optional={}
)

StrategyPaused = EventSchema(
    event_type="StrategyPaused",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional={"strategy_version_id"},
        forbidden={"trade_id"},
    ),
    payload={},
    payload_optional={"reason": str}
)

StrategyArchived = EventSchema(
    event_type="StrategyArchived",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"strategy_version_id", "trade_id"},
    ),
    payload={},
    payload_optional={}
)

from decimal import Decimal
from aurono.events.schemas.base import EventSchema, EnvelopeRule


StrategyBuyIntentCreated = EventSchema(
    event_type="StrategyBuyIntentCreated",
    domain="strategy",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "symbol"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "side": str,            # always "buy"
        "base_units": Decimal,  # domain-calculated units
        "price_hint": Decimal,  # candle close price (NOT live price)
    },
    payload_optional={},
)
