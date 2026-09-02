# aurono/events/schemas/execution.py
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

OrderSized = EventSchema(
    event_type="OrderSized",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={
            "trade_id",
            "strategy_id",
            "strategy_version_id",
            "symbol",
            "exchange",
        },
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "side": str,
        "quote_eur_requested": Decimal,
        "price_used": Decimal,
        "price_source": str,
        "price_timestamp_utc": str,
        "base_units": Decimal,
        "quote_eur_used": Decimal,
        "fee_rate": Decimal,
        "max_fee_eur": Decimal,
        "tick_size": Decimal,
        "min_order_size": Decimal,
        "rounding_delta_eur": Decimal,
    },
    payload_optional={
        "limit_price": Decimal,
        "close_price": Decimal,
        # Trade-journal enrichment — emitted by aurono/runtime/evaluator.py
        # so the UI can show "price dropped X% from prev close" on each trade
        "prev_close_price": Decimal,
        "change_pct": Decimal,
    },
)

OrderSubmitted = EventSchema(
    event_type="OrderSubmitted",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "exchange"},
        optional=set(),
        forbidden={"strategy_version_id"},
    ),
    payload={
        "order_type": str,
    },
    payload_optional={
        "limit_price": Decimal,
    },
)

OrderAccepted = EventSchema(
    event_type="OrderAccepted",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "exchange_order_id": str,
    },
    payload_optional={}
)


OrderRejected = EventSchema(
    event_type="OrderRejected",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)

FundsReserved = EventSchema(
    event_type="FundsReserved",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "reserved_eur": Decimal,
    },
    payload_optional={}
)


InventoryReserved = EventSchema(
    event_type="InventoryReserved",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "reserved_units": Decimal,
    },
    payload_optional={}
)

OrderPartiallyFilled = EventSchema(
    event_type="OrderPartiallyFilled",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "filled_units": Decimal,
        "avg_price": Decimal,
        "fee_eur": Decimal,
    },
    payload_optional={}
)


OrderFullyFilled = EventSchema(
    event_type="OrderFullyFilled",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "filled_units": Decimal,
        "avg_price": Decimal,
        "fee_eur": Decimal,
    },
    payload_optional={}
)

OrderCancelled = EventSchema(
    event_type="OrderCancelled",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)

OrderFailed = EventSchema(
    event_type="OrderFailed",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)

FundsReleased = EventSchema(
    event_type="FundsReleased",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "released_eur": Decimal,
    },
    payload_optional={
        "consumed_eur": Decimal,
    }
)


InventoryReleased = EventSchema(
    event_type="InventoryReleased",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "symbol", "side"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "released_units": Decimal,
    },
    payload_optional={}
)


ExecutionAborted = EventSchema(
    event_type="ExecutionAborted",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        # Trade_id is allocated during the decision/intent emit upstream, so
        # by the time the gate fires post-sizing we have it. Strategy_id +
        # exchange + symbol let dashboards group aborts by destination.
        required={"trade_id", "strategy_id", "symbol", "exchange"},
        optional={"strategy_version_id", "side"},
        forbidden=set(),
    ),
    payload={
        "side": str,           # "buy" | "sell"
        "reason_code": str,    # e.g. BELOW_MIN_NOTIONAL
    },
    # Decimal values stay at top level so they round-trip through _json_safe
    # (which only stringifies top-level Decimal, not nested-dict values).
    payload_optional={
        "quote_eur_used": Decimal,
        "min_eur": Decimal,
        "exchange_resolved": str,
        "base_units": Decimal,
        "min_order_size": Decimal,
    },
)
