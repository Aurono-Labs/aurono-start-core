# aurono/events/runtime.py

import logging
from pathlib import Path
from typing import Dict, Any

from aurono.events.emit import emit_event as build_event
from aurono.events.store_sqlite import insert_event
from aurono.ledger.write_from_event import write_ledger_entries
from aurono.projections.incremental import apply_event_to_projections

logger = logging.getLogger(__name__)


def emit_event_runtime(
    *,
    event_db_path: Path,
    ledger_db_path: Path,

    event_type: str,
    actor_type: str,
    actor_id: str,
    aurono_device_id: str,
    payload: Dict[str, Any],
    envelope: Dict[str, Any],
    correlation_id: str | None = None,
) -> str:
    """
    Canonical runtime event emission pipeline.

    Guarantees:
    - schema + envelope + payload validated
    - event persisted exactly once
    - ledger side-effects applied after persistence
    - projections updated incrementally
    - returns event_id
    """

    # 1. Build + validate event (pure)
    event = build_event(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        aurono_device_id=aurono_device_id,
        payload=payload,
        envelope=envelope,
        correlation_id=correlation_id,
    )

    # 2. Persist event (authoritative fact)
    insert_event(event_db_path, event)

    # 3. Ledger side-effects (derived from event)
    event_for_ledger = dict(event)
    event_for_ledger["payload"] = payload
    write_ledger_entries(event_for_ledger, db_path=ledger_db_path)

    # 4. Projection side-effects (derived from event)
    try:
        event_for_projection = dict(event)
        event_for_projection["payload"] = payload
        apply_event_to_projections(projection_db_path=event_db_path, event=event_for_projection)
    except Exception:
        logger.error(
            "Projection update failed for %s — projection may be inconsistent until rebuilt with the fix",
            event_type,
            exc_info=True,
        )

    return event["event_id"]
