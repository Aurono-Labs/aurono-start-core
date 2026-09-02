from decimal import Decimal
from aurono.domain.types import PortfolioState


def test_portfolio_state_semantic_invariants():
    """
    PortfolioState represents STRATEGY-SCOPED capital only.
    These invariants must never change without an explicit design decision.
    """

    p = PortfolioState(
        free_eur=Decimal("100.00"),
        asset_units=Decimal("2.5"),
        acb_price=Decimal("40.00"),
    )

    # Capital invariants
    assert p.free_eur >= 0
    assert p.asset_units >= 0
    assert p.acb_price is None or p.acb_price > 0


def test_free_eur_is_not_exchange_balance():
    """
    free_eur MUST represent strategy-allocated free capital,
    not total exchange wallet balance.
    This is a semantic contract, enforced by structure.
    """

    annotations = PortfolioState.__annotations__

    # Field exists and is unambiguous
    assert "free_eur" in annotations
    assert annotations["free_eur"].__name__ == "Decimal"

    # No exchange-level fields are allowed in PortfolioState
    forbidden = {
        "exchange_balance",
        "wallet_eur",
        "total_eur",
        "available_eur",
        "reserved_eur",
    }

    for field in forbidden:
        assert field not in annotations
