# pct_change, safe_div, rounding helpers (pure)

from decimal import Decimal, getcontext

getcontext().prec = 28


def pct_change(prev: Decimal, last: Decimal) -> Decimal:
    if prev <= 0:
        return Decimal("0")
    return (last - prev) / prev * Decimal("100")
