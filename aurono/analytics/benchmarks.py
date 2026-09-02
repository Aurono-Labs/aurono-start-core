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
- **Counterfactual framing**: benchmarks assume the full current net
  injected base was available from day 1 of the strategy's lifetime.
  The caveat is surfaced in the UI, not hidden.
- **Strategy lifetime window**: from the earliest strategy_version
  creation to the latest available candle.
- **DCA interval**: calendar-month or calendar-week boundaries. One buy
  per interval, equal chunks, first buy on the first candle of the
  window.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Literal, Optional, Tuple


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


# ============================================================
# Net injected capital
# ============================================================

def net_injected_capital(conn: sqlite3.Connection, strategy_id: str) -> Decimal:
    """
    Total EUR value the user put into this strategy at inception + over time.
    Includes:
      - `credit` entries (deposits)
      - `withdraw` entries (withdrawals — these rows are already negative)
      - `cost_basis` entries (EUR value of a bootstrapped initial position,
        written by InventoryBootstrapped as `initial_units * acb_price`)

    Excludes fill-related entries (those carry a trade_id).

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

    Amounts in the ledger are signed: credits/cost_basis are positive,
    withdrawals/debits are negative, so a simple SUM yields the net.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS net
        FROM capital_ledger
        WHERE strategy_id = ?
          AND currency = 'EUR'
          AND kind IN ('credit', 'debit', 'withdraw', 'cost_basis')
          AND trade_id IS NULL
        """,
        (strategy_id,),
    ).fetchone()
    value = row["net"] if isinstance(row, sqlite3.Row) else row[0]
    return Decimal(str(value or 0))


# ============================================================
# Buy & Hold
# ============================================================

def compute_buy_and_hold_series(
    candles: List[list],
    base_eur: Decimal,
) -> List[BenchmarkPoint]:
    """
    Deploy `base_eur` fully at the first candle's close price. Each
    subsequent candle is valued at `units * close`.

    `candles` are [timestamp_ms, open, high, low, close, volume] oldest→newest.
    """
    if not candles or base_eur <= 0:
        return []

    first_close = Decimal(str(candles[0][4]))
    if first_close <= 0:
        return []

    units = base_eur / first_close
    out: List[BenchmarkPoint] = []
    for c in candles:
        ts_iso = _ms_to_iso(int(c[0]))
        close = Decimal(str(c[4]))
        value = units * close
        out.append(BenchmarkPoint(timestamp_utc=ts_iso, value_eur=float(value)))
    return out


# ============================================================
# DCA
# ============================================================

def compute_dca_series(
    candles: List[list],
    base_eur: Decimal,
    interval: Interval,
) -> List[BenchmarkPoint]:
    """
    Split `base_eur` into equal chunks, one chunk per calendar interval
    boundary within the window. Each chunk buys the asset at that candle's
    close price; accumulated units plus undeployed EUR are valued at
    every candle's close.

    The first buy is on the first candle of the window. Subsequent buys
    are on the first candle of each new calendar month (or week).
    """
    if not candles or base_eur <= 0:
        return []

    # 1. Identify buy points: first candle per calendar interval
    buy_indices: List[int] = []
    last_bucket: Optional[Tuple] = None
    for idx, c in enumerate(candles):
        bucket = _interval_bucket(int(c[0]), interval)
        if bucket != last_bucket:
            buy_indices.append(idx)
            last_bucket = bucket

    if not buy_indices:
        return []

    n_buys = len(buy_indices)
    chunk = base_eur / Decimal(n_buys)

    # 2. Walk candles, deploying a chunk at each buy index
    units = Decimal("0")
    undeployed = base_eur
    buy_set = set(buy_indices)

    out: List[BenchmarkPoint] = []
    for idx, c in enumerate(candles):
        close = Decimal(str(c[4]))
        if idx in buy_set and close > 0 and undeployed > 0:
            # Deploy one chunk (or whatever is left on the final chunk)
            spend = min(chunk, undeployed)
            units += spend / close
            undeployed -= spend
        value = units * close + undeployed
        out.append(
            BenchmarkPoint(timestamp_utc=_ms_to_iso(int(c[0])), value_eur=float(value))
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

    hold_series = compute_buy_and_hold_series(candles, base)
    dca_series = compute_dca_series(candles, base, interval)

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
    )


# ============================================================
# Helpers
# ============================================================

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
