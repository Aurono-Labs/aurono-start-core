from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


@dataclass(frozen=True)
class LedgerState:
    """
    Deterministic per-strategy ledger state.

    Assumptions:
    - One strategy trades one symbol.
    - Fill events are treated as DELTAS (incremental fills).
      (If you emit cumulative fills, adjust ingestion to emit deltas.)
    """
    strategy_id: int
    symbol: str

    # capital & position
    free_eur: Decimal
    position_units: Decimal = Decimal("0")
    position_cost_eur: Decimal = Decimal("0")  # cost basis (incl fees on buys)

    # reservations (totals)
    reserved_eur: Decimal = Decimal("0")
    reserved_units: Decimal = Decimal("0")

    # per-trade reservation books
    buy_reservations: Dict[str, Decimal] = None   # trade_id -> remaining reserved EUR
    sell_reservations: Dict[str, Decimal] = None  # trade_id -> remaining reserved units

    def __post_init__(self):
        # dataclasses + frozen: allow initializing dicts safely
        object.__setattr__(self, "buy_reservations", self.buy_reservations or {})
        object.__setattr__(self, "sell_reservations", self.sell_reservations or {})

    @property
    def acb_price(self) -> Optional[Decimal]:
        if self.position_units <= 0:
            return None
        return self.position_cost_eur / self.position_units


def initial_ledger_state(*, strategy_id: int, symbol: str, free_eur: Decimal) -> LedgerState:
    return LedgerState(strategy_id=strategy_id, symbol=symbol, free_eur=free_eur)
