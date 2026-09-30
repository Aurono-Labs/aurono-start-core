# aurono/domain/strategy_params.py

"""
Strategy parameter validation.

The engine's own check on what a strategy version may contain. The bounds
match the frontend's `validateParams()` (strategyParamsFormHelpers.ts) exactly,
so a strategy the form accepts is never refused here; what this adds is
enforcement for any caller that is not the form, plus type checks the form
gets for free from its inputs. `tests/fixtures/strategy_param_cases.json` is
run against both implementations, which is what keeps them from drifting.

Only keys the engine reads are checked. Anything else (execution metadata
such as `product_id`) passes through untouched.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

# The timeframes the engine can evaluate - the same set strategy_eval.py
# converts to minutes for the cooldown.
TIMEFRAMES = ("1h", "4h", "1d", "1w")

REQUIRED_PARAMETERS = {
    "symbol", "exchange", "timeframe",
    "buy_drop_pct", "sell_rise_pct", "buy_eur", "sell_eur",
}


def _number(value: Any) -> Optional[Decimal]:
    """A finite number, or None. Numeric strings count; bools do not."""
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _integer(value: Any) -> Optional[int]:
    d = _number(value)
    if d is None or d != d.to_integral_value():
        return None
    return int(d)


def validate_parameters(params: Dict[str, Any]) -> Dict[str, str]:
    """
    Every problem with `params`, keyed by field. Empty means valid.

    All errors are reported together so a caller fixes them in one pass.
    """
    errs: Dict[str, str] = {}

    for key in ("symbol", "exchange"):
        v = params.get(key)
        if not isinstance(v, str) or not v.strip():
            errs[key] = f"{key} is required."

    if params.get("timeframe") not in TIMEFRAMES:
        errs["timeframe"] = f"timeframe must be one of {', '.join(TIMEFRAMES)}."

    for key in ("buy_drop_pct", "sell_rise_pct"):
        v = _number(params.get(key))
        if v is None or v <= 0:
            errs[key] = f"{key} must be a positive number."

    amounts = {}
    for key in ("buy_eur", "sell_eur"):
        v = _number(params.get(key))
        if v is None or v < 0:
            errs[key] = f"{key} must be zero or more (0 disables that side)."
        amounts[key] = v
    if "buy_eur" not in errs and "sell_eur" not in errs:
        if amounts["buy_eur"] <= 0 and amounts["sell_eur"] <= 0:
            errs["buy_eur"] = "At least one side (buy or sell) must have a positive amount."

    if params.get("min_position_units") is not None:
        v = _number(params["min_position_units"])
        if v is None or v < 0:
            errs["min_position_units"] = "min_position_units cannot be negative."

    if params.get("cooldown_periods") is not None:
        v = _integer(params["cooldown_periods"])
        if v is None or v < 0:
            errs["cooldown_periods"] = "cooldown_periods must be a whole number, zero or more."

    if params.get("cooldown_reset_on_opposite") is not None:
        if not isinstance(params["cooldown_reset_on_opposite"], bool):
            errs["cooldown_reset_on_opposite"] = "cooldown_reset_on_opposite must be true or false."

    if params.get("min_sell_margin_pct") is not None:
        v = _number(params["min_sell_margin_pct"])
        if v is None or v < 0:
            errs["min_sell_margin_pct"] = "min_sell_margin_pct cannot be negative."

    rsi_period = params.get("rsi_period")
    if rsi_period is not None:
        v = _integer(rsi_period)
        if v is None or v < 2:
            errs["rsi_period"] = "rsi_period must be a whole number, at least 2."
        # Thresholds are required once RSI is on, as in the form. A period of
        # 0 or 1 is already an error above, so only a positive one needs them.
        if v is not None and v > 0:
            for key in ("rsi_max_for_buy", "rsi_min_for_sell"):
                t = _number(params.get(key))
                if t is None or t < 0 or t > 100:
                    errs[key] = f"{key} must be between 0 and 100."

    return errs
