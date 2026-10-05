# aurono/projections/event_groups.py

"""
Event-type groupings used by projection dispatchers.

Single source of truth for which event types feed which projection. Both
the incremental dispatcher (`aurono/projections/incremental.py`) and the
full-replay dispatcher (`aurono/projections/rebuild.py`) import from here
so they cannot drift.
"""


# Trade lifecycle events: those that mutate `trade_state_projection`.
# Pre-trade events that carry a trade_id for correlation (e.g. ExecutionAborted)
# are intentionally excluded — they have no entry in the trade-state machine.
TRADE_LIFECYCLE_EVENTS: frozenset[str] = frozenset({
    "TradeIntentCreated",
    "TradeIntentCancelled",
    "OrderSized",
    "OrderSubmitted",
    "OrderAcceptedByExchange",
    "OrderAccepted",
    "OrderRejectedByExchange",
    "OrderRejected",
    "OrderPartiallyFilled",
    "OrderFullyFilled",
    "OrderCancelled",
})


# Event types that mutate `inventory_balance_projection`.
#
# An explicit set rather than a `startswith("Inventory")` prefix match: the
# prefix silently enrols any future Inventory* event in the projection. That
# bit immediately: InventoryBootstrapMarked records what a bootstrapped
# position was worth and moves no units at all, but the prefix would have run
# it through the reducer and bumped `updated_at` on a row it never touches.
# InventoryExitMarked is the second of that kind, and it cost nothing to add
# precisely because the set is explicit - the prefix match would have enrolled
# it silently all over again.
INVENTORY_BALANCE_EVENTS: frozenset[str] = frozenset({
    "InventoryBootstrapped",
    "InventoryIncreased",
    "InventoryDecreased",
    "InventoryReserved",
    "InventoryReleased",
})


def base_asset(symbol: str | None) -> str:
    """Reduce a market symbol to the base asset used as the inventory key.

    `inventory_balance_projection` keys on the base asset ("FET"), never the
    pair ("FET-EUR"), matching `inventory_ledger` and its
    `CHECK(asset NOT LIKE '%-%')` constraint.

    This lives here because the two dispatchers previously disagreed: the
    incremental path normalised, the replay path did not, so a rebuild wrote
    pair-keyed rows that the live runtime then shadowed with bare-keyed ones.
    Both survived, and the bare row started from zero and absorbed sells whose
    buys it had never seen — which is how holdings went negative.
    """
    if not symbol:
        return ""
    return symbol.split("-", 1)[0]
