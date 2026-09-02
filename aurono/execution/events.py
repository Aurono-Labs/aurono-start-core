#aurono/execution/events.py

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

# ============================================================
# Base execution event
# ============================================================
@dataclass(frozen=True)
class ExecutionEvent:
    """
    Base type for execution-domain events.

    Adapters and fill ingestors produce these typed dataclasses.
    Use aurono.execution.bridge.to_runtime_args() to convert
    to the dict format required by emit_event_runtime().
    """

    # --- domain identity ---
    occurred_at: datetime
    trade_id: str
    strategy_id: int
    symbol: str
    side: str


# ============================================================
# Reservation events (capital isolation)
# ============================================================

@dataclass(frozen=True)
class FundsReserved(ExecutionEvent):
    """
    Emitted before submitting a BUY order.
    Prevents overspending allocated capital.
    """
    reserved_eur: Decimal


@dataclass(frozen=True)
class InventoryReserved(ExecutionEvent):
    """
    Emitted before submitting a SELL order.
    Prevents overselling inventory.
    """
    reserved_units: Decimal


# ============================================================
# Submission lifecycle events
# ============================================================

@dataclass(frozen=True)
class OrderSubmitted(ExecutionEvent):
    """
    Order was sent to the exchange.
    """
    client_order_id: str
    order_type: str


@dataclass(frozen=True)
class OrderAccepted(ExecutionEvent):
    """
    Exchange accepted the order and assigned an order id.
    """
    exchange_order_id: str


@dataclass(frozen=True)
class OrderRejected(ExecutionEvent):
    reason: str



# ============================================================
# Fill lifecycle events
# ============================================================

@dataclass(frozen=True)
class OrderPartiallyFilled(ExecutionEvent):
    """
    Partial execution of the order.
    May occur multiple times.
    """
    filled_units: Decimal
    avg_price: Decimal
    fee_eur: Decimal


@dataclass(frozen=True)
class OrderFullyFilled(ExecutionEvent):
    """
    Order completely filled.
    Terminal success state.
    """
    filled_units: Decimal
    avg_price: Decimal
    fee_eur: Decimal


# ============================================================
# Terminal failure / cancellation events
# ============================================================

@dataclass(frozen=True)
class OrderCancelled(ExecutionEvent):
    reason: str


@dataclass(frozen=True)
class OrderFailed(ExecutionEvent):
    reason: str

# ============================================================
# Reservation release events (MANDATORY)
# ============================================================

@dataclass(frozen=True)
class FundsReleased(ExecutionEvent):
    """
    Release previously reserved EUR.
    consumed_eur: EUR consumed from reserved on fill (doesn't go back to free).
    """
    released_eur: Decimal
    consumed_eur: Decimal = Decimal("0")


@dataclass(frozen=True)
class InventoryReleased(ExecutionEvent):
    """
    Release previously reserved asset units.
    """
    released_units: Decimal

# ============================================================
# Inventory settlement events
# ============================================================

@dataclass(frozen=True)
class InventoryIncreased(ExecutionEvent):
    """
    Units received from a BUY fill.
    """
    quantity: Decimal


@dataclass(frozen=True)
class InventoryDecreased(ExecutionEvent):
    """
    Units consumed from reserved on a SELL fill.
    """
    quantity: Decimal


# aurono/execution/events.py

@dataclass(frozen=True)
class InventoryBootstrapped(ExecutionEvent):
    asset_units: Decimal
    acb_price: Decimal

