from decimal import Decimal
from .base import EventSchema, EnvelopeRule

MarketCandleClosed = EventSchema(
    event_type="MarketCandleClosed",
    domain="market",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"exchange", "symbol", "timeframe"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id"},
    ),
    payload={
        "candle_timestamp": str,
        "open": Decimal,
        "high": Decimal,
        "low": Decimal,
        "close": Decimal,
        "volume": Decimal,
    },
    payload_optional={}
)

MarketDataUnavailable = EventSchema(
    event_type="MarketDataUnavailable",
    domain="market",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"exchange", "symbol", "timeframe"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)