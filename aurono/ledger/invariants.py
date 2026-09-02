# aurono/ledger/invariants.py

from decimal import Decimal
from typing import List, Tuple

from aurono.ledger.state import LedgerState


def check_ledger_invariants(state: LedgerState) -> List[Tuple[str, str]]:
    """
    Returns a list of (code, message) invariant violations.
    Empty list means OK.

    Keep these rules stable: they become CI guardrails.
    """
    violations: List[Tuple[str, str]] = []

    if state.free_eur < 0:
        violations.append(("NEGATIVE_FREE_EUR", f"free_eur={state.free_eur}"))

    if state.reserved_eur < 0:
        violations.append(("NEGATIVE_RESERVED_EUR", f"reserved_eur={state.reserved_eur}"))

    if state.position_units < 0:
        violations.append(("NEGATIVE_POSITION_UNITS", f"position_units={state.position_units}"))

    if state.reserved_units < 0:
        violations.append(("NEGATIVE_RESERVED_UNITS", f"reserved_units={state.reserved_units}"))

    if state.position_cost_eur < 0:
        violations.append(("NEGATIVE_POSITION_COST", f"position_cost_eur={state.position_cost_eur}"))

    # ACB definition must match units/cost
    if state.position_units == 0:
        if state.acb_price is not None:
            violations.append(("ACB_DEFINED_WITH_ZERO_UNITS", f"acb_price={state.acb_price}"))
    else:
        if state.acb_price is None:
            violations.append(("ACB_MISSING_WITH_UNITS", "acb_price is None but position_units>0"))

    # reservation books must not contain negative leftovers
    for tid, eur in state.buy_reservations.items():
        if eur < 0:
            violations.append(("NEGATIVE_BUY_RESERVATION", f"{tid}={eur}"))

    for tid, u in state.sell_reservations.items():
        if u < 0:
            violations.append(("NEGATIVE_SELL_RESERVATION", f"{tid}={u}"))

    return violations
