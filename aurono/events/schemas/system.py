from .base import EventSchema, EnvelopeRule

SystemStartup = EventSchema(
    event_type="SystemStartup",
    domain="system",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required=set(),
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id", "symbol", "timeframe"},
    ),
    payload={},
    payload_optional={}
)

KillSwitchActivated = EventSchema(
    event_type="KillSwitchActivated",
    domain="system",
    actor_types={"system", "user"},
    envelope=EnvelopeRule(
        required=set(),
        optional={"strategy_id"},
        forbidden={"trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)

from decimal import Decimal
from aurono.events.schemas.base import EventSchema, EnvelopeRule

InventoryBootstrapped = EventSchema(
    event_type="InventoryBootstrapped",
    domain="system",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "symbol"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "initial_units": Decimal,
        "acb_price": Decimal,
    },
    payload_optional={},
)
