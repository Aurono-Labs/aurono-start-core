from datetime import datetime
from decimal import Decimal

from aurono.domain.strategy_eval import evaluate_strategy
from aurono.domain.types import (
    StrategySpec,
    MarketWindow,
    PortfolioState,
    EvalContext,
    ConstraintState,
)
from aurono.domain import reasons


def _closes_from_changes(start, changes):
    closes = [start]
    for c in changes:
        closes.append(closes[-1] + c)
    return closes


def test_sell_triggered_when_price_above_acb():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),  # +20%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.order_intent is not None
    assert decision.order_intent.side == "sell"

def test_sell_triggered_when_rise_and_inventory_ok():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("1.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.order_intent is not None
    assert decision.order_intent.side == "sell"
    assert decision.order_intent.quote_eur == Decimal("100")
    assert decision.order_intent.base_units is None


def test_sell_blocked_min_position():
    """Sell rejected when remaining position would drop below min_position_units."""
    # Price = 120, sell_eur = 100 → sell_units = 100/120 ≈ 0.833
    # Position = 1.0, remaining = 1.0 - 0.833 = 0.167 < 0.5 → blocked
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0.5"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),  # +20% → sell signal
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("1.0"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.MIN_POSITION_BREACH


def test_sell_allowed_above_min_position():
    """Sell passes when remaining position stays above min_position_units."""
    # Price = 120, sell_eur = 100 → sell_units = 100/120 ≈ 0.833
    # Position = 2.0, remaining = 2.0 - 0.833 = 1.167 > 0.5 → allowed
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0.5"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2.0"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED


def test_sell_blocked_min_position_when_sell_amount_exceeds_holdings():
    """A sell_eur sized well above the position doesn't crash or misbehave —
    still correctly held back by min_position_units, matching
    triggerAnalysis.ts's Math.min(sellEur/close, assetUnits) clamp.
    F11: `remaining` must never go negative."""
    # Price = 110 (10% rise triggers the sell signal), sell_eur = 1000 →
    # naive sell_units = 1000/110 ≈ 9.09, but position is only 1.0 → clamped
    # sell_units = 1.0, remaining = 0 < 0.5 → blocked
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("1000"),
            min_position_units=Decimal("0.5"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("110"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("1.0"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.MIN_POSITION_BREACH


def test_min_position_zero_means_no_constraint():
    """min_position_units=0 should not block any sell."""
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_position_units=Decimal("0"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("0.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"


def test_min_position_none_means_no_constraint():
    """min_position_units=None (unset) should not block any sell."""
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("0.5"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"


def test_sell_blocked_when_rsi_below_threshold():
    # 13 losses of -5, then a final +13.5 rise (+10% from 135) — the price
    # trigger fires, but 14 periods of mostly losses keeps RSI low (~17.2),
    # well below a 70 overbought threshold.
    closes = _closes_from_changes(Decimal("200"), [Decimal("-5")] * 13 + [Decimal("13.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_min_for_sell=Decimal("70"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.RSI_NOT_OVERBOUGHT


def test_sell_allowed_when_rsi_at_or_above_threshold():
    # 13 gains of +5, then a final +16.5 rise (+10% from 165) — price
    # trigger fires, and 14 periods of pure gains drive RSI to 100.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_min_for_sell=Decimal("70"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED


def test_sell_triggered_metrics_include_rsi_value():
    # Same fixture as test_sell_allowed_when_rsi_at_or_above_threshold — RSI
    # passes the gate and the trade fires. The rsi value used to gate it
    # must survive into metrics, not just get discarded once it passed.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_min_for_sell=Decimal("70"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED
    assert decision.metrics["rsi"] == Decimal("100")


def test_sell_triggered_metrics_omit_rsi_when_rsi_period_none():
    # Same rise as the "allowed" case above, but no RSI configured — metrics
    # must not carry a phantom "rsi" key.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert "rsi" not in decision.metrics


def test_sell_disabled_when_sell_eur_zero():
    # Trigger fires (rise crosses threshold) but sell_eur=0 — a documented
    # valid one-sided config. Must resolve to a clean HOLD before ever
    # reaching inventory/ACB checks or sizing.
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("0"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("120"),  # +20%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.SELL_DISABLED
    assert decision.order_intent is None


def test_sell_blocked_by_margin_above_raw_acb():
    # Price is above raw ACB (100) but below the default fee-margin floor
    # (100 * 1.005 = 100.5 at the default min_sell_margin_pct=0.5). Pre-fix,
    # this would have wrongly triggered since 100.4 > 100 (raw comparison).
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("0.1"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("100.4"),  # +0.4%, above ACB but below floor
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.BELOW_ACB


def test_sell_allowed_once_margin_cleared():
    # Same setup, but price clears the fee-margin floor (100.5).
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("0.1"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("101"),  # +1%, clears the 0.5% margin floor
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED


def test_sell_margin_zero_matches_old_raw_acb_behavior():
    # min_sell_margin_pct=0 reproduces the exact pre-fix boundary: a sell
    # exactly at (or fractionally above) raw ACB fires with no margin
    # required — the widened check is a strict superset, not a separate
    # condition, and this pins that the old callers/behavior still work.
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1h",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("0.1"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            min_sell_margin_pct=Decimal("0"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("100.1"),  # +0.1%, above raw ACB
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("100"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "SELL"
    assert decision.reason_code == reasons.SELL_TRIGGERED
