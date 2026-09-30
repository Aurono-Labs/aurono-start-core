# tests/domain/test_strategy_params.py

"""Engine-side strategy parameter validation (field-notes F3)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from aurono.domain.strategy_params import validate_parameters

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "strategy_param_cases.json").read_text()
)["cases"]

VALID = {
    "symbol": "BTC-EUR",
    "exchange": "bitvavo",
    "timeframe": "1d",
    "buy_drop_pct": 5,
    "sell_rise_pct": 5,
    "buy_eur": 10,
    "sell_eur": 10,
}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_cases_match_expected_validity(case):
    # The same file is run against the frontend's validateParams(), which is
    # what keeps the two from drifting apart.
    assert sorted(validate_parameters(case["params"])) == case["invalid_fields"]


def test_all_errors_reported_at_once():
    errs = validate_parameters({**VALID, "buy_drop_pct": -1, "sell_rise_pct": 0, "timeframe": "5m"})
    assert set(errs) == {"buy_drop_pct", "sell_rise_pct", "timeframe"}


def test_unknown_keys_are_allowed():
    assert validate_parameters({**VALID, "product_id": "BTC-EUR", "base_increment": "0.0001"}) == {}


@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), Decimal("NaN"), None, [], "ten"])
def test_bool_and_non_finite_numbers_rejected(bad):
    assert "buy_drop_pct" in validate_parameters({**VALID, "buy_drop_pct": bad})


def test_numeric_strings_accepted():
    # Stored versions and scripts pass numbers as strings; the engine reads
    # them through Decimal(str(...)) either way.
    assert validate_parameters({**VALID, "buy_drop_pct": "5.0", "buy_eur": "100.00"}) == {}


def test_rsi_thresholds_required_once_rsi_period_set():
    errs = validate_parameters({**VALID, "rsi_period": 14})
    assert set(errs) == {"rsi_max_for_buy", "rsi_min_for_sell"}
    # No period, no RSI gate: stray thresholds are ignored, as in the engine.
    assert validate_parameters({**VALID, "rsi_max_for_buy": 900}) == {}
