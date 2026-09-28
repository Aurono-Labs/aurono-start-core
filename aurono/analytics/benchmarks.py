# aurono/analytics/benchmarks.py

"""
Benchmark analytics: compare a strategy's actual portfolio value against
passive alternatives (Buy & Hold and Dollar-Cost Averaging) using the
strategy's total net injected capital as the shared base.

Design notes
------------
- **Base capital** = sum of CapitalCredited minus CapitalDebited entries on
  the strategy's capital ledger (both recorded as `credit`/`debit` kinds
  with trade_id IS NULL). Fill-related debits carry a trade_id and are
  excluded — we only count user-initiated deposits/withdrawals.
- **Counterfactual framing**: each euro is put to work on the date it
  actually arrived, and a withdrawal is taken out of the position on the
  date it was taken. Benchmarks used to assume the full *final* net base
  was available from day 1, so a deposit made in month five bought at
  month one's price and a withdrawal was subtracted from a position it
  had never been taken out of. A strategy funded once at creation is
  unaffected, which is the case the two framings agree on.
- **Strategy lifetime window**: from the earliest strategy_version
  creation to the latest available candle.
- **DCA interval**: calendar-month or calendar-week boundaries. One buy
  per interval, each deploying the cash that has arrived across the
  boundaries still to come.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Tuple


Interval = Literal["monthly", "weekly"]


# ============================================================
# Data types
# ============================================================

@dataclass
class BenchmarkPoint:
    timestamp_utc: str
    value_eur: float


@dataclass
class BenchmarkSeries:
    strategy_id: str
    symbol: str
    interval: Interval
    base_capital_eur: float
    start_ts: Optional[str]
    end_ts: Optional[str]
    strategy: List[BenchmarkPoint]
    dca: List[BenchmarkPoint]
    buy_and_hold: List[BenchmarkPoint]
    # Context beside the comparison, never a gate on showing it. A strategy
    # whose triggers have not fired is not the same thing as buy & hold - it is
    # holding cash, and that cash is exactly what the comparison measures. On
    # the dev instance 11 of 18 strategies with no round trip hold cash, so
    # suppressing those would hide the most informative reading on the page.
    round_trips: int = 0


# ============================================================
# Net injected capital
# ============================================================

def net_injected_capital(conn: sqlite3.Connection, strategy_id: str) -> Decimal:
    """
    Total EUR value the user put into this strategy at inception + over time.
    Includes:
      - `credit` entries (deposits)
      - `withdraw` entries (withdrawals — these rows are already negative)
      - the market value of a bootstrapped initial position, from the
        `InventoryBootstrapMarked` event (see below)

    Excludes fill-related entries (those carry a trade_id).

    Bootstrapped positions are valued at the market price on the day they were
    bootstrapped, never at `initial_units * acb_price`. The `cost_basis` ledger
    row holds the latter, and it is the right number for ACB and realized P&L,
    but it is a behavioural sell floor rather than money that came in. The two
    can diverge by a wide margin, since `acb_price` is whatever was paid long
    before the strategy existed. Wherever they do, every benchmark base that
    included it is inflated and the strategy looks systematically worse than it
    was.

    Falls back to the `cost_basis` row for any bootstrap with no mark event.
    That covers strategies created before this shipped and not yet backfilled,
    and the ones whose bootstrap date has no candle close enough to price it
    honestly. Today's number is wrong for those, but it is the number they
    already had; silently dropping to cash-only would be a bigger error.

    Note: `'debit'` is retained in the IN-list as a safety net for any
    legacy rows not yet rebuilt under the new kind taxonomy. Once rebuild
    has run on a deployment, only `'withdraw'` rows will exist for user
    withdrawals; buy-fill `'debit'` rows always carry a trade_id and are
    excluded by the trade_id IS NULL filter.

    Including `cost_basis` matters when a user starts a strategy with an
    existing position: without it, the benchmark base would be just the
    cash injected, and the strategy value line would start far above the
    Buy & Hold and DCA lines because it also includes the bootstrapped
    position. Including it gives all three series a fair starting point.

    Amounts in the ledger are signed: credits are positive, withdrawals/debits
    are negative, so a simple SUM yields the net.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS net
        FROM capital_ledger
        WHERE strategy_id = ?
          AND currency = 'EUR'
          AND kind IN ('credit', 'debit', 'withdraw')
          AND trade_id IS NULL
        """,
        (strategy_id,),
    ).fetchone()
    value = row["net"] if isinstance(row, sqlite3.Row) else row[0]
    cash = Decimal(str(value or 0))

    return cash + _bootstrap_base(conn, strategy_id)


def dated_capital_flows(
    conn: sqlite3.Connection, strategy_id: str
) -> List[Tuple[str, Decimal]]:
    """
    The same money `net_injected_capital` totals, but with each amount kept at
    the date it actually moved, oldest first.

    `net_injected_capital` stays the scalar it is: `strategy_summary()` derives
    `net_invested_eur` and `total_pnl_eur` from it and the UI's `netInvested()`
    contract depends on that shape. Two consumers want two shapes of one rule,
    so this is an addition rather than a redefinition - the same split the
    ledger already keeps between `cost_basis` and the bootstrap mark. Their
    totals are pinned equal by a test.

    A benchmark built on the scalar has to assume the whole base was available
    on day 1, which is the counterfactual this module's docstring used to admit
    to: a deposit made in month five bought at month one's price, and a
    withdrawal was subtracted from a position it was never taken out of.

    **Bootstrap inflows are dated from the `InventoryBootstrapped` event, never
    from the mark's own `timestamp_utc`.** A backfilled mark carries the moment
    the backfill ran, so dating from it would place a March position in
    September and buy the benchmark's units at the wrong end of the history.
    The mark supplies the *value*; the bootstrap supplies the *date*.
    """
    flows: List[Tuple[str, Decimal]] = []

    for ts, amount in conn.execute(
        """
        SELECT timestamp_utc, amount
        FROM capital_ledger
        WHERE strategy_id = ?
          AND currency = 'EUR'
          AND kind IN ('credit', 'debit', 'withdraw')
          AND trade_id IS NULL
        """,
        (strategy_id,),
    ).fetchall():
        if ts:
            flows.append((str(ts), Decimal(str(amount or 0))))

    flows.extend(_dated_bootstrap_flows(conn, strategy_id))
    flows.sort(key=lambda f: f[0])
    return flows


def _dated_bootstrap_flows(
    conn: sqlite3.Connection, strategy_id: str
) -> List[Tuple[str, Decimal]]:
    """
    `_bootstrap_base`'s per-bootstrap values, each carrying its bootstrap date.

    Deliberately mirrors `_bootstrap_base`'s resolution order - mark first,
    `cost_basis` row as the fallback, plus marks whose bootstrap wrote no
    cost_basis row at all because `acb_price` was 0. The two must agree on
    value or the dated series would total something the headline does not.
    """
    marked: Dict[str, Decimal] = {}
    for event_id, payload_json in conn.execute(
        """
        SELECT event_id, payload_json
        FROM events
        WHERE strategy_id = ?
          AND event_type = 'InventoryBootstrapMarked'
        """,
        (strategy_id,),
    ).fetchall():
        try:
            payload = json.loads(payload_json)
            marked[payload["bootstrap_event_id"]] = Decimal(
                str(payload["mark_value_eur"])
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue

    # The bootstrap event is the one date that is true regardless of when the
    # mark was written, so every row here is dated from it.
    bootstrap_dates: Dict[str, str] = {
        str(event_id): str(ts)
        for event_id, ts in conn.execute(
            """
            SELECT event_id, timestamp_utc
            FROM events
            WHERE strategy_id = ?
              AND event_type = 'InventoryBootstrapped'
            """,
            (strategy_id,),
        ).fetchall()
        if ts
    }

    out: List[Tuple[str, Decimal]] = []
    seen = set()
    for event_id, amount in conn.execute(
        """
        SELECT event_id, amount
        FROM capital_ledger
        WHERE strategy_id = ?
          AND currency = 'EUR'
          AND kind = 'cost_basis'
          AND trade_id IS NULL
        """,
        (strategy_id,),
    ).fetchall():
        seen.add(event_id)
        ts = bootstrap_dates.get(str(event_id))
        if ts:
            out.append((ts, marked.get(event_id, Decimal(str(amount or 0)))))

    for bootstrap_id, value in marked.items():
        if bootstrap_id not in seen:
            ts = bootstrap_dates.get(str(bootstrap_id))
            if ts:
                out.append((ts, value))

    return out


def _bootstrap_base(conn: sqlite3.Connection, strategy_id: str) -> Decimal:
    """
    EUR the bootstrapped starting position was worth when it was bootstrapped.

    Prefers the `InventoryBootstrapMarked` mark. Per bootstrap event, so a
    strategy with several bootstraps takes the mark for the ones that have it
    and the cost_basis row for the ones that do not.
    """
    marked: Dict[str, Decimal] = {}
    for event_id, payload_json in conn.execute(
        """
        SELECT event_id, payload_json
        FROM events
        WHERE strategy_id = ?
          AND event_type = 'InventoryBootstrapMarked'
        """,
        (strategy_id,),
    ).fetchall():
        try:
            payload = json.loads(payload_json)
            bootstrap_id = payload["bootstrap_event_id"]
            marked[bootstrap_id] = Decimal(str(payload["mark_value_eur"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            # A malformed mark must not take the whole benchmark down with it;
            # the cost_basis fallback below still covers that bootstrap.
            continue

    # capital_ledger.event_id on a cost_basis row is the InventoryBootstrapped
    # event's id, which is what the mark points back at.
    cost_basis_rows = conn.execute(
        """
        SELECT event_id, amount
        FROM capital_ledger
        WHERE strategy_id = ?
          AND currency = 'EUR'
          AND kind = 'cost_basis'
          AND trade_id IS NULL
        """,
        (strategy_id,),
    ).fetchall()

    total = Decimal("0")
    seen = set()
    for event_id, amount in cost_basis_rows:
        seen.add(event_id)
        total += marked.get(event_id, Decimal(str(amount or 0)))

    # A bootstrap with acb_price = 0 writes no cost_basis row at all
    # (write_from_event.py gates on acb_price > 0), so a mark for one would be
    # missed by the loop above. Those are the strategies the old base counted
    # as nothing, and the mark is the first real number they get.
    for bootstrap_id, value in marked.items():
        if bootstrap_id not in seen:
            total += value

    return total


# ============================================================
# Buy & Hold
# ============================================================

def compute_buy_and_hold_series(
    candles: List[list],
    base_eur: Decimal,
    flows: Optional[List[Tuple[str, Decimal]]] = None,
) -> List[BenchmarkPoint]:
    """
    "Every euro you put in, buying the coin on the day you put it in."

    With `flows`, each inflow buys units at the close of the candle on or after
    its own date, and each withdrawal sells units at that date's close. Without
    them, the whole of `base_eur` is deployed at the first candle - the old
    behaviour, kept because the module's direct callers and tests pass a
    positional base, and because a strategy funded once at creation must come
    out identical either way.

    Money is held as cash until its candle arrives rather than being back-dated,
    so a deposit made in month five is not credited with month one's price. Cash
    still counts toward the value, which is what keeps the line comparable to a
    strategy that also holds cash.

    `candles` are [timestamp_ms, open, high, low, close, volume] oldest→newest.
    """
    if not candles:
        return []

    if flows is None:
        if base_eur <= 0:
            return []
        flows = [(_ms_to_iso(int(candles[0][0])), base_eur)]

    if not flows:
        return []

    pending = _flows_to_ms(flows)
    last_idx = len(candles) - 1
    units = Decimal("0")
    cash = Decimal("0")
    i = 0
    out: List[BenchmarkPoint] = []

    for idx, c in enumerate(candles):
        ts_ms = int(c[0])
        ts_iso = _ms_to_iso(ts_ms)
        close = Decimal(str(c[4]))

        # Everything that had arrived by this candle, including anything dated
        # before the window, which lands on the first candle rather than being
        # dropped - the strategy holds that money too. On the final candle,
        # take whatever is left as well: a flow timestamped after the last
        # close is real money, and dropping it would leave the series holding
        # units it no longer owns while `base_capital_eur` still counts the
        # withdrawal. Archiving does exactly that, minutes after the last
        # candle.
        while i < len(pending) and (pending[i][0] <= ts_ms or idx == last_idx):
            cash += pending[i][1]
            i += 1

        if close > 0:
            if cash > 0:
                units += cash / close
                cash = Decimal("0")
            elif cash < 0:
                # A withdrawal sells units at its own date's price. It can only
                # take what is there; the rest stays as a negative balance and
                # is settled by the next inflow, which is what the ledger does.
                wanted = -cash / close
                sold = min(units, wanted)
                units -= sold
                cash += sold * close

        out.append(
            BenchmarkPoint(timestamp_utc=ts_iso, value_eur=float(units * close + cash))
        )
    return out


# ============================================================
# DCA
# ============================================================

def compute_dca_series(
    candles: List[list],
    base_eur: Decimal,
    interval: Interval,
    flows: Optional[List[Tuple[str, Decimal]]] = None,
) -> List[BenchmarkPoint]:
    """
    "The same money, drip-fed on a fixed schedule instead."

    One buy per calendar month (or week) in the window; accumulated units plus
    undeployed cash are valued at every candle's close.

    With `flows`, each boundary deploys the cash that has actually **arrived**,
    split across the boundaries still to come. Without them the whole of
    `base_eur` is assumed present at the first candle - which is the same
    day-1 defect buy & hold had, and worse here, because the chunk size was
    set from the *final* net base and so spent deposits that had not been made
    yet and subtracted withdrawals that had not happened.

    Spreading over the *remaining* boundaries is what makes the single-deposit
    case reduce exactly to the old even split: with `n` boundaries and nothing
    arriving later, the first spends `base/n`, the second spends the remaining
    `base(n-1)/n` over `n-1` boundaries, which is `base/n` again, and so on.
    """
    if not candles:
        return []

    if flows is None:
        if base_eur <= 0:
            return []
        flows = [(_ms_to_iso(int(candles[0][0])), base_eur)]

    if not flows:
        return []

    buy_indices: List[int] = []
    last_bucket: Optional[Tuple] = None
    for idx, c in enumerate(candles):
        bucket = _interval_bucket(int(c[0]), interval)
        if bucket != last_bucket:
            buy_indices.append(idx)
            last_bucket = bucket

    if not buy_indices:
        return []

    buy_set = set(buy_indices)
    pending = _flows_to_ms(flows)
    last_idx = len(candles) - 1
    units = Decimal("0")
    cash = Decimal("0")
    i = 0
    buys_done = 0
    out: List[BenchmarkPoint] = []

    for idx, c in enumerate(candles):
        ts_ms = int(c[0])
        ts_iso = _ms_to_iso(ts_ms)
        close = Decimal(str(c[4]))

        # See the note in compute_buy_and_hold_series: the final candle also
        # takes anything dated after it, so a withdrawal made after the last
        # close is not silently dropped.
        while i < len(pending) and (pending[i][0] <= ts_ms or idx == last_idx):
            cash += pending[i][1]
            i += 1

        if idx in buy_set:
            buys_done += 1
            remaining = len(buy_indices) - buys_done + 1
            if close > 0 and cash > 0 and remaining > 0:
                spend = cash / Decimal(remaining)
                units += spend / close
                cash -= spend

        # A withdrawal is taken out of the position rather than waiting for a
        # schedule it has nothing to do with, same as buy & hold.
        if close > 0 and cash < 0:
            wanted = -cash / close
            sold = min(units, wanted)
            units -= sold
            cash += sold * close

        out.append(
            BenchmarkPoint(timestamp_utc=ts_iso, value_eur=float(units * close + cash))
        )
    return out


# ============================================================
# Strategy actual series
# ============================================================

def _strategy_value_series(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> List[BenchmarkPoint]:
    """Read actual portfolio value from snapshot projection."""
    rows = conn.execute(
        """
        SELECT timestamp_utc, portfolio_value_eur
        FROM portfolio_snapshot_projection
        WHERE strategy_id = ?
          AND portfolio_value_eur IS NOT NULL
        ORDER BY timestamp_utc
        """,
        (strategy_id,),
    ).fetchall()
    return [
        BenchmarkPoint(timestamp_utc=r["timestamp_utc"], value_eur=float(r["portfolio_value_eur"]))
        for r in rows
    ]


def _extend_strategy_series_to_last_candle(
    conn: sqlite3.Connection,
    strategy_id: str,
    series: List[BenchmarkPoint],
    candles: List[list],
) -> List[BenchmarkPoint]:
    """
    Append one point repricing the strategy's last known position at the final
    candle, so the strategy line and the benchmark lines end at the same instant
    (Phase 19.3).

    Without this the comparison differences two values taken days apart. The
    strategy line comes from `portfolio_snapshot_projection`, which is written
    from `DecisionObserved` and therefore stops when a strategy stops being
    evaluated; the benchmark lines are computed from candles and run to today.
    A paused strategy that had never traded, and so held exactly the position
    buy-and-hold holds, therefore reported a loss against it where the honest
    answer is "level": its value at the pause, against buy-and-hold's value
    today. The gap grows with however long the strategy has been idle.

    **Priced from the last candle, deliberately, and not from the freshest price
    available.** `_current_mark_price` in the API layer searches every timeframe
    and would often return a newer price than `candles` holds, since `candles`
    is the strategy's own timeframe. Using it here would make the strategy line
    end on a different price from the benchmark lines and hand the strategy a
    free gain or loss purely from which candle each side happened to read. Both
    sides ending on one candle is the property that matters; being a few minutes
    behind the newest tick is not.

    **Archived strategies are excluded.** Their coins left the measurement
    boundary on the archive date, so their value correctly stops there - the
    same rule `_exit_mark_price` and `_current_mark_price` already follow.
    Aligning *their* two series means truncating the benchmark lines instead,
    which is a different operation on shipped 19.2 behaviour; see the note in
    project_plan.md 19.3.
    """
    if not series or not candles:
        return series

    status_row = conn.execute(
        "SELECT status FROM strategy_state_projection WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()
    if status_row is not None and status_row["status"] == "archived":
        return series

    last_candle_ms = int(candles[-1][0])
    close = candles[-1][4]
    if close is None or float(close) <= 0:
        return series

    # Nothing to add when the strategy's own history already reaches the final
    # candle, which is the normal case for an actively evaluated strategy.
    if _iso_to_ms(series[-1].timestamp_utc) >= last_candle_ms:
        return series

    snapshot = conn.execute(
        """
        SELECT free_eur, reserved_eur, asset_units, reserved_units
        FROM portfolio_snapshot_projection
        WHERE strategy_id = ? AND portfolio_value_eur IS NOT NULL
        ORDER BY timestamp_utc DESC LIMIT 1
        """,
        (strategy_id,),
    ).fetchone()
    if snapshot is None:
        return series

    def _num(value) -> float:
        return float(value) if value is not None else 0.0

    units = _num(snapshot["asset_units"]) + _num(snapshot["reserved_units"])
    value = _num(snapshot["free_eur"]) + _num(snapshot["reserved_eur"]) + units * float(close)

    return [
        *series,
        BenchmarkPoint(timestamp_utc=_ms_to_iso(last_candle_ms), value_eur=value),
    ]


# ============================================================
# Strategy lifetime window + parameters
# ============================================================

@dataclass
class _StrategyContext:
    symbol: str
    timeframe: str
    exchange: str
    start_ts: str  # ISO UTC — earliest version created_at


def load_strategy_context(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> Optional[_StrategyContext]:
    """Resolve symbol/timeframe/exchange and strategy lifetime start.

    Public (no leading underscore) so callers can resolve which
    (symbol, timeframe, exchange) to fetch candles for before calling
    `compute_benchmark_comparison` — this module has no market-data or
    exchange dependency, so candle fetching is the caller's job.
    """
    # Strategy lifetime start = earliest version creation
    first_version = conn.execute(
        """
        SELECT created_at, parameters_json
        FROM strategy_version
        WHERE strategy_id = ?
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (strategy_id,),
    ).fetchone()

    if not first_version:
        return None

    # Prefer the active version's parameters for symbol/timeframe, since
    # v1 may not carry the same asset if it was re-keyed.
    active_row = conn.execute(
        """
        SELECT sv.parameters_json
        FROM strategy_version sv
        JOIN strategy_state_projection ssp
          ON sv.strategy_version_id = ssp.active_version_id
        WHERE ssp.strategy_id = ?
        """,
        (strategy_id,),
    ).fetchone()

    params_row = active_row if active_row else first_version
    try:
        params = json.loads(params_row["parameters_json"])
    except (TypeError, ValueError):
        return None

    symbol = params.get("symbol")
    timeframe = params.get("timeframe")
    exchange = params.get("exchange") or "auto"
    if not symbol or not timeframe:
        return None

    return _StrategyContext(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        start_ts=first_version["created_at"],
    )


# ============================================================
# Top-level composer
# ============================================================

def compute_benchmark_comparison(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    interval: Interval,
    candles_all: List[list],
) -> BenchmarkSeries:
    """
    Compose the three-line benchmark series for a strategy. Empty arrays
    are returned for series that cannot be computed (e.g. no candles, no
    injected capital).

    `candles_all` must already be the full [timestamp_ms, open, high, low,
    close, volume] history for the strategy's (symbol, timeframe, exchange)
    — fetched by the caller via `load_strategy_context` + its own candle
    store, since this module has no market-data dependency by design.
    """
    ctx = load_strategy_context(conn, strategy_id)
    if ctx is None:
        return BenchmarkSeries(
            strategy_id=strategy_id,
            symbol="",
            interval=interval,
            base_capital_eur=0.0,
            start_ts=None,
            end_ts=None,
            strategy=[],
            dca=[],
            buy_and_hold=[],
        )

    base = net_injected_capital(conn, strategy_id)

    # Some strategies may have been created before their candle backfill,
    # so we filter on the strategy start timestamp.
    start_ms = _iso_to_ms(ctx.start_ts)
    candles = [c for c in candles_all if int(c[0]) >= start_ms]

    strategy_series = _strategy_value_series(conn, strategy_id)

    if base <= 0 or not candles:
        return BenchmarkSeries(
            strategy_id=strategy_id,
            symbol=ctx.symbol,
            interval=interval,
            base_capital_eur=float(base),
            start_ts=ctx.start_ts,
            end_ts=strategy_series[-1].timestamp_utc if strategy_series else None,
            strategy=strategy_series,
            dca=[],
            buy_and_hold=[],
        )

    # Both sides must end on the same candle, or the card differences two
    # values taken days apart. See the helper for the measured case.
    strategy_series = _extend_strategy_series_to_last_candle(
        conn, strategy_id, strategy_series, candles
    )

    flows = dated_capital_flows(conn, strategy_id)
    hold_series = compute_buy_and_hold_series(candles, base, flows=flows)
    dca_series = compute_dca_series(candles, base, interval, flows=flows)

    end_ts = hold_series[-1].timestamp_utc if hold_series else ctx.start_ts

    return BenchmarkSeries(
        strategy_id=strategy_id,
        symbol=ctx.symbol,
        interval=interval,
        base_capital_eur=float(base),
        start_ts=ctx.start_ts,
        end_ts=end_ts,
        strategy=strategy_series,
        dca=dca_series,
        buy_and_hold=hold_series,
        round_trips=_round_trip_count(conn, strategy_id, ctx.symbol),
    )


def _round_trip_count(conn: sqlite3.Connection, strategy_id: str, symbol: str) -> int:
    """
    Completed round trips, counted the way the phase settled it: one filled
    sell, taken from `InventoryDecreased` rather than a `side` filter.

    `compute_realized_pnl` reads `inventory_ledger` `consume` entries, which are
    written 1:1 from that event, so this already *is* the settled count - no
    separate counter needed. Verified across every strategy on the dev
    instance: consume, InventoryDecreased and this count agree exactly, while
    `trade_state_projection`'s filled sells read 161 against 181 on an older
    database, which is why the projection route was rejected.

    Imported inside the function because `statistics` imports this module for
    `net_injected_capital`; `pnl` has no such edge, so it is taken directly.
    """
    from aurono.analytics.pnl import compute_realized_pnl

    try:
        return len(compute_realized_pnl(conn, strategy_id=strategy_id, symbol=symbol))
    except Exception:
        # Context beside a figure must never be what takes the figure down.
        return 0


# ============================================================
# Helpers
# ============================================================

def _flows_to_ms(flows: List[Tuple[str, Decimal]]) -> List[Tuple[int, Decimal]]:
    """
    Flows as (epoch_ms, amount), oldest first.

    Comparing against candle timestamps numerically rather than by ISO string.
    The two sides are written by different producers and do not share a format:
    the ledger stores `2026-09-27T15:19:45.152517+00:00` while candles render
    as `2026-09-27T12:00:00Z`. A string compare on those is not just ugly, it
    is wrong in both directions - `+` sorts before `Z`, and the microseconds
    shift the comparison at a character position that has nothing to do with
    time.

    A flow whose timestamp cannot be parsed is dropped rather than raising;
    the base it belongs to is computed separately and a malformed row must not
    take the whole comparison down.
    """
    out: List[Tuple[int, Decimal]] = []
    for ts, amount in flows:
        try:
            out.append((_iso_to_ms(ts), amount))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda f: f[0])
    return out


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_ms(iso: str) -> int:
    s = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _interval_bucket(ts_ms: int, interval: Interval) -> Tuple:
    """Return a bucket key identifying the calendar interval of a timestamp."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if interval == "monthly":
        return (dt.year, dt.month)
    # weekly: ISO week
    iso = dt.isocalendar()
    return (iso[0], iso[1])
