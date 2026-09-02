def test_reason_codes_are_canonical():
    from aurono.domain import reasons

    expected = {
        "INVALID_MARKET_DATA",
        "CAPITAL_INSUFFICIENT",
        "INVENTORY_INSUFFICIENT",
        "NO_SIGNAL",
        "NO_ACB",
        "BELOW_ACB",
        "EXCHANGE_UNAVAILABLE",
        "EXCHANGE_UNREACHABLE",
        "BUY_TRIGGERED",
        "SELL_TRIGGERED",
        "MIN_POSITION_BREACH",
        "COOLDOWN_ACTIVE",
        "BELOW_MIN_NOTIONAL",
        "BELOW_MIN_ORDER_SIZE",
    }

    actual = {
        name for name in dir(reasons)
        if name.isupper()
    }

    assert actual == expected
