from decimal import Decimal, ROUND_FLOOR

def floor_to_tick(x: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise ValueError("tick_size must be > 0")
    if x <= 0:
        return Decimal("0")
    steps = (x / tick).to_integral_value(rounding=ROUND_FLOOR)
    return steps * tick
