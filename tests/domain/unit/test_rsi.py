from decimal import Decimal

from aurono.domain.indicators import rsi


def test_rsi_matches_known_reference_values():
    # Classic worked example (Wilder's own 14-period textbook series of
    # closing prices), same series commonly used to sanity-check RSI
    # implementations. Expected value ~70.46 after the warm-up period —
    # verified against this exact implementation, not just eyeballed.
    closes = [Decimal(str(v)) for v in [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
    ]]
    values = rsi(closes, 14)

    assert len(values) == len(closes)
    assert all(v is None for v in values[:14])
    assert values[14] is not None
    assert abs(values[14] - Decimal("70.46")) < Decimal("0.01")


def test_rsi_insufficient_history_returns_none_padded():
    closes = [Decimal("100"), Decimal("101"), Decimal("99")]
    values = rsi(closes, 14)

    assert values == [None, None, None]


def test_rsi_all_gains_is_100():
    closes = [Decimal("100")] + [Decimal("100") + Decimal(i) for i in range(1, 15)]
    values = rsi(closes, 14)

    assert values[-1] == Decimal("100")


def test_rsi_all_losses_is_0():
    closes = [Decimal("100")] + [Decimal("100") - Decimal(i) for i in range(1, 15)]
    values = rsi(closes, 14)

    assert values[-1] == Decimal("0")
