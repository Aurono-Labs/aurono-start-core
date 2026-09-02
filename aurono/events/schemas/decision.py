#aurono/events/schemas/decision.py

from decimal import Decimal
from .base import EventSchema, EnvelopeRule

StrategyEvaluationStarted = EventSchema(
    event_type="StrategyEvaluationStarted",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "candle_timestamp": str,
    },
    payload_optional={}
)

BuyDecisionTriggered = EventSchema(
    event_type="BuyDecisionTriggered",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
        "trigger_threshold": Decimal,
        "reference_price": Decimal,
    },
    payload_optional={}
)

SellDecisionTriggered = EventSchema(
    event_type="SellDecisionTriggered",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
        "trigger_threshold": Decimal,
        "reference_price": Decimal,
    },
    payload_optional={}
)

NoActionDecision = EventSchema(
    event_type="NoActionDecision",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
    },
    payload_optional={}
)

DecisionRejected = EventSchema(
    event_type="DecisionRejected",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional={"symbol", "timeframe"},
        forbidden={"trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={"details": dict}
)

# decision.py  (bottom part only)

from decimal import Decimal
from .base import EventSchema, EnvelopeRule

StrategyBuyIntentCreatedSchema = EventSchema(
    event_type="StrategyBuyIntentCreated",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "side": str,           # always "buy"
        "quote_eur": Decimal,  # BUY intents carry EUR only
        "price_hint": Decimal, # candle close (NOT live price)
    },
    payload_optional={}
)

def _sell_intent_payload_invariant(payload: dict):
    quote = payload.get("quote_eur")
    base  = payload.get("base_units")

    if (quote is None and base is None) or (quote is not None and base is not None):
        raise ValueError(
            "StrategySellIntentCreated requires exactly one of "
            "'quote_eur' or 'base_units'"
        )

StrategySellIntentCreatedSchema = EventSchema(
    event_type="StrategySellIntentCreated",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "side": str,
        "price_hint": Decimal,
    },
    payload_optional={
        "quote_eur": Decimal,
        "base_units": Decimal,
    },
    payload_invariant=_sell_intent_payload_invariant,
)
