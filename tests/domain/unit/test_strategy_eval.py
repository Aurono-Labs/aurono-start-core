from decimal import Decimal
from datetime import datetime

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


def test_buy_triggered_when_drop_and_capital_ok():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),  # −15%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED
    assert decision.order_intent is not None

def test_buy_rejected_when_capital_insufficient():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("50"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.CAPITAL_INSUFFICIENT

def test_sell_blocked_below_acb():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("110"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("0"),
            asset_units=Decimal("2"),
            acb_price=Decimal("120"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.BELOW_ACB

def test_hold_no_signal():
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("10"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("102"),
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("100"),
            asset_units=Decimal("1"),
            acb_price=Decimal("90"),
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.NO_SIGNAL


def test_buy_blocked_when_rsi_above_threshold():
    # 13 gains of +5, then a final -16.5 drop (-10% from 165) — the price
    # trigger fires, but 14 periods of mostly gains keeps RSI high (~79.75),
    # well above a 30 oversold threshold.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("-16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_max_for_buy=Decimal("30"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.RSI_NOT_OVERSOLD


def test_buy_allowed_when_rsi_at_or_below_threshold():
    # 13 losses of -5, then a final -13.5 drop (-10% from 135) — price
    # trigger fires, and 14 periods of pure losses drive RSI to 0.
    closes = _closes_from_changes(Decimal("200"), [Decimal("-5")] * 13 + [Decimal("-13.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_max_for_buy=Decimal("30"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED


def test_rsi_gate_skipped_when_rsi_period_none():
    # Same drop as the "blocked" case above, but rsi_period is None — the
    # gate must be skipped entirely, exactly like before this feature existed.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("-16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED


def test_rsi_gate_runs_before_cooldown_check():
    # RSI-not-oversold AND cooldown-active at the same time — must resolve
    # to RSI_NOT_OVERSOLD, not COOLDOWN_ACTIVE, since RSI is checked first.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("-16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_max_for_buy=Decimal("30"),
            cooldown_periods=6,
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1, 12, 0)),
        constraint=ConstraintState(last_buy_at=datetime(2026, 1, 1, 11, 0)),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.RSI_NOT_OVERSOLD


def test_buy_triggered_metrics_include_rsi_value():
    # Same fixture as test_buy_allowed_when_rsi_at_or_below_threshold — RSI
    # passes the gate and the trade fires. The rsi value used to gate it
    # must survive into metrics, not just get discarded once it passed.
    closes = _closes_from_changes(Decimal("200"), [Decimal("-5")] * 13 + [Decimal("-13.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_max_for_buy=Decimal("30"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED
    assert decision.metrics["rsi"] == Decimal("0")


def test_buy_triggered_metrics_omit_rsi_when_rsi_period_none():
    # Same fixture as test_rsi_gate_skipped_when_rsi_period_none — no RSI
    # configured, so metrics must not carry a phantom "rsi" key.
    closes = _closes_from_changes(Decimal("100"), [Decimal("5")] * 13 + [Decimal("-16.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert "rsi" not in decision.metrics


def test_buy_blocked_by_cooldown_after_rsi_passes_still_records_rsi():
    # RSI passes the gate (same fixture as the "allowed" case), but a
    # still-active cooldown blocks the trade. metrics.rsi must still be
    # recorded — RSI genuinely was evaluated and passed on this cycle, the
    # cooldown check just runs after it and rejects for its own reason.
    closes = _closes_from_changes(Decimal("200"), [Decimal("-5")] * 13 + [Decimal("-13.5")])
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1d",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
            rsi_period=14,
            rsi_max_for_buy=Decimal("30"),
            cooldown_periods=6,
        ),
        market=MarketWindow(
            prev_close=closes[-2],
            last_close=closes[-1],
            closes=closes,
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 7)),
        constraint=ConstraintState(last_buy_at=datetime(2026, 1, 6)),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.COOLDOWN_ACTIVE
    assert decision.metrics["rsi"] == Decimal("0")


def test_buy_disabled_when_buy_eur_zero():
    # Trigger fires (drop crosses threshold) but buy_eur=0 — a documented
    # valid one-sided config. Must resolve to a clean HOLD, never reach
    # sizing (which would raise on quote_eur_requested<=0).
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("0"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),  # -15%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "HOLD"
    assert decision.reason_code == reasons.BUY_DISABLED
    assert decision.order_intent is None


def test_buy_not_disabled_when_buy_eur_positive():
    # Regression pin: a positive buy_eur must behave exactly as before —
    # the disabled check must not accidentally fire for the normal case.
    decision = evaluate_strategy(
        spec=StrategySpec(
            symbol="BTCEUR",
            timeframe="1w",
            buy_drop_pct=Decimal("10"),
            sell_rise_pct=Decimal("15"),
            buy_eur=Decimal("100"),
            sell_eur=Decimal("100"),
        ),
        market=MarketWindow(
            prev_close=Decimal("100"),
            last_close=Decimal("85"),  # -15%
        ),
        portfolio=PortfolioState(
            free_eur=Decimal("200"),
            asset_units=Decimal("0"),
            acb_price=None,
        ),
        ctx=EvalContext(asof=datetime(2026, 1, 1)),
        constraint=ConstraintState(),
    )

    assert decision.action == "BUY"
    assert decision.reason_code == reasons.BUY_TRIGGERED

