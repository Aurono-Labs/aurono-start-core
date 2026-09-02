# aurono/events/emit.py

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

from aurono.events.schemas.base import ENVELOPE_FIELDS, EventSchema
from aurono.events.schemas.registry import ALL_EVENT_SCHEMAS


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """
    Convert values to JSON-safe representations.
    """
    if isinstance(value, Decimal):
        return str(value)
    return value


# -------------------------------------------------
# Validation
# -------------------------------------------------

def _validate_actor(schema: EventSchema, actor_type: str):
    if actor_type not in schema.actor_types:
        raise ValueError(
            f"{schema.event_type}: invalid actor_type '{actor_type}', "
            f"allowed={schema.actor_types}"
        )


def _validate_envelope(schema: EventSchema, envelope: Dict[str, Any]):
    rules = schema.envelope

    # forbidden fields
    forbidden = envelope.keys() & rules.forbidden
    if forbidden:
        raise ValueError(
            f"{schema.event_type}: forbidden envelope fields {forbidden}"
        )

    # required fields
    missing = rules.required - envelope.keys()
    if missing:
        raise ValueError(
            f"{schema.event_type}: missing required envelope fields {missing}"
        )

    # unknown fields
    allowed = rules.required | rules.optional
    unknown = envelope.keys() - allowed
    if unknown:
        raise ValueError(
            f"{schema.event_type}: unknown envelope fields {unknown}"
        )

    # schema consistency guard
    referenced = rules.required | rules.optional | rules.forbidden
    illegal = referenced - ENVELOPE_FIELDS
    if illegal:
        raise RuntimeError(
            f"{schema.event_type}: schema references illegal envelope fields {illegal}"
        )


def _validate_payload(schema: EventSchema, payload: Dict[str, Any]):
    # required payload fields
    missing = schema.payload.keys() - payload.keys()
    if missing:
        raise ValueError(
            f"{schema.event_type}: missing payload fields {missing}"
        )

    # type checks (required)
    for field, field_type in schema.payload.items():
        if not isinstance(payload[field], field_type):
            raise TypeError(
                f"{schema.event_type}: payload.{field} must be {field_type}, "
                f"got {type(payload[field])}"
            )
    # 👇 ADD THIS
    if schema.payload_invariant:
        schema.payload_invariant(payload)

    # type checks (optional)
    for field, field_type in schema.payload_optional.items():
        if field in payload and not isinstance(payload[field], field_type):
            raise TypeError(
                f"{schema.event_type}: payload.{field} must be {field_type}, "
                f"got {type(payload[field])}"
            )

    # unknown payload fields
    allowed = set(schema.payload) | set(schema.payload_optional)
    unknown = payload.keys() - allowed
    if unknown:
        raise ValueError(
            f"{schema.event_type}: unknown payload fields {unknown}"
        )


# -------------------------------------------------
# Public API
# -------------------------------------------------

def emit_event(
    *,
    event_type: str,
    actor_type: str,
    actor_id: str,
    aurono_device_id: str,
    payload: Dict[str, Any],
    envelope: Dict[str, Any],
    correlation_id: str | None = None,
) -> Dict[str, Any]:
    """
    Validate and emit an Aurono event.

    Returns the fully materialized event dict (as stored).
    """

    # -------------------------------------------------
    # Schema lookup
    # -------------------------------------------------

    schema = ALL_EVENT_SCHEMAS.get(event_type)
    if not schema:
        raise ValueError(f"Unknown event_type '{event_type}'")

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------

    _validate_actor(schema, actor_type)
    _validate_envelope(schema, envelope)
    _validate_payload(schema, payload)

    # -------------------------------------------------
    # Build event
    # -------------------------------------------------

    event_id = str(uuid.uuid4())

    event = {
        "event_id": event_id,
        "event_type": event_type,
        "event_domain": schema.domain,
        "timestamp_utc": _now_utc(),
        "aurono_device_id": aurono_device_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "correlation_id": correlation_id,
        # context
        **envelope,
        # payload
        "payload_json": json.dumps(
            {k: _json_safe(v) for k, v in payload.items()},
            separators=(",", ":"),
            sort_keys=True,
        ),
    }

    # -------------------------------------------------
    # Persist (single responsibility)
    # -------------------------------------------------
    # IMPORTANT:
    # - emit_event does NOT know *how* persistence works
    # - caller must inject storage (SQLite, Postgres, etc.)
    #
    # For now we return the event.
    # A storage adapter will handle insertion.
    # -------------------------------------------------

    return event
