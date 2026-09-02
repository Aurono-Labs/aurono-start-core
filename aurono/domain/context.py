from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EvalContext:
    """
    Immutable evaluation context.

    Carries external facts that are NOT part of strategy spec:
    - evaluation timestamp
    """
    asof: datetime
