# aurono/events/schemas/base.py

from dataclasses import dataclass
from typing import Dict, Set, Type, Callable, Optional

# -------------------------------------------------
# Canonical envelope field names (global contract)
# -------------------------------------------------

ENVELOPE_FIELDS = {
    "strategy_id",
    "strategy_version_id",
    "trade_id",
    "exchange",
    "symbol",
    "timeframe",
    "side",
}

@dataclass(frozen=True)
class EnvelopeRule:
    required: Set[str]
    optional: Set[str]
    forbidden: Set[str]


@dataclass(frozen=True)
class EventSchema:
    event_type: str
    domain: str

    actor_types: Set[str]
    envelope: EnvelopeRule

    payload: Dict[str, Type]
    payload_optional: Dict[str, Type]

    payload_invariant: Optional[Callable[[dict], None]] = None
