"""
Money-weighted rate of return for the whole Aurono Start account.

MWRR is the internal rate of return of the dated capital flows, the measure a
spreadsheet calls XIRR. It answers "what did my money actually earn, given how
much was in and when?", which is the question the account level exists to
answer: the flows crossing Start's boundary are the user's own decisions, so
removing their timing would answer a question nobody asked.

**This is an account-level measure only.** Per strategy the right measure is
TWR, which strips funding timing out precisely because there you are grading
the ruleset rather than the funding. The two disagree by design and both are
correct; putting MWRR on a strategy page would blame its rules for the user's
deposit timing.

What crosses the boundary, and where each piece comes from:

  cash in     `CapitalCredited`            exact amount, exact date
  cash out    `CapitalDebited`             exact amount, exact date
  coins in    `InventoryBootstrapped`      valued by `InventoryBootstrapMarked`
  coins out   `StrategyArchived`           valued by `InventoryExitMarked`

The first three are already assembled by `dated_capital_flows()`, which this
module reuses rather than re-deriving. Only the fourth is added here, because a
strategy's archive releases its cash as an ordinary withdrawal row but says
nothing about coins that stayed on the exchange.

Deliberately outside the boundary: exchange deposits, idle cash at the
exchange, and anything traded by hand. Part of a deposit may fund things that
have nothing to do with Start, so this is not "your real return" and the
surface must not label it as such.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

# `_iso_to_ms` rather than a second copy of it: the ledger writes
# `2026-09-27T15:19:45.152517+00:00` while candles write `2026-09-27T12:00:00Z`,
# and comparing those as strings is wrong in both directions. 19.5 shipped that
# bug once already. One parser, imported, beats two that drift.
from aurono.analytics.benchmarks import _iso_to_ms, dated_capital_flows

MS_PER_DAY = 86_400_000
# 365 rather than 365.25: this is the XIRR convention, so a figure computed here
# matches what the same flows give in a spreadsheet. Single-sourced because it
# appears both in the discounting and in the de-annualisation, and the two
# disagreeing would be invisible.
DAYS_PER_YEAR = 365.0

# Pinned deliberately rather than widened until production stopped complaining.
# Bracket width decides convergence: during the 19.6 survey two strategies found
# no root at hi=1e6 and resolved at hi=1e9. `lo` sits just above -1 because a
# rate of exactly -100% makes every discount factor zero.
BRACKET_LO = -0.9999
BRACKET_HI = 1e9

# Below this much capital actually at work, MWRR stops describing anything. A
# strategy averaging EUR 15.48 read +290.4% against a period return of +75.2%
# in the 19.6 survey: the arithmetic is right and the figure is meaningless.
MIN_AVERAGE_CAPITAL_EUR = Decimal("25")

# Below a year every return measure amplifies noise into a headline: a 5% gain
# over three weeks annualises to roughly 130%. Under the floor the surface shows
# the period return with its window stated instead.
ANNUALISE_FLOOR_DAYS = 365

# A rate needs a window to be a rate over. Below a day there is nothing to
# de-annualise onto: `(1 + r) ** 0 - 1` is exactly 0, so a brand-new account
# would read "grew about 0.0%" however well or badly its first hours went - a
# claim of breaking even, made by arithmetic rather than by measurement. This is
# most reachable on the account of someone who just funded their first strategy,
# which is the worst moment to show a number that is not true.
MIN_WINDOW_DAYS = 1

# Why `rate` is None. The surface shows the euro figure alone for any of these
# rather than substituting a number it cannot defend.
REASON_NO_FLOWS = "no_flows"
REASON_SINGLE_SIGN = "single_sign_flows"
REASON_NO_CONVERGENCE = "no_convergence"
REASON_BELOW_EXPOSURE_FLOOR = "below_exposure_floor"
REASON_WINDOW_TOO_SHORT = "window_too_short"


@dataclass(frozen=True)
class AccountReturn:
    """
    The account's money-weighted return, with everything needed to quote it
    honestly beside it.

    A rate never appears without its denominator, so `base_eur` and
    `window_days` are part of the result rather than something the caller
    re-derives. `annualised` says which of the two readings `rate` is: per year
    above the track-record floor, over the actual window below it.
    """

    rate: Optional[Decimal]
    annualised: bool
    window_days: int
    base_eur: Decimal
    terminal_value_eur: Decimal
    average_capital_eur: Decimal
    flow_count: int
    unavailable_reason: Optional[str]
    unpriced_bootstraps: int
    unpriced_bootstrap_eur: Decimal


# ============================================================
# The solver
# ============================================================

def npv(rate: float, flows: Sequence[Tuple[int, Decimal]], t0_ms: int) -> float:
    """
    Net present value of a dated flow series at `rate`, in years from `t0_ms`.

    Sign convention, which is the single easiest thing here to get backwards:
    the series is **investor-positive**. Money handed to strategies is positive,
    money coming back is negative, and the value still held is appended as a
    final negative. A root of this function is a root of the sign-flipped one
    too, so the convention costs nothing as long as it is applied consistently -
    and `dated_capital_flows()` already uses it, which is why it was chosen.
    """
    total = 0.0
    base = 1.0 + rate
    for ts_ms, amount in flows:
        years = (ts_ms - t0_ms) / (MS_PER_DAY * DAYS_PER_YEAR)
        try:
            discount = base ** years
        except (OverflowError, ValueError):
            discount = float("inf")
        if discount == 0.0:
            # Underflow at the bottom of the bracket: the term dominates, and
            # its sign is the flow's own.
            return float("-inf") if amount < 0 else float("inf")
        total += float(amount) / discount
    return total


def solve_irr_bisection(
    flows: Sequence[Tuple[int, Decimal]],
    *,
    lo: float = BRACKET_LO,
    hi: float = BRACKET_HI,
    tol: float = 1e-10,
    max_iter: int = 300,
) -> Optional[float]:
    """
    The annual rate at which the series discounts to zero, or None.

    Bisection rather than Newton: it cannot diverge, it needs no derivative, and
    a bounded bracket makes "no answer" an explicit outcome rather than a
    silent one. **None means none** - never 0.0, which would read on the surface
    as a real result of "you broke even".

    Returns None when the bracket holds no sign change. That covers a total
    loss (every flow positive, nothing came back, so NPV is positive for every
    rate) and any series whose root lies outside a deliberately pinned bracket.
    """
    if len(flows) < 2:
        return None

    t0_ms = min(ts for ts, _ in flows)

    f_lo = npv(lo, flows, t0_ms)
    f_hi = npv(hi, flows, t0_ms)

    if f_lo != f_lo or f_hi != f_hi:  # NaN
        return None
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, flows, t0_ms)
        if f_mid == 0.0 or (hi - lo) / 2.0 < tol:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def average_capital(
    flows: Sequence[Tuple[int, Decimal]], *, start_ms: int, end_ms: int
) -> Decimal:
    """
    Time-weighted average capital at work over the window: the Modified Dietz
    denominator, each contribution weighted by the fraction of the window it
    was present for.

    This is what the exposure floor is measured against. Net contributions
    alone would not do: an account funded on the last day of a year has barely
    any capital at work even though its net base looks healthy.

    **The window is passed in rather than derived from the flows.** Taking the
    earliest flow in the list as the start makes a single late deposit span its
    own whole window and weight 1.0, which is exactly the case the floor exists
    to catch.
    """
    if not flows:
        return Decimal("0")

    span = end_ms - start_ms
    if span <= 0:
        return sum((a for _, a in flows), Decimal("0"))

    total = Decimal("0")
    for ts_ms, amount in flows:
        weight = Decimal(str(max(0.0, min(1.0, (end_ms - ts_ms) / span))))
        total += amount * weight
    return total


# ============================================================
# Assembling the account's flows
# ============================================================

def _archived_exit_flows(
    conn: sqlite3.Connection, strategy_id: str
) -> List[Tuple[str, Decimal]]:
    """
    The coins an archived strategy still held, as a dated outflow.

    Archiving withdraws the strategy's EUR - already an ordinary `withdraw` row
    that `dated_capital_flows()` picks up - but leaves coins on the exchange,
    no longer traded. That value genuinely left Start's measurement boundary, so
    it is a distribution, not a holding, and it belongs in the series at the
    date it crossed.

    **Dated from `StrategyArchived`, never from the mark's own timestamp**, for
    the same reason the bootstrap inflow is dated from `InventoryBootstrapped`:
    a backfilled mark carries the moment the backfill ran. The mark supplies the
    value; the archive supplies the date.
    """
    archived_at: Optional[str] = None
    for (ts,) in conn.execute(
        """
        SELECT timestamp_utc
        FROM events
        WHERE strategy_id = ? AND event_type = 'StrategyArchived'
        ORDER BY timestamp_utc DESC
        """,
        (strategy_id,),
    ).fetchall():
        if ts:
            archived_at = str(ts)
            break

    if archived_at is None:
        return []

    for (payload_json,) in conn.execute(
        """
        SELECT payload_json
        FROM events
        WHERE strategy_id = ? AND event_type = 'InventoryExitMarked'
        ORDER BY timestamp_utc DESC
        """,
        (strategy_id,),
    ).fetchall():
        try:
            value = Decimal(str(json.loads(payload_json)["mark_value_eur"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            # A malformed mark must not take the account figure down with it.
            continue
        if value > 0:
            return [(archived_at, -value)]
        return []

    # Archived with no mark: either archived holding nothing, or archived before
    # the mark shipped and not yet backfilled. Contributing zero is right for the
    # first and is the number it already had for the second.
    return []


def count_unpriced_bootstraps(conn: sqlite3.Connection) -> Tuple[int, Decimal]:
    """
    Bootstraps still valued at what the user paid rather than what the coins
    were worth on the day, because no candle exists to price them.

    Disclosed beside the figure rather than used to suppress it. The remaining
    production cases are both a delisted asset, where no candle exists at any
    depth on any timeframe and none ever will - a rare, structural, explainable
    case that wants honest copy more than arithmetic. Suppressing the account
    figure over it would hide a number that is right for every other strategy.
    """
    marked: set = set()
    for (payload_json,) in conn.execute(
        "SELECT payload_json FROM events WHERE event_type = 'InventoryBootstrapMarked'"
    ).fetchall():
        try:
            marked.add(json.loads(payload_json)["bootstrap_event_id"])
        except (ValueError, KeyError, TypeError):
            continue

    count = 0
    total = Decimal("0")
    for event_id, amount in conn.execute(
        """
        SELECT event_id, amount
        FROM capital_ledger
        WHERE currency = 'EUR' AND kind = 'cost_basis' AND trade_id IS NULL
        """
    ).fetchall():
        if event_id not in marked:
            count += 1
            total += Decimal(str(amount or 0))
    return count, total


def account_flows(conn: sqlite3.Connection) -> List[Tuple[int, Decimal]]:
    """
    Every capital flow across Start's boundary, every strategy, archived
    included, in epoch milliseconds and oldest first.

    **Rotation is not a flow.** Moving capital from one strategy to another is
    an internal transfer, not money leaving the measured pool, and it needs no
    pairing logic here: the rebalancer writes it as a withdrawal on the source
    and a credit on the target within milliseconds, so the two cancel exactly in
    the merged series. That is a property worth a test rather than an
    assumption - it is invisible until someone rebalances, and it would be
    silently wrong if either half ever stopped being written.

    Excluding archived strategies would be survivorship bias: closing the ones
    that did badly would improve the account's return, which is backwards.
    """
    flows: List[Tuple[str, Decimal]] = []

    for (strategy_id,) in conn.execute(
        "SELECT strategy_id FROM strategy_state_projection"
    ).fetchall():
        sid = str(strategy_id)
        flows.extend(dated_capital_flows(conn, sid))
        flows.extend(_archived_exit_flows(conn, sid))

    out: List[Tuple[int, Decimal]] = []
    for ts, amount in flows:
        try:
            out.append((_iso_to_ms(ts), amount))
        except (ValueError, TypeError):
            # Same treatment an unparseable stamp gets in the benchmark path.
            continue

    # Sorted on milliseconds, not on the ISO string: the stamps come from
    # different producers and differ in both suffix and precision.
    out.sort(key=lambda f: f[0])
    return out


# ============================================================
# The account figure
# ============================================================

def compute_account_return(
    conn: sqlite3.Connection,
    *,
    terminal_value_eur: Decimal,
    now_ms: int,
) -> AccountReturn:
    """
    The account's money-weighted return.

    `terminal_value_eur` is what the account holds today, summed across
    non-archived strategies and passed in by the caller. It is not resolved
    here: pricing a position needs the candle store and, at the last rung, a
    live exchange call, and this module is in the open trust layer where
    reaching into either is a boundary violation. Same contract as
    `compute_benchmark_comparison()`.
    """
    flows = account_flows(conn)
    base = sum((a for _, a in flows), Decimal("0"))
    unpriced_count, unpriced_eur = count_unpriced_bootstraps(conn)

    def unavailable(reason: str, window_days: int = 0, avg: Decimal = Decimal("0")):
        return AccountReturn(
            rate=None,
            annualised=False,
            window_days=window_days,
            base_eur=base,
            terminal_value_eur=terminal_value_eur,
            average_capital_eur=avg,
            flow_count=len(flows),
            unavailable_reason=reason,
            unpriced_bootstraps=unpriced_count,
            unpriced_bootstrap_eur=unpriced_eur,
        )

    if not flows:
        return unavailable(REASON_NO_FLOWS)

    t0_ms = flows[0][0]
    window_days = max(0, (now_ms - t0_ms) // MS_PER_DAY)

    # Computed before the window guard so the diagnostic field is never a
    # misleading zero: an account funded this morning has its full capital at
    # work, and reporting 0 there reads as "nothing invested" rather than
    # "not computed".
    avg = average_capital(flows, start_ms=t0_ms, end_ms=now_ms)

    if window_days < MIN_WINDOW_DAYS:
        return unavailable(REASON_WINDOW_TOO_SHORT, window_days, avg)

    if avg < MIN_AVERAGE_CAPITAL_EUR:
        return unavailable(REASON_BELOW_EXPOSURE_FLOOR, window_days, avg)

    # The value still held closes the series: a notional distribution of
    # everything the account would realise today.
    solved_series = list(flows) + [(now_ms, -terminal_value_eur)]

    signs = {a > 0 for _, a in solved_series if a != 0}
    if len(signs) < 2:
        return unavailable(REASON_SINGLE_SIGN, window_days, avg)

    annual = solve_irr_bisection(solved_series)
    if annual is None:
        return unavailable(REASON_NO_CONVERGENCE, window_days, avg)

    annualised = window_days >= ANNUALISE_FLOOR_DAYS
    if annualised:
        rate = Decimal(str(annual))
    else:
        # De-annualised to the window actually observed, off the same solve, so
        # the two readings cannot disagree about the underlying answer.
        years = window_days / DAYS_PER_YEAR
        try:
            rate = Decimal(str((1.0 + annual) ** years - 1.0))
        except (OverflowError, ValueError):
            return unavailable(REASON_NO_CONVERGENCE, window_days, avg)

    return AccountReturn(
        rate=rate,
        annualised=annualised,
        window_days=window_days,
        base_eur=base,
        terminal_value_eur=terminal_value_eur,
        average_capital_eur=avg,
        flow_count=len(flows),
        unavailable_reason=None,
        unpriced_bootstraps=unpriced_count,
        unpriced_bootstrap_eur=unpriced_eur,
    )
