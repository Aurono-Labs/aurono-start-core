# aurono/events/schemas/registry

from .market import *
from .strategy import *
from .decision import (
    StrategyBuyIntentCreatedSchema,
    StrategySellIntentCreatedSchema,
    BuyDecisionTriggered,
    SellDecisionTriggered,
    NoActionDecision,
    DecisionRejected,
    StrategyEvaluationStarted,
)

from .execution import (
    OrderSized,
    FundsReserved,
    InventoryReserved,
    FundsReleased,
    InventoryReleased,
    OrderSubmitted,
    OrderAccepted,
    OrderRejected,
    OrderPartiallyFilled,
    OrderFullyFilled,
    OrderCancelled,
    OrderFailed,
    ExecutionAborted,
)


from .capital import CapitalCredited, CapitalDebited

from .inventory import InventoryIncreased, InventoryDecreased

from .system import *

ALL_EVENT_SCHEMAS = {
    schema.event_type: schema
    for schema in [
        # Market
        MarketCandleClosed,
        MarketDataUnavailable,

        # Capital lifecycle
        CapitalCredited,
        CapitalDebited,

        # Strategy lifecycle
        StrategyCreated,
        StrategyVersionCreated,
        StrategyActivated,
        StrategyPaused,
        StrategyArchived,

        # Strategy evaluation
        StrategyEvaluationStarted,

        # Legacy decision outputs (still allowed as events)
        BuyDecisionTriggered,
        SellDecisionTriggered,
        NoActionDecision,
        DecisionRejected,

        # v2 domain strategy intents
        StrategyBuyIntentCreatedSchema,
        StrategySellIntentCreatedSchema,

        # v2 domain execution lifecycle
        OrderSized,

        FundsReserved,
        InventoryReserved,

        OrderSubmitted,
        OrderAccepted,
        OrderRejected,
        OrderPartiallyFilled,
        OrderFullyFilled,
        OrderCancelled,
        OrderFailed,

        FundsReleased,
        InventoryReleased,

        ExecutionAborted,

        # Inventory settlement
        InventoryIncreased,
        InventoryDecreased,

        # System
        SystemStartup,
        KillSwitchActivated,
        InventoryBootstrapped,
        InventoryBootstrapMarked,
    ]
}
