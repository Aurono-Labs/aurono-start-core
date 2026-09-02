# aurono/execution/sizing/engine.py
from decimal import Decimal
from .math import floor_to_tick

def size_buy(
    *,
    quote_eur_requested: Decimal,
    free_eur: Decimal,
    price_used: Decimal,
    tick_size: Decimal,
    min_order_size: Decimal,
    fee_rate: Decimal,
) -> dict:
    if quote_eur_requested <= 0:
        raise ValueError("quote_eur_requested must be > 0")
    if free_eur <= 0:
        raise ValueError("free_eur must be > 0")
    if price_used <= 0:
        raise ValueError("price_used must be > 0")
    if fee_rate < 0:
        raise ValueError("fee_rate must be >= 0")

    # 1) Fee-inclusive EUR budget (hard guard)
    #    Symmetric with size_sell: quote_eur_requested represents the TOTAL
    #    capital outflow (asset cost + fee), not the asset cost alone. We
    #    must ensure: quote_used * (1 + fee_rate) <= min(free_eur, quote_eur_requested).
    fee_factor = Decimal("1") + fee_rate
    max_quote_within_request = quote_eur_requested / fee_factor
    max_quote_affordable = free_eur / fee_factor

    # 2) Cap requested asset-spend by both the user's requested ceiling and
    #    what's actually affordable in the account.
    quote_cap = min(max_quote_within_request, max_quote_affordable)

    # 3) Convert to raw units, then floor to tick
    raw_units = quote_cap / price_used
    base_units = floor_to_tick(raw_units, tick_size)

    # 4) Enforce min order size
    if base_units < min_order_size:
        return {}  # reject at execution sizing layer (cannot place order)

    # 5) Recompute definitive EUR notional after rounding
    quote_eur_used = base_units * price_used

    # 6) Compute max fee in EUR for guard + audit
    max_fee_eur = quote_eur_used * fee_rate

    # 7) Final hard invariants (should always hold after step 1 + flooring)
    if quote_eur_used + max_fee_eur > free_eur:
        return {}  # reject (paranoia guard against free_eur overdraw)
    if quote_eur_used + max_fee_eur > quote_eur_requested:
        return {}  # reject (paranoia guard against budget overshoot)

    rounding_delta_eur = quote_eur_requested - quote_eur_used

    return {
        "base_units": base_units,
        "quote_eur_used": quote_eur_used,
        "max_fee_eur": max_fee_eur,
        "rounding_delta_eur": rounding_delta_eur,
    }

def size_sell(
    *,
    quote_eur_requested: Decimal,
    free_units: Decimal,
    price_used: Decimal,
    tick_size: Decimal,
    min_order_size: Decimal,
    fee_rate: Decimal,
) -> dict:
    if quote_eur_requested <= 0:
        raise ValueError("quote_eur_requested must be > 0")
    if free_units <= 0:
        return {}  # reject: no inventory
    if price_used <= 0:
        raise ValueError("price_used must be > 0")
    if fee_rate < 0:
        raise ValueError("fee_rate must be >= 0")

    # 1) Convert requested EUR notional to raw units.
    #    To guarantee proceeds >= quote_eur_requested after fees:
    #    units * price * (1 - fee_rate) >= quote_eur_requested
    #    units >= quote_eur_requested / (price * (1 - fee_rate))
    effective_price = price_used * (Decimal("1") - fee_rate)
    requested_units = quote_eur_requested / effective_price

    # 2) Cap by available inventory
    units_cap = min(requested_units, free_units)

    # 3) Floor to tick
    base_units = floor_to_tick(units_cap, tick_size)

    # 4) Enforce min order size
    if base_units < min_order_size:
        return {}  # reject: too small to sell

    # 5) Definitive EUR notional after rounding
    quote_eur_used = base_units * price_used

    # 6) Max fee (EUR) for audit
    max_fee_eur = quote_eur_used * fee_rate

    # 7) Rounding delta (requested - used)
    rounding_delta_eur = quote_eur_requested - quote_eur_used

    return {
        "base_units": base_units,
        "quote_eur_used": quote_eur_used,
        "max_fee_eur": max_fee_eur,
        "rounding_delta_eur": rounding_delta_eur,
    }

