from dataclasses import dataclass
from typing import Optional
from .types import OrderIntent


class OutcomeType:
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class DecisionOutcome:
    type: str                       # BUY | SELL | NONE | REJECTED
    intent: Optional[OrderIntent]   # only for BUY / SELL
    reason_code: Optional[str]      # only for NONE / REJECTED
    metrics: dict
