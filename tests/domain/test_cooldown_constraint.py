"""Tests for the cooldown period constraint in strategy evaluation."""

from datetime import datetime, timezone, timedelta
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


def _spec(cooldown_periods=2, reset_on_opposite=True, **overrides):
    defaults = dict(
        symbol="BTC-EUR",
        timeframe="4h",
        buy_drop_pct=Decimal("5"),
        sell_rise_pct=Decimal("5"),
        buy_eur=Decimal("100"),
        sell_eur=Decimal("100"),
        cooldown_periods=cooldown_periods,
        cooldown_reset_on_opposite=reset_on_opposite,
    )
    defaults.update(overrides)
    return StrategySpec(**defaults)


# Market that triggers a BUY (prev=100, close=90 → -10% drop)
BUY_MARKET = MarketWindow(prev_close=Decimal("100"), last_close=Decimal("90"))

# Market that triggers a SELL (prev=100, close=110 → +10% rise)
SELL_MARKET = MarketWindow(prev_close=Decimal("100"), last_close=Decimal("110"))

PORTFOLIO = PortfolioState(
    free_eur=Decimal("1000"),
    asset_units=Decimal("1"),
    acb_price=Decimal("50"),
)

T0 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_buy_blocked_within_cooldown():
    """BUY rejected when last buy was within cooldown window (2 × 4h = 8h)."""
    spec = _spec(cooldown_periods=2)  # 2 × 4h = 8h cooldown
    constraint = ConstraintState(last_buy_at=T0)
    asof = T0 + timedelta(hours=4)  # only 4h elapsed, need 8h

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "HOLD"
    assert result.reason_code == reasons.COOLDOWN_ACTIVE


def test_buy_allowed_after_cooldown():
    """BUY passes when cooldown window has elapsed."""
    spec = _spec(cooldown_periods=2)
    constraint = ConstraintState(last_buy_at=T0)
    asof = T0 + timedelta(hours=8)  # exactly 8h elapsed

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"
    assert result.reason_code == reasons.BUY_TRIGGERED


def test_sell_not_blocked_by_buy_cooldown():
    """SELL passes even if last buy was very recent — sides are independent."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=False)
    constraint = ConstraintState(last_buy_at=T0)  # buy just happened, no sell history
    asof = T0 + timedelta(hours=1)

    result = evaluate_strategy(
        spec=spec, market=SELL_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "SELL"
    assert result.reason_code == reasons.SELL_TRIGGERED


def test_first_trade_no_cooldown():
    """First trade ever is never blocked by cooldown."""
    spec = _spec(cooldown_periods=10)
    constraint = ConstraintState()  # no prior trades

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=T0), constraint=constraint,
    )
    assert result.action == "BUY"


def test_cooldown_zero_means_no_constraint():
    """cooldown_periods=0 means no enforcement."""
    spec = _spec(cooldown_periods=0)
    constraint = ConstraintState(last_buy_at=T0)
    asof = T0 + timedelta(minutes=1)

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"


def test_cooldown_none_means_no_constraint():
    """cooldown_periods=None means no enforcement."""
    spec = _spec(cooldown_periods=None)
    constraint = ConstraintState(last_buy_at=T0)
    asof = T0 + timedelta(minutes=1)

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"


def test_reset_on_opposite_buy_after_sell():
    """With reset_on_opposite=True, a sell clears the buy-side wait instantly —
    not "reference moves to the sell, still wait the full window from there".
    This is the harvest behavior the checkbox exists for: buy the dip, sell the
    bounce, buy the next dip, without being gated by the strategy's own history."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=True)  # 8h cooldown

    # Last buy was at T0, but a sell happened at T0+2h (opposite side, more
    # recent than the last buy) — clears the buy-side wait to zero.
    constraint = ConstraintState(
        last_buy_at=T0,
        last_sell_at=T0 + timedelta(hours=2),
    )

    # Only 3h after the sell, 5h after the original buy — far short of the 8h
    # window either way. Allowed anyway: the sell is more recent than the buy,
    # so the buy-side check clears instantly, regardless of elapsed time.
    asof = T0 + timedelta(hours=5)

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"
    assert result.reason_code == reasons.BUY_TRIGGERED


def test_opposite_side_never_blocked_by_cooldown():
    """An opposite-side trade is never cooldown-gated, no matter how recent the
    same-side history is — cooldown only ever restricts repeats of the SAME side."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=True)  # 8h cooldown
    constraint = ConstraintState(last_buy_at=T0)  # buy just happened

    # A sell signal one minute later — nothing to gate it, opposite side is free.
    asof = T0 + timedelta(minutes=1)
    result = evaluate_strategy(
        spec=spec, market=SELL_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "SELL"
    assert result.reason_code == reasons.SELL_TRIGGERED


def test_reset_on_opposite_is_instant_not_deferred():
    """The reset clears the wait to zero the moment an opposite trade exists —
    it does not impose a fresh cooldown_periods wait measured from that opposite
    trade. A same-side signal one minute after the opposite trade is allowed."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=True)  # 8h cooldown
    constraint = ConstraintState(
        last_buy_at=T0,
        last_sell_at=T0 + timedelta(hours=1),
    )

    # One minute after the sell — nowhere near an 8h wait from either reference,
    # but the reset makes this instant, not deferred.
    asof = T0 + timedelta(hours=1, minutes=1)
    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"
    assert result.reason_code == reasons.BUY_TRIGGERED


def test_reset_is_consumed_by_next_same_side_trade():
    """The reset is spent by the next same-side trade — it doesn't stay open
    indefinitely. Once that trade fires, normal same-side cooldown resumes,
    measured from the NEW trade, not reopened by the old opposite trade."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=True)  # 8h cooldown

    # Buy at T0, sell at T0+1h (opposite, more recent — would clear a buy check).
    # Now the buy side ALREADY fired again at T0+2h (consuming that reset) —
    # constraint reflects the state right after that second buy.
    constraint = ConstraintState(
        last_buy_at=T0 + timedelta(hours=2),
        last_sell_at=T0 + timedelta(hours=1),
    )

    # A third buy signal 3h after the second buy, with no further sell in
    # between — last_sell (T0+1h) is now OLDER than last_buy (T0+2h), so the
    # reset branch doesn't apply. Normal 8h same-side wait from T0+2h: blocked.
    asof = T0 + timedelta(hours=5)  # 3h since the second buy
    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "HOLD"
    assert result.reason_code == reasons.COOLDOWN_ACTIVE

    # 8h after the second buy → allowed, same-side wait fully elapsed.
    asof = T0 + timedelta(hours=10)  # 8h since the second buy
    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"


def test_no_reset_buy_blocked_after_sell():
    """With reset_on_opposite=False, sell does NOT reset buy cooldown."""
    spec = _spec(cooldown_periods=2, reset_on_opposite=False)  # 8h cooldown

    # Buy at T0, sell at T0+1h
    constraint = ConstraintState(
        last_buy_at=T0,
        last_sell_at=T0 + timedelta(hours=1),
    )

    # 7h after buy. Without reset: 7h < 8h = blocked.
    # With reset=False: reference is last_buy_at=T0, so 7h < 8h = blocked.
    asof = T0 + timedelta(hours=7)

    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "HOLD"
    assert result.reason_code == reasons.COOLDOWN_ACTIVE

    # 8h after buy → allowed (cooldown measured from buy, not sell)
    asof = T0 + timedelta(hours=8)
    result = evaluate_strategy(
        spec=spec, market=BUY_MARKET, portfolio=PORTFOLIO,
        ctx=EvalContext(asof=asof), constraint=constraint,
    )
    assert result.action == "BUY"
