# Aurono v2 — Event Payload Schemas (v1.0)
Below is a **complete, concrete design for `aurono/events/schemas/`**, including:

* structure
* envelope rules
* payload validation
* actor semantics
* extensibility rules

This is written so you can **implement it verbatim**.

---

**Status:** Canonical
**Purpose:** Enforce correctness of events at runtime
**Location:** `aurono/events/schemas/`

---

## 1. Directory Structure

```
aurono/events/schemas/
├── base.py
├── market.py
├── strategy.py
├── decision.py
├── execution.py
├── capital.py
├── inventory.py
├── system.py
└── registry.py
```

Each file defines **only schemas**, no persistence, no logic.

---

## 2. Base Schema Definition

### `base.py`

This defines the **schema language** Aurono uses.

```python
from dataclasses import dataclass
from typing import Dict, Set, Type, Optional

@dataclass(frozen=True)
class EnvelopeRule:
    required: Set[str]
    optional: Set[str]
    forbidden: Set[str]

@dataclass(frozen=True)
class EventSchema:
    event_type: str
    domain: str

    actor_types: Set[str]                 # allowed actor_type values
    envelope: EnvelopeRule
    payload: Dict[str, Type]              # required payload fields
    payload_optional: Dict[str, Type]     # optional payload fields
```

### Envelope Field Names (Canonical)

```python
ENVELOPE_FIELDS = {
    "strategy_id",
    "strategy_version_id",
    "trade_id",
    "exchange",
    "symbol",
    "timeframe",
}
```

---

## 3. Market Event Schemas

### `market.py`

```python
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

MarketCandleClosed = EventSchema(
    event_type="MarketCandleClosed",
    domain="market",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"exchange", "symbol", "timeframe"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id"},
    ),
    payload={
        "candle_timestamp": str,
        "open": Decimal,
        "high": Decimal,
        "low": Decimal,
        "close": Decimal,
        "volume": Decimal,
    },
    payload_optional={}
)

MarketDataUnavailable = EventSchema(
    event_type="MarketDataUnavailable",
    domain="market",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"exchange", "symbol", "timeframe"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)
```

---

## 4. Strategy Event Schemas

### `strategy.py`

```python
from .base import EventSchema, EnvelopeRule

StrategyCreated = EventSchema(
    event_type="StrategyCreated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"strategy_version_id", "trade_id"},
    ),
    payload={
        "name": str,
    },
    payload_optional={}
)

StrategyVersionCreated = EventSchema(
    event_type="StrategyVersionCreated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "parameters": dict,
        "previous_version_id": (str, type(None)),
    },
    payload_optional={}
)

StrategyActivated = EventSchema(
    event_type="StrategyActivated",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={},
    payload_optional={}
)

StrategyPaused = EventSchema(
    event_type="StrategyPaused",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional={"strategy_version_id"},
        forbidden={"trade_id"},
    ),
    payload={},
    payload_optional={"reason": str}
)

StrategyArchived = EventSchema(
    event_type="StrategyArchived",
    domain="strategy",
    actor_types={"user", "system"},
    envelope=EnvelopeRule(
        required={"strategy_id"},
        optional=set(),
        forbidden={"strategy_version_id", "trade_id"},
    ),
    payload={},
    payload_optional={}
)
```

---

## 5. Decision Event Schemas

### `decision.py`

```python
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

StrategyEvaluationStarted = EventSchema(
    event_type="StrategyEvaluationStarted",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "candle_timestamp": str,
    },
    payload_optional={}
)

BuyDecisionTriggered = EventSchema(
    event_type="BuyDecisionTriggered",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
        "trigger_threshold": Decimal,
        "reference_price": Decimal,
    },
    payload_optional={}
)

SellDecisionTriggered = EventSchema(
    event_type="SellDecisionTriggered",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
        "trigger_threshold": Decimal,
        "reference_price": Decimal,
    },
    payload_optional={}
)

NoActionDecision = EventSchema(
    event_type="NoActionDecision",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id", "symbol", "timeframe"},
        optional=set(),
        forbidden={"trade_id"},
    ),
    payload={
        "observed_change_pct": Decimal,
    },
    payload_optional={}
)

DecisionRejected = EventSchema(
    event_type="DecisionRejected",
    domain="decision",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "strategy_version_id"},
        optional={"symbol", "timeframe"},
        forbidden={"trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={"details": dict}
)
```

---

## 6. Execution Event Schemas

### `execution.py`

```python
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

TradeIntentCreated = EventSchema(
    event_type="TradeIntentCreated",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "strategy_id", "strategy_version_id", "symbol"},
        optional={"exchange"},
        forbidden=set(),
    ),
    payload={
        "side": str,
        "intended_price": Decimal,
        "intended_quantity": Decimal,
    },
    payload_optional={}
)

OrderSubmitted = EventSchema(
    event_type="OrderSubmitted",
    domain="execution",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"trade_id", "exchange"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id"},
    ),
    payload={
        "order_type": str,
    },
    payload_optional={}
)

OrderAcceptedByExchange = EventSchema(
    event_type="OrderAcceptedByExchange",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "exchange"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id"},
    ),
    payload={
        "external_order_id": str,
    },
    payload_optional={}
)

OrderRejectedByExchange = EventSchema(
    event_type="OrderRejectedByExchange",
    domain="execution",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"trade_id", "exchange"},
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)
```

---

## 7. Capital & Inventory Schemas

### `capital.py`

```python
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

CapitalReserved = EventSchema(
    event_type="CapitalReserved",
    domain="capital",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required={"strategy_id", "trade_id"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "currency": str,
        "amount": Decimal,
    },
    payload_optional={}
)
```

### `inventory.py`

```python
from decimal import Decimal
from .base import EventSchema, EnvelopeRule

InventoryIncreased = EventSchema(
    event_type="InventoryIncreased",
    domain="inventory",
    actor_types={"exchange"},
    envelope=EnvelopeRule(
        required={"strategy_id", "trade_id", "symbol"},
        optional=set(),
        forbidden=set(),
    ),
    payload={
        "quantity": Decimal,
    },
    payload_optional={}
)
```

---

## 8. System & Safety Schemas

### `system.py`

```python
from .base import EventSchema, EnvelopeRule

SystemStartup = EventSchema(
    event_type="SystemStartup",
    domain="system",
    actor_types={"system"},
    envelope=EnvelopeRule(
        required=set(),
        optional=set(),
        forbidden={"strategy_id", "strategy_version_id", "trade_id", "symbol", "timeframe"},
    ),
    payload={},
    payload_optional={}
)

KillSwitchActivated = EventSchema(
    event_type="KillSwitchActivated",
    domain="system",
    actor_types={"system", "user"},
    envelope=EnvelopeRule(
        required=set(),
        optional={"strategy_id"},
        forbidden={"trade_id"},
    ),
    payload={
        "reason": str,
    },
    payload_optional={}
)
```

---

## 9. Registry (Single Source of Truth)

### `registry.py`

```python
from .market import *
from .strategy import *
from .decision import *
from .execution import *
from .capital import *
from .inventory import *
from .system import *

ALL_EVENT_SCHEMAS = {
    schema.event_type: schema
    for schema in [
        MarketCandleClosed,
        MarketDataUnavailable,
        StrategyCreated,
        StrategyVersionCreated,
        StrategyActivated,
        StrategyPaused,
        StrategyArchived,
        StrategyEvaluationStarted,
        BuyDecisionTriggered,
        SellDecisionTriggered,
        NoActionDecision,
        DecisionRejected,
        TradeIntentCreated,
        OrderSubmitted,
        OrderAcceptedByExchange,
        OrderRejectedByExchange,
        CapitalReserved,
        InventoryIncreased,
        SystemStartup,
        KillSwitchActivated,
    ]
}
```

---

## 10. What This Enables Immediately

With these schemas you can now:

* reject malformed events at runtime
* guarantee semantic correctness
* enforce Open Source boundaries
* build a safe event emitter
* build replay tooling with confidence

---
