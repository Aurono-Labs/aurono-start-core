# tests/domain/test_capital_schemas.py

"""Phase 13.2 — CapitalCredited and CapitalDebited schema tests."""

from decimal import Decimal

import pytest

from aurono.events.emit import emit_event
from aurono.events.schemas.registry import ALL_EVENT_SCHEMAS


def test_capital_credited_registered():
    assert "CapitalCredited" in ALL_EVENT_SCHEMAS


def test_capital_debited_registered():
    assert "CapitalDebited" in ALL_EVENT_SCHEMAS


def _emit_capital(event_type, amount="100"):
    return emit_event(
        event_type=event_type,
        actor_type="user",
        actor_id="operator",
        aurono_device_id="test",
        payload={"amount": Decimal(amount), "currency": "EUR"},
        envelope={"strategy_id": "s1"},
    )


def test_capital_credited_emit():
    event = _emit_capital("CapitalCredited")
    assert event["event_type"] == "CapitalCredited"
    assert event["strategy_id"] == "s1"
    assert '"amount":"100"' in event["payload_json"]


def test_capital_debited_emit():
    event = _emit_capital("CapitalDebited")
    assert event["event_type"] == "CapitalDebited"


def test_capital_credited_forbids_trade_id():
    with pytest.raises(ValueError, match="forbidden"):
        emit_event(
            event_type="CapitalCredited",
            actor_type="user",
            actor_id="operator",
            aurono_device_id="test",
            payload={"amount": Decimal("100"), "currency": "EUR"},
            envelope={"strategy_id": "s1", "trade_id": "t1"},
        )


def test_capital_credited_requires_eur():
    with pytest.raises(ValueError, match="EUR"):
        emit_event(
            event_type="CapitalCredited",
            actor_type="user",
            actor_id="operator",
            aurono_device_id="test",
            payload={"amount": Decimal("100"), "currency": "USD"},
            envelope={"strategy_id": "s1"},
        )


def test_capital_credited_requires_strategy_id():
    with pytest.raises(ValueError, match="missing required"):
        emit_event(
            event_type="CapitalCredited",
            actor_type="user",
            actor_id="operator",
            aurono_device_id="test",
            payload={"amount": Decimal("100"), "currency": "EUR"},
            envelope={},
        )
