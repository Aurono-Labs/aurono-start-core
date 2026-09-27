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

# The market value of a bootstrapped position at the moment it was bootstrapped.
#
# Emitted alongside InventoryBootstrapped, never instead of it. The two numbers
# answer different questions and both are needed: acb_price is the behavioural
# sell floor the strategy trades against, while mark_price is the fiat that
# actually flowed in, which is what any return or benchmark figure has to use.
#
# A separate event rather than a field on InventoryBootstrapped because events
# are immutable: the 43 historical bootstraps cannot grow a field, but they can
# be followed by a derived record.
#
# source_* fields record which candle the price came from, so the derivation is
# auditable rather than implied. source_lag_seconds is the gap between the
# bootstrap and the candle that priced it.
InventoryBootstrapMarked = EventSchema(
    event_type="InventoryBootstrapMarked",
    domain="system",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "symbol"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "bootstrap_event_id": str,
        "units": Decimal,
        "mark_price": Decimal,
        "mark_value_eur": Decimal,
        "source_timeframe": str,
        "source_timestamp_ms": int,
        "source_lag_seconds": int,
    },
    payload_optional={},
)

# The market value of a position still held when its strategy was archived: the
# other end of the boundary InventoryBootstrapMarked opens.
#
# Archiving withdraws the strategy's EUR but emits no inventory event, so the
# coins stay in inventory_balance_projection while the strategy drops out of
# every aggregate query. Without this record the value leaving Aurono's
# measurement boundary is never stated, and what the read path reports depends
# on luck: frozen at the archive-date snapshot when one exists, drifting on a
# live ticker when it does not, and zero when neither resolves. That last case
# is not marginal - a strategy whose fallback resolves to nothing reports
# losing its entire invested base while it is still holding the coins.
#
# Same payload field names as InventoryBootstrapMarked on purpose - the Activity
# detail renderer and its EUR/price formatting keys are shared, and the two
# events are the same kind of fact about opposite ends of a strategy's life.
#
# No back-pointer, unlike the bootstrap mark's bootstrap_event_id. A strategy
# can have several bootstraps, so that mark has to say which one it prices; a
# strategy is archived exactly once, so strategy_id plus event type is already
# unambiguous and an archived_event_id would only be a field to keep correct.
InventoryExitMarked = EventSchema(
    event_type="InventoryExitMarked",
    domain="system",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "symbol"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "units": Decimal,
        "mark_price": Decimal,
        "mark_value_eur": Decimal,
        "source_timeframe": str,
        "source_timestamp_ms": int,
        "source_lag_seconds": int,
    },
    payload_optional={},
)
