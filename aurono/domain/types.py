# dataclasses / pydantic-free plain types

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Literal
from datetime import datetime

@dataclass(frozen=True)
class StrategySpec:
    symbol: str
    timeframe: str
    buy_drop_pct: Decimal      # e.g. Decimal("10.0") for -10%
    sell_rise_pct: Decimal     # e.g. Decimal("15.0") for +15%
    buy_eur: Decimal
    sell_eur: Decimal
    min_position_units: Optional[Decimal] = None  # reject sell if remaining < this
    cooldown_periods: Optional[int] = None         # skip N timeframe periods between same-side trades
    cooldown_reset_on_opposite: bool = True         # opposite-side trade resets cooldown timer

@dataclass(frozen=True)
class ConstraintState:
    last_buy_at: Optional[datetime] = None
    last_sell_at: Optional[datetime] = None

@dataclass(frozen=True)
class MarketWindow:
    prev_close: Decimal        # previous closed candle
    last_close: Decimal        # most recent closed candle


@dataclass(frozen=True)
class PortfolioState:
    free_eur: Decimal
    asset_units: Decimal
    acb_price: Optional[Decimal]  # avg cost per unit, None if no position


@dataclass(frozen=True)
class EvalContext:
    asof: datetime             # injected timestamp, no now()

@dataclass(frozen=True)
class OrderIntent:
    side: Literal["buy", "sell"]
    symbol: str

    # BUY: required
    quote_eur: Optional[Decimal] = None

    # SELL: required (unless strategy explicitly sells by quote_eur)
    base_units: Optional[Decimal] = None

    notes: Optional[str] = None

    def __post_init__(self):
        if self.side == "buy":
            if self.quote_eur is None:
                raise ValueError("BUY OrderIntent requires quote_eur")
            if self.base_units is not None:
                raise ValueError("BUY OrderIntent must not define base_units")

        if self.side == "sell":
            if self.base_units is None and self.quote_eur is None:
                raise ValueError(
                    "SELL OrderIntent requires base_units or quote_eur"
                )
            if self.base_units is not None and self.quote_eur is not None:
                raise ValueError(
                    "SELL OrderIntent must not define both base_units and quote_eur"
                )


@dataclass(frozen=True)
class Decision:
    action: Literal["HOLD", "BUY", "SELL"]
    reason_code: str
    metrics: dict
    order_intent: Optional[OrderIntent] = None

@dataclass(frozen=True)
class Rejection:
    code: str
    message: str
    metrics: dict

