from decimal import Decimal


def eur_to_units(*, eur: Decimal, price: Decimal) -> Decimal:
    """
    Deterministic EUR → base units conversion.
    Assumes validated positive inputs.
    """
    return eur / price
