# aurono/domain/events.py

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .decision_outcomes import OutcomeType
from .types import OrderIntent


# ============================================================
# Base event
# ============================================================

@dataclass(frozen=True)
class DomainEvent:
    occurred_at: datetime
    strategy_id: int
    symbol: str


# ============================================================
# Concrete events
# ============================================================

@dataclass(frozen=True)
class StrategyBuyIntentCreated(DomainEvent):
    intent: OrderIntent
    price_hint: Decimal  # price used for EUR→units sizing


@dataclass(frozen=True)
class StrategySellIntentCreated(DomainEvent):
    intent: OrderIntent
    price_hint: Decimal  # price used for units→EUR sizing


@dataclass(frozen=True)
class StrategyDecisionRejected(DomainEvent):
    reason_code: str
    metrics: dict


@dataclass(frozen=True)
class StrategyNoOp(DomainEvent):
    reason_code: str          # always NO_SIGNAL
    metrics: dict

@dataclass(frozen=True)
class OrderSized(DomainEvent):
    """
    Final, fully-sized, constraint-validated order decision.
    This event authorizes execution but does not execute.
    """
    strategy_version_id: int
    exchange: str
    side: str                     # "buy" | "sell"

    # final executable values
    price: Decimal                # live price used for sizing
    amount_asset: Decimal         # final rounded asset amount
    notional_eur: Decimal         # price × amount (post-fees)
    rounding_delta_units: Decimal   # pre_round_amount_asset - post_round_amount_asset

    # provenance
    price_source: str             # "last_trade" | "mid_price"
    priced_at_utc: datetime       # timestamp the price was captured at

@dataclass(frozen=True)
class ExecutionAborted(DomainEvent):
    """
    Execution was aborted after intent creation but before execution.

    This is a terminal decision event explaining why no order was authorized.
    """

    side: str                     # "buy" | "sell"
    reason_code: str              # e.g. LIVE_PRICE_UNAVAILABLE, BELOW_MIN_NOTIONAL
    details: Optional[dict] = None
