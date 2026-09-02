from decimal import Decimal
from .base import EventSchema, EnvelopeRule

InventoryIncreased = EventSchema(
    event_type="InventoryIncreased",
    domain="inventory",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"strategy_id", "trade_id", "symbol"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "quantity": Decimal,
    },
    payload_optional={}
)

InventoryDecreased = EventSchema(
    event_type="InventoryDecreased",
    domain="inventory",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"strategy_id", "trade_id", "symbol"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "quantity": Decimal,
    },
    payload_optional={}
)