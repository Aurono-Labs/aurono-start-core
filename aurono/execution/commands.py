from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ExecutionCommand:
    """
    System-level execution command.

    This represents an instruction to attempt an exchange order.
    It is NOT a domain concept and contains NO strategy logic.

    Invariants:
    - units is already EUR→units sized by the domain
    - adapters MUST NOT resize or reinterpret units
    - adapters MAY quantize (tick size), but must emit events if rejected
    """

    created_at: datetime          # injected timestamp (no now())
    trade_id: str                 # unique per execution attempt
    strategy_id: int              # owning strategy
    symbol: str                   # e.g. "BTCEUR"
    side: str                     # "buy" | "sell"

    units: Decimal                # base units to trade (authoritative)
    price_hint: Decimal           # price used for sizing (audit only)

    exchange: str                 # target exchange (routing, not logic)

    limit_price: Decimal | None = None  # limit price for limit orders (None = market)
