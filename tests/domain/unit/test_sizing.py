from decimal import Decimal
from aurono.domain.sizing import eur_to_units


def test_eur_to_units_basic():
    units = eur_to_units(
        eur=Decimal("100"),
        price=Decimal("50"),
    )
    assert units == Decimal("2")


def test_eur_to_units_exact_reversibility():
    price = Decimal("37.5")
    eur = Decimal("150")

    units = eur_to_units(eur=eur, price=price)

    assert units * price == eur


def test_eur_to_units_requires_positive_price():
    try:
        eur_to_units(eur=Decimal("100"), price=Decimal("0"))
        assert False, "Expected division by zero"
    except Exception:
        pass
