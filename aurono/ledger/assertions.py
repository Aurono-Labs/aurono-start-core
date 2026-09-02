# aurono/ledger/assertions.py

import sqlite3
from pathlib import Path
from decimal import Decimal
from typing import Dict, Tuple

from aurono.db.connect import connect as db_connect


# ============================================================
# Capital Ledger ↔ Projection Consistency Assertion
# ============================================================

def assert_capital_consistency(
    *,
    ledger_db_path: Path,
    projection_db_path: Path,
    tolerance: Decimal = Decimal("0.01"),
) -> None:
    """
    Assert that capital ledger sums reconcile exactly with
    capital_balance_projection.

    Invariant:
        SUM(capital_ledger.amount)
        ==
        available + reserved

    Raises AssertionError on first violation.
    """

    ledger_totals = _load_capital_ledger_totals(ledger_db_path)
    projection_totals = _load_capital_projection_totals(projection_db_path)

    for key, ledger_sum in ledger_totals.items():
        proj = projection_totals.get(key)

        if proj is None:
            raise AssertionError(
                f"Missing capital projection for strategy={key[0]} currency={key[1]}"
            )

        projected_sum = proj["available"] + proj["reserved"]
        delta = ledger_sum - projected_sum

        if abs(delta) > tolerance:
            raise AssertionError(
                f"Capital mismatch for strategy={key[0]} currency={key[1]} | "
                f"ledger={ledger_sum:.2f} "
                f"projection={projected_sum:.2f} "
                f"delta={delta:.2f}"
            )


# ============================================================
# Internal helpers
# ============================================================

def _load_capital_ledger_totals(
    db_path: Path,
) -> Dict[Tuple[str, str], Decimal]:
    """
    Return {(strategy_id, currency): SUM(amount)}
    """
    conn = db_connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT strategy_id, currency, SUM(amount)
            FROM capital_ledger
            WHERE kind != 'cost_basis'
            GROUP BY strategy_id, currency
            """
        )

        totals = {}
        for strategy_id, currency, amount in cur.fetchall():
            totals[(strategy_id, currency)] = Decimal(str(amount or 0))

        return totals
    finally:
        conn.close()


def _load_capital_projection_totals(
    db_path: Path,
) -> Dict[Tuple[str, str], Dict[str, Decimal]]:
    """
    Return {(strategy_id, currency): {available, reserved}}
    """
    conn = db_connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT strategy_id, currency, available, reserved
            FROM capital_balance_projection
            """
        )

        totals = {}
        for strategy_id, currency, available, reserved in cur.fetchall():
            totals[(strategy_id, currency)] = {
                "available": Decimal(str(available)),
                "reserved": Decimal(str(reserved)),
            }

        return totals
    finally:
        conn.close()
