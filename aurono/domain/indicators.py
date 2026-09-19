# Technical indicators (pure math, no I/O)

"""
Mirrors frontend/src/lib/indicators.ts exactly — same algorithm, same
intermediate values — so Lab Simulate and live/shadow trading produce
identical trigger points for the same closing-price series. Pinned by
a shared parity fixture (see tests/domain/unit/test_rsi.py and
frontend/src/lib/__tests__/triggerAnalysis.test.ts).
"""

from decimal import Decimal
from typing import List, Optional


def rsi(closes: List[Decimal], period: int) -> List[Optional[Decimal]]:
    """RSI (Relative Strength Index), Wilder's smoothing. Returns one value
    per input close — None for the first `period` values (insufficient
    history to seed the average)."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: List[Optional[Decimal]] = [None] * period

    avg_gain = Decimal("0")
    avg_loss = Decimal("0")
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            avg_gain += change
        else:
            avg_loss += abs(change)
    avg_gain /= period
    avg_loss /= period

    result.append(
        Decimal("100") if avg_loss == 0
        else Decimal("100") - Decimal("100") / (1 + avg_gain / avg_loss)
    )

    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else Decimal("0")
        loss = abs(change) if change < 0 else Decimal("0")
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(
            Decimal("100") if avg_loss == 0
            else Decimal("100") - Decimal("100") / (1 + avg_gain / avg_loss)
        )

    return result
