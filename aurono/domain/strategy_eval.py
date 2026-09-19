from .types import *
from .math import pct_change
from .indicators import rsi
from . import reasons
from datetime import timedelta

_TIMEFRAME_MINUTES = {
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}


def _cooldown_elapsed(
    side: str,
    spec: StrategySpec,
    constraint: ConstraintState,
    asof: datetime,
) -> bool:
    """Return True if cooldown has elapsed (trade is allowed)."""
    if not spec.cooldown_periods or spec.cooldown_periods <= 0:
        return True

    tf_minutes = _TIMEFRAME_MINUTES.get(spec.timeframe)
    if tf_minutes is None:
        return True  # unknown timeframe = no enforcement

    cooldown = timedelta(minutes=spec.cooldown_periods * tf_minutes)

    if side == "buy":
        last_same = constraint.last_buy_at
        last_opposite = constraint.last_sell_at
    else:
        last_same = constraint.last_sell_at
        last_opposite = constraint.last_buy_at

    # First trade ever on this side → never blocked
    if last_same is None:
        return True

    # An opposite-side trade more recent than our own last same-side trade clears
    # the wait instantly — this is what lets a strategy harvest a flip-flopping
    # market (buy the dip, sell the bounce, buy the next dip...) without being
    # gated by its own history, while a same-side repeat with no opposite trade
    # in between still waits the full window. The clear is spent the moment this
    # side trades again; normal same-side timing then resumes from that trade.
    # Mirrored in frontend/src/lib/triggerAnalysis.ts::cooldownOk() for the Lab
    # simulator — keep both in sync; see the matching scenario tests in each.
    if spec.cooldown_reset_on_opposite and last_opposite and last_opposite > last_same:
        return True

    return (asof - last_same) >= cooldown


def evaluate_strategy(
    *,
    spec: StrategySpec,
    market: MarketWindow,
    portfolio: PortfolioState,
    ctx: EvalContext,
    constraint: ConstraintState,
) -> Decision:

    # -----------------------------
    # Validate market data
    # -----------------------------
    if market.prev_close <= 0 or market.last_close <= 0:
        return Decision(
            action="HOLD",
            reason_code=reasons.INVALID_MARKET_DATA,
            metrics={},
        )

    close_price = market.last_close
    change_pct = pct_change(market.prev_close, close_price)

    metrics = {
        "close_price": close_price,
        "change_pct": change_pct,
        "asof": ctx.asof,
        "free_eur": portfolio.free_eur,
        "asset_units": portfolio.asset_units,
        "acb_price": portfolio.acb_price,
    }

    # =============================
    # BUY LOGIC (INTENT ONLY — NO SIZING)
    # =============================
    if change_pct <= -spec.buy_drop_pct:
        # Side-disabled check — checked before everything else, including
        # RSI: a zero buy_eur means this side can never trade regardless of
        # what else is true, so there's no point evaluating any other
        # condition first. A documented valid config (one-sided strategy),
        # not an error — must resolve to a clean HOLD, never reach sizing.
        if spec.buy_eur <= 0:
            return Decision(
                action="HOLD",
                reason_code=reasons.BUY_DISABLED,
                metrics=metrics,
            )

        # RSI condition — checked first: it gates whether this is a valid
        # trade candidate at all, not a resource/timing constraint like the
        # checks below it. Skipped entirely when rsi_period is None.
        if spec.rsi_period is not None:
            rsi_values = rsi(market.closes or [], spec.rsi_period)
            current_rsi = rsi_values[-1] if rsi_values else None
            if current_rsi is None or current_rsi > spec.rsi_max_for_buy:
                return Decision(
                    action="HOLD",
                    reason_code=reasons.RSI_NOT_OVERSOLD,
                    metrics={**metrics, "rsi": current_rsi},
                )
            # RSI passed the gate — carry the value forward so it's still
            # visible in metrics on BUY_TRIGGERED (and any HOLD below this
            # point), not just on the RSI_NOT_OVERSOLD rejection above.
            metrics = {**metrics, "rsi": current_rsi}

        # Cooldown constraint
        if not _cooldown_elapsed("buy", spec, constraint, ctx.asof):
            return Decision(
                action="HOLD",
                reason_code=reasons.COOLDOWN_ACTIVE,
                metrics=metrics,
            )

        if portfolio.free_eur < spec.buy_eur:
            return Decision(
                action="HOLD",
                reason_code=reasons.CAPITAL_INSUFFICIENT,
                metrics=metrics,
            )

        return Decision(
            action="BUY",
            reason_code=reasons.BUY_TRIGGERED,
            metrics=metrics,
            order_intent=OrderIntent(
                side="buy",
                symbol=spec.symbol,
                quote_eur=spec.buy_eur,   # ✅ EUR ONLY
            ),
        )

    # =============================
    # SELL LOGIC (INTENT ONLY)
    # =============================
    if change_pct >= spec.sell_rise_pct:
        # Side-disabled check — mirrors the buy side above.
        if spec.sell_eur <= 0:
            return Decision(
                action="HOLD",
                reason_code=reasons.SELL_DISABLED,
                metrics=metrics,
            )

        # RSI condition — checked first, mirrors the buy side above.
        if spec.rsi_period is not None:
            rsi_values = rsi(market.closes or [], spec.rsi_period)
            current_rsi = rsi_values[-1] if rsi_values else None
            if current_rsi is None or current_rsi < spec.rsi_min_for_sell:
                return Decision(
                    action="HOLD",
                    reason_code=reasons.RSI_NOT_OVERBOUGHT,
                    metrics={**metrics, "rsi": current_rsi},
                )
            # RSI passed the gate — carry the value forward, mirrors the
            # buy side above.
            metrics = {**metrics, "rsi": current_rsi}

        # Cooldown constraint
        if not _cooldown_elapsed("sell", spec, constraint, ctx.asof):
            return Decision(
                action="HOLD",
                reason_code=reasons.COOLDOWN_ACTIVE,
                metrics=metrics,
            )

        if portfolio.asset_units <= 0:
            return Decision(
                action="HOLD",
                reason_code=reasons.INVENTORY_INSUFFICIENT,
                metrics=metrics,
            )

        if not portfolio.acb_price or portfolio.acb_price <= 0:
            return Decision(
                action="HOLD",
                reason_code=reasons.NO_ACB,
                metrics=metrics,
            )

        # Fee-aware floor: a sell exactly at cost still loses the round-trip
        # fee, so the floor is cost plus a margin, not raw cost. margin=0
        # reproduces the exact old raw-ACB comparison — this is a strict
        # superset of that check, not a separate condition.
        sell_floor = portfolio.acb_price * (Decimal("1") + spec.min_sell_margin_pct / Decimal("100"))
        if close_price < sell_floor:
            return Decision(
                action="HOLD",
                reason_code=reasons.BELOW_ACB,
                metrics=metrics,
            )

        # Minimum position constraint
        if spec.min_position_units and spec.min_position_units > 0:
            # Clamped to the actual position — mirrors triggerAnalysis.ts's
            # Math.min(sellEur/close, assetUnits). A sell_eur sized above the
            # position can never realize more units than are held, so
            # `remaining` must not go negative.
            sell_units = min(spec.sell_eur / close_price, portfolio.asset_units)
            remaining = portfolio.asset_units - sell_units
            if remaining < spec.min_position_units:
                return Decision(
                    action="HOLD",
                    reason_code=reasons.MIN_POSITION_BREACH,
                    metrics=metrics,
                )

        return Decision(
            action="SELL",
            reason_code=reasons.SELL_TRIGGERED,
            metrics=metrics,
            order_intent=OrderIntent(
                side="sell",
                symbol=spec.symbol,
                quote_eur=spec.sell_eur,  # ✅ EUR-based sell
            ),
        )

    # =============================
    # HOLD (no signal)
    # =============================
    return Decision(
        action="HOLD",
        reason_code=reasons.NO_SIGNAL,
        metrics=metrics,
    )
