from dataclasses import replace
from decimal import Decimal

from aurono.execution.events import (
    ExecutionEvent,
    FundsReserved,
    InventoryReserved,
    OrderPartiallyFilled,
    OrderFullyFilled,
    FundsReleased,
    InventoryReleased,
    InventoryBootstrapped,
)
from aurono.ledger.state import LedgerState


def apply_ledger(state: LedgerState, event: ExecutionEvent) -> LedgerState:
    # ignore other strategies/symbols defensively
    if event.strategy_id != state.strategy_id:
        return state
    if event.symbol != state.symbol:
        return state

    # -----------------------------
    # SET SOME INVENTORY TO TEST SHADOW SELL TESTING
    # -----------------------------
    if isinstance(event, InventoryBootstrapped):
        if event.initial_units <= 0:
            return state

        position_cost = event.initial_units * event.acb_price

        return replace(
            state,
            position_units=event.initial_units,
            position_cost_eur=position_cost,
            acb_price=event.acb_price,
        )

    # -----------------------------
    # RESERVATIONS
    # -----------------------------
    if isinstance(event, FundsReserved):
        tid = event.trade_id
        amt = event.reserved_eur

        buy_res = dict(state.buy_reservations)
        buy_res[tid] = buy_res.get(tid, Decimal("0")) + amt

        return replace(
            state,
            free_eur=state.free_eur - amt,
            reserved_eur=state.reserved_eur + amt,
            buy_reservations=buy_res,
        )

    if isinstance(event, InventoryReserved):
        tid = event.trade_id
        units = event.reserved_units

        sell_res = dict(state.sell_reservations)
        sell_res[tid] = sell_res.get(tid, Decimal("0")) + units

        return replace(
            state,
            reserved_units=state.reserved_units + units,
            sell_reservations=sell_res,
        )

    # -----------------------------
    # FILLS (delta-based)
    # -----------------------------
    if isinstance(event, OrderPartiallyFilled):
        return _apply_fill_delta(state, event, terminal=False)

    if isinstance(event, OrderFullyFilled):
        return _apply_fill_delta(state, event, terminal=True)

    # -----------------------------
    # RELEASE EVENTS (explicit)
    # -----------------------------
    if isinstance(event, FundsReleased):
        tid = event.trade_id
        amt = event.released_eur

        buy_res = dict(state.buy_reservations)
        buy_res.pop(tid, None)

        return replace(
            state,
            free_eur=state.free_eur + amt,
            reserved_eur=state.reserved_eur - amt,
            buy_reservations=buy_res,
        )

    if isinstance(event, InventoryReleased):
        tid = event.trade_id
        units = event.released_units

        sell_res = dict(state.sell_reservations)
        sell_res.pop(tid, None)

        return replace(
            state,
            reserved_units=state.reserved_units - units,
            sell_reservations=sell_res,
        )

    return state

def _apply_fill_delta(state: LedgerState, event: ExecutionEvent, *, terminal: bool) -> LedgerState:
    tid = event.trade_id

    if event.side == "buy":
        # spend (includes fees)
        spend = (event.filled_units * event.avg_price) + event.fee_eur

        # reduce remaining reservation for this trade
        buy_res = dict(state.buy_reservations)
        remaining = buy_res.get(tid, Decimal("0"))
        new_remaining = remaining - spend
        buy_res[tid] = new_remaining

        # update totals
        new_state = replace(
            state,
            reserved_eur=state.reserved_eur - spend,
            position_units=state.position_units + event.filled_units,
            position_cost_eur=state.position_cost_eur + spend,
            buy_reservations=buy_res,
        )

        # if terminal, refund whatever reservation remains (if positive)
        if terminal:
            buy_res2 = dict(new_state.buy_reservations)
            refund = buy_res2.pop(tid, Decimal("0"))
            if refund > 0:
                new_state = replace(
                    new_state,
                    free_eur=new_state.free_eur + refund,
                    reserved_eur=new_state.reserved_eur - refund,
                    buy_reservations=buy_res2,
                )
            else:
                # remove key even if 0/negative
                buy_res2.pop(tid, None)
                new_state = replace(new_state, buy_reservations=buy_res2)

        return new_state

    if event.side == "sell":
        # proceeds (fees reduce EUR received)
        proceeds = (event.filled_units * event.avg_price) - event.fee_eur

        # ACB removal uses current ACB before reducing units
        acb = state.acb_price or Decimal("0")
        cost_removed = acb * event.filled_units

        sell_res = dict(state.sell_reservations)
        remaining_u = sell_res.get(tid, Decimal("0"))
        sell_res[tid] = remaining_u - event.filled_units

        new_state = replace(
            state,
            free_eur=state.free_eur + proceeds,
            reserved_units=state.reserved_units - event.filled_units,
            position_units=state.position_units - event.filled_units,
            position_cost_eur=state.position_cost_eur - cost_removed,
            sell_reservations=sell_res,
        )

        # if terminal, release any remaining reserved units for this trade
        if terminal:
            sell_res2 = dict(new_state.sell_reservations)
            leftover = sell_res2.pop(tid, Decimal("0"))
            if leftover > 0:
                new_state = replace(
                    new_state,
                    reserved_units=new_state.reserved_units - leftover,
                    sell_reservations=sell_res2,
                )
            else:
                sell_res2.pop(tid, None)
                new_state = replace(new_state, sell_reservations=sell_res2)

        return new_state

    return state
