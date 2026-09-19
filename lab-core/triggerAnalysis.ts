/**
 * Trigger Analysis Engine — client-side strategy stress-tester.
 *
 * Mirrors aurono/domain/strategy_eval.py logic exactly:
 * same % change formula, same BUY/SELL conditions, same ACB protection.
 * All functions are pure — no side effects, no API calls.
 *
 * This module is designated "open" in docs/vision_and_mission.md's
 * open-source boundary — it lives outside frontend/ specifically so it can
 * be mirrored to a public repo independently of the proprietary UI. It has
 * zero dependencies on anything else in this monorepo (see
 * tests/domain/test_import_boundaries.py's sibling concern on the Python
 * side, and lab-core/__tests__ for this file's own standalone tests).
 */

import { rsi } from "./indicators";

// ── Types ──

export interface CandleRecord {
  timestamp_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TriggerAnalysisParams {
  candles: CandleRecord[];
  buySigma: number;
  sellSigma: number;
  buyEur: number;
  sellEur: number;
  allocatedEur: number;
  feePct?: number; // Exchange fee %, default 0.25
  minPositionUnits?: number; // Reject sell if remaining position < this
  cooldownPeriods?: number; // Skip N timeframe periods between same-side trades
  cooldownResetOnOpposite?: boolean; // Opposite trade resets cooldown timer
  timeframeMinutes?: number; // Minutes per candle (for cooldown computation)
  initialAssetUnits?: number; // Starting position (units already held)
  initialAcbPrice?: number; // Starting average cost basis per unit
  rsiPeriod?: number; // e.g. 14 — unset disables the RSI gate entirely
  rsiMaxForBuy?: number; // buy also requires rsi <= this
  rsiMinForSell?: number; // sell also requires rsi >= this
}

export type TriggerAction =
  | "BUY_EXECUTED"
  | "SELL_EXECUTED"
  | "BUY_IGNORED_NO_CAPITAL"
  | "BUY_IGNORED_COOLDOWN"
  | "BUY_IGNORED_MIN_NOTIONAL"
  | "BUY_IGNORED_RSI"
  | "BUY_IGNORED_DISABLED"
  | "SELL_IGNORED_NO_INVENTORY"
  | "SELL_IGNORED_BELOW_ACB"
  | "SELL_IGNORED_MIN_POSITION"
  | "SELL_IGNORED_COOLDOWN"
  | "SELL_IGNORED_MIN_NOTIONAL"
  | "SELL_IGNORED_RSI"
  | "SELL_IGNORED_DISABLED"
  | "BUY_CANCELLED"
  | "SELL_CANCELLED"
  | "HOLD";

export interface CandleAnalysisRow {
  timestamp_ms: number;
  close: number;
  changePct: number;
  action: TriggerAction;
  freeEur: number;
  assetUnits: number;
  acbPrice: number | null;
  netBuySaldo: number;
  // Optional per-row thresholds — only set by the Evaluate tab, where the
  // strategy's buy_drop_pct / sell_rise_pct can change across versions.
  // When present, TriggerChart draws stepped threshold lines from these
  // values instead of the scalar props (Simulate continues to use scalars).
  activeBuyThreshold?: number;   // negative (e.g. -5 for a 5% drop)
  activeSellThreshold?: number;  // positive (e.g. +3 for a 3% rise)
  // RSI at this candle — only set when the strategy has rsiPeriod configured.
  // null for the warm-up period (fewer than rsiPeriod prior closes).
  rsi?: number | null;
}

export interface TriggerAnalysisResult {
  // Thresholds
  buyThresholdPct: number;
  sellThresholdPct: number;

  // Per-candle data for charting
  rows: CandleAnalysisRow[];

  // Signal counts
  totalCandles: number;
  buyExecuted: number;
  sellExecuted: number;
  buyIgnoredNoCapital: number;
  sellIgnoredNoInventory: number;
  sellIgnoredBelowAcb: number;
  sellIgnoredMinPosition: number;
  buyIgnoredCooldown: number;
  sellIgnoredCooldown: number;
  buyIgnoredRsi: number;
  sellIgnoredRsi: number;
  buyIgnoredDisabled: number;
  sellIgnoredDisabled: number;
  holdCount: number;

  // Capital safety
  maxNetBuys: number;
  theoreticalEurRequired: number;
  peakEurDrawdown: number;
  capitalSafe: boolean;

  // Streaks
  maxBuyStreakBeforeSell: number;
  maxSellStreakBeforeBuy: number;

  // Fees
  feePct: number;
  totalFeesEur: number;
  totalVolumeEur: number;

  // Final state
  finalFreeEur: number;
  finalAssetUnits: number;
  finalAcbPrice: number | null;
}

// True cost-basis breach vs. a fee-margin near-miss: BELOW_ACB (backend) /
// SELL_IGNORED_BELOW_ACB (here) fires on close < acbPrice*(1+margin%), a
// strict superset of the raw close < acbPrice check. This isolates which
// side of that superset a HOLD landed on, so tooltip/summary copy can say
// "below ACB" only when it's literally true (never for the near-miss case —
// see FT-SELLFLOOR-01's fix for why that distinction matters).
export function sellBelowRawAcb(close: number, acbPrice: number | null): boolean {
  return acbPrice != null && close < acbPrice;
}

export interface SigmaSuggestion {
  parameter: "buySigma" | "sellSigma" | "buyEur" | "allocatedEur";
  current: number;
  suggested: number;
  reason: string;
}

// ── Sigma thresholds ──

/**
 * Compute buy/sell trigger thresholds from historical % changes.
 * buyThreshold = mean - (buySigma × stddev)  → negative number (drops)
 * sellThreshold = mean + (sellSigma × stddev) → positive number (rises)
 */
export function computeSigmaThresholds(
  changes: number[],
  buySigma: number,
  sellSigma: number,
): { buyThresholdPct: number; sellThresholdPct: number } {
  if (changes.length < 2) {
    return { buyThresholdPct: -Infinity, sellThresholdPct: Infinity };
  }

  const mean = changes.reduce((a, b) => a + b, 0) / changes.length;
  const variance =
    changes.reduce((sum, v) => sum + (v - mean) ** 2, 0) / changes.length;
  const stddev = Math.sqrt(variance);

  return {
    buyThresholdPct: mean - buySigma * stddev,
    sellThresholdPct: mean + sellSigma * stddev,
  };
}

// ── Forward-walk simulation ──

export function runTriggerSimulation(
  params: TriggerAnalysisParams,
): TriggerAnalysisResult {
  const { candles, buySigma, sellSigma, buyEur, sellEur, allocatedEur } = params;
  const feePct = params.feePct ?? 0.25;
  const minPositionUnits = params.minPositionUnits ?? 0;
  const cooldownPeriods = params.cooldownPeriods ?? 0;
  const cooldownResetOnOpposite = params.cooldownResetOnOpposite ?? true;
  const timeframeMinutes = params.timeframeMinutes ?? 0;
  const cooldownMs = cooldownPeriods * timeframeMinutes * 60_000;
  const feeMultiplier = 1 - feePct / 100;

  if (candles.length < 2) {
    return emptyResult(buySigma, sellSigma, feePct);
  }

  // Compute % changes (candle-to-candle on close)
  const changes: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1].close;
    if (prev > 0) {
      changes.push(((candles[i].close - prev) / prev) * 100);
    }
  }

  const { buyThresholdPct, sellThresholdPct } = computeSigmaThresholds(
    changes,
    buySigma,
    sellSigma,
  );

  // RSI condition (optional) — computed once, aligned 1:1 with `candles`
  // (index i in rsiSeries corresponds to candles[i], same as the rsi()
  // helper's own null-padded shape). Mirrors aurono/domain/strategy_eval.py.
  const rsiPeriod = params.rsiPeriod ?? 0;
  const rsiSeries: (number | null)[] = rsiPeriod > 0
    ? rsi(candles.map((c) => c.close), rsiPeriod)
    : [];

  // State — optionally start with existing position
  const startUnits = params.initialAssetUnits ?? 0;
  const startAcb = params.initialAcbPrice ?? 0;
  let freeEur = allocatedEur;
  let assetUnits = startUnits;
  let totalCost = startUnits * startAcb; // total EUR spent on position (for ACB)
  let netBuySaldo = 0;

  // Signal-level saldo: tracks what would happen with unlimited capital
  // Used for theoretical capital requirement (per support docs)
  let signalNetSaldo = 0;
  let maxSignalNetBuys = 0;

  // Fees
  let totalFeesEur = 0;
  let totalVolumeEur = 0;

  // Counters
  let buyExecuted = 0;
  let sellExecuted = 0;
  let buyIgnoredNoCapital = 0;
  let sellIgnoredNoInventory = 0;
  let sellIgnoredBelowAcb = 0;
  let sellIgnoredMinPosition = 0;
  let buyIgnoredCooldown = 0;
  let sellIgnoredCooldown = 0;
  let buyIgnoredRsi = 0;
  let sellIgnoredRsi = 0;
  let buyIgnoredDisabled = 0;
  let sellIgnoredDisabled = 0;
  let holdCount = 0;

  // Cooldown state: track last intent timestamps (ms)
  let lastBuyTs = -Infinity;
  let lastSellTs = -Infinity;

  // Peaks
  let maxNetBuys = 0;
  let minFreeEur = allocatedEur;

  // Streaks
  let currentBuyStreak = 0;
  let currentSellStreak = 0;
  let maxBuyStreakBeforeSell = 0;
  let maxSellStreakBeforeBuy = 0;

  const rows: CandleAnalysisRow[] = [];

  // First candle has no % change — just record initial state
  const acbPrice = (): number | null =>
    assetUnits > 0 ? totalCost / assetUnits : null;

  rows.push({
    timestamp_ms: candles[0].timestamp_ms,
    close: candles[0].close,
    changePct: 0,
    action: "HOLD",
    freeEur,
    assetUnits,
    acbPrice: acbPrice(),
    netBuySaldo,
    rsi: rsiPeriod > 0 ? (rsiSeries[0] ?? null) : null,
  });

  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1].close;
    const close = candles[i].close;
    const changePct = prev > 0 ? ((close - prev) / prev) * 100 : 0;

    let action: TriggerAction = "HOLD";

    // Cooldown helper: an opposite-side trade more recent than our own last
    // same-side trade clears the wait instantly (harvest a flip-flopping market
    // without being gated by our own history); a same-side repeat with no
    // opposite trade in between still waits the full window. Must mirror
    // aurono/domain/strategy_eval.py::_cooldown_elapsed() exactly — the two are
    // pinned to matching scenarios independently: see the "flip-flopping" /
    // "consumed by the next same-side trade" tests here and in
    // tests/domain/test_cooldown_constraint.py — keep both in sync.
    const cooldownOk = (side: "buy" | "sell", tsMs: number): boolean => {
      if (cooldownMs <= 0) return true;
      const lastSame = side === "buy" ? lastBuyTs : lastSellTs;
      if (lastSame === -Infinity) return true; // first trade never blocked
      if (cooldownResetOnOpposite) {
        const lastOpposite = side === "buy" ? lastSellTs : lastBuyTs;
        if (lastOpposite > lastSame) return true;
      }
      return (tsMs - lastSame) >= cooldownMs;
    };

    const candleTs = candles[i].timestamp_ms;

    // RSI condition (optional) — a condition gates the trigger, so it's
    // checked first, before cooldown, mirroring aurono/domain/strategy_eval.py.
    const currentRsi = rsiPeriod > 0 ? (rsiSeries[i] ?? null) : null;
    const rsiBlocksBuy = rsiPeriod > 0 && params.rsiMaxForBuy !== undefined &&
      (currentRsi === null || currentRsi > params.rsiMaxForBuy);
    const rsiBlocksSell = rsiPeriod > 0 && params.rsiMinForSell !== undefined &&
      (currentRsi === null || currentRsi < params.rsiMinForSell);

    // BUY check (price dropped below threshold)
    if (changePct <= buyThresholdPct) {
      // Side-disabled check — before signal tracking: a zero buyEur means
      // this side can never trade, so it isn't a real signal at all, not
      // just a blocked one. Mirrors strategy_eval.py's ordering (checked
      // before RSI/cooldown/everything else in the branch).
      if (buyEur <= 0) {
        buyIgnoredDisabled += 1;
        action = "BUY_IGNORED_DISABLED";
      } else {
        // Signal-level tracking (unlimited capital assumption)
        signalNetSaldo += 1;
        if (signalNetSaldo > maxSignalNetBuys) maxSignalNetBuys = signalNetSaldo;

        // RSI check (before cooldown)
        if (rsiBlocksBuy) {
          buyIgnoredRsi += 1;
          action = "BUY_IGNORED_RSI";
        } else if (!cooldownOk("buy", candleTs)) {
          buyIgnoredCooldown += 1;
          action = "BUY_IGNORED_COOLDOWN";
        } else if (freeEur >= buyEur) {
          // Execute BUY — fee reduces units received
          const fee = buyEur * (feePct / 100);
          const effectiveEur = buyEur * feeMultiplier;
          const units = close > 0 ? effectiveEur / close : 0;
          freeEur -= buyEur;
          assetUnits += units;
          totalCost += effectiveEur; // ACB based on what actually bought assets
          totalFeesEur += fee;
          totalVolumeEur += buyEur;
          netBuySaldo += 1;
          buyExecuted += 1;
          action = "BUY_EXECUTED";
          lastBuyTs = candleTs;
        } else {
          buyIgnoredNoCapital += 1;
          action = "BUY_IGNORED_NO_CAPITAL";
        }

        // Streak tracking (all BUY signals)
        currentBuyStreak += 1;
        if (currentSellStreak > maxSellStreakBeforeBuy) {
          maxSellStreakBeforeBuy = currentSellStreak;
        }
        currentSellStreak = 0;
      }
    }
    // SELL check (price rose above threshold)
    else if (changePct >= sellThresholdPct) {
      // Side-disabled check — mirrors the buy side above.
      if (sellEur <= 0) {
        sellIgnoredDisabled += 1;
        action = "SELL_IGNORED_DISABLED";
      } else {
        // Signal-level tracking
        signalNetSaldo -= 1;

        // RSI check (before cooldown), mirrors the buy side above
        if (rsiBlocksSell) {
          sellIgnoredRsi += 1;
          action = "SELL_IGNORED_RSI";
        } else if (!cooldownOk("sell", candleTs)) {
          sellIgnoredCooldown += 1;
          action = "SELL_IGNORED_COOLDOWN";
        } else if (assetUnits <= 0) {
          sellIgnoredNoInventory += 1;
          action = "SELL_IGNORED_NO_INVENTORY";
        } else {
          const currentAcb = acbPrice();
          // Fee-aware floor: a sell exactly at cost still loses the
          // round-trip fee. Margin derived from the Lab's own feePct (2x =
          // buy fee + sell fee) rather than a separate input — at the
          // default feePct=0.25 this equals the backend's default 0.5%
          // min_sell_margin_pct.
          const sellFloor = currentAcb !== null
            ? currentAcb * (1 + (2 * feePct) / 100)
            : null;
          if (sellFloor !== null && close < sellFloor) {
            sellIgnoredBelowAcb += 1;
            action = "SELL_IGNORED_BELOW_ACB";
          } else if (minPositionUnits > 0 && close > 0) {
            const wouldSell = Math.min(sellEur / close, assetUnits);
            if (assetUnits - wouldSell < minPositionUnits) {
              sellIgnoredMinPosition += 1;
              action = "SELL_IGNORED_MIN_POSITION";
            }
          }

          if (action === "HOLD") {
            // Execute SELL — sell sellEur worth of units, fee reduces proceeds
            const unitsToSell = close > 0 ? Math.min(sellEur / close, assetUnits) : 0;
            const grossProceeds = unitsToSell * close;
            const fee = grossProceeds * (feePct / 100);
            const proceeds = grossProceeds * feeMultiplier;

            // Reduce cost proportionally (ACB stays constant)
            if (assetUnits > 0) {
              totalCost -= totalCost * (unitsToSell / assetUnits);
            }
            assetUnits -= unitsToSell;
            freeEur += proceeds;
            totalFeesEur += fee;
            totalVolumeEur += grossProceeds;
            netBuySaldo -= 1;
            sellExecuted += 1;
            action = "SELL_EXECUTED";
            lastSellTs = candleTs;
          }
        }

        // Streak tracking for SELL signals (all outcomes)
        currentSellStreak += 1;
        if (currentBuyStreak > maxBuyStreakBeforeSell) {
          maxBuyStreakBeforeSell = currentBuyStreak;
        }
        currentBuyStreak = 0;
      }
    } else {
      holdCount += 1;
    }

    // Track peaks
    if (netBuySaldo > maxNetBuys) maxNetBuys = netBuySaldo;
    if (freeEur < minFreeEur) minFreeEur = freeEur;

    rows.push({
      timestamp_ms: candles[i].timestamp_ms,
      close,
      changePct,
      action,
      freeEur,
      assetUnits,
      acbPrice: acbPrice(),
      netBuySaldo,
      rsi: currentRsi,
    });
  }

  // Finalize streaks (end of data)
  if (currentBuyStreak > maxBuyStreakBeforeSell) {
    maxBuyStreakBeforeSell = currentBuyStreak;
  }
  if (currentSellStreak > maxSellStreakBeforeBuy) {
    maxSellStreakBeforeBuy = currentSellStreak;
  }

  const theoreticalEurRequired = maxSignalNetBuys * buyEur;
  const peakEurDrawdown = allocatedEur - minFreeEur;

  return {
    buyThresholdPct,
    sellThresholdPct,
    rows,
    totalCandles: candles.length,
    buyExecuted,
    sellExecuted,
    buyIgnoredNoCapital,
    sellIgnoredNoInventory,
    sellIgnoredBelowAcb,
    sellIgnoredMinPosition,
    buyIgnoredCooldown,
    sellIgnoredCooldown,
    buyIgnoredRsi,
    sellIgnoredRsi,
    buyIgnoredDisabled,
    sellIgnoredDisabled,
    holdCount,
    maxNetBuys: maxSignalNetBuys,
    theoreticalEurRequired,
    peakEurDrawdown,
    capitalSafe: allocatedEur >= theoreticalEurRequired,
    maxBuyStreakBeforeSell,
    maxSellStreakBeforeBuy,
    feePct,
    totalFeesEur,
    totalVolumeEur,
    finalFreeEur: freeEur,
    finalAssetUnits: assetUnits,
    finalAcbPrice: acbPrice(),
  };
}

// ── Suggestions ──

export function suggestAdjustments(
  result: TriggerAnalysisResult,
  currentBuySigma: number,
  currentSellSigma: number,
  allocatedEur?: number,
  buyEur?: number,
): SigmaSuggestion[] {
  const suggestions: SigmaSuggestion[] = [];

  const totalBuySignals = result.buyExecuted + result.buyIgnoredNoCapital;
  const totalSellSignals =
    result.sellExecuted + result.sellIgnoredNoInventory + result.sellIgnoredBelowAcb;

  // If >30% of BUY signals are ignored due to capital, suggest raising BUY sigma
  if (totalBuySignals > 0 && result.buyIgnoredNoCapital / totalBuySignals > 0.3) {
    suggestions.push({
      parameter: "buySigma",
      current: currentBuySigma,
      suggested: Math.round((currentBuySigma + 0.5) * 10) / 10,
      reason: `${result.buyIgnoredNoCapital} of ${totalBuySignals} buy signals were skipped because capital ran out. Making buys less sensitive reduces how often the strategy tries to buy.`,
    });
  }

  // If >50% of SELL signals are ignored due to no inventory, suggest raising SELL sigma
  if (totalSellSignals > 0 && result.sellIgnoredNoInventory / totalSellSignals > 0.5) {
    suggestions.push({
      parameter: "sellSigma",
      current: currentSellSigma,
      suggested: Math.round((currentSellSigma + 0.5) * 10) / 10,
      reason: `${result.sellIgnoredNoInventory} of ${totalSellSignals} sell signals were skipped because there was nothing to sell yet. Making sells less sensitive adds patience.`,
    });
  }

  // If not capital safe, suggest raising BUY sigma
  if (!result.capitalSafe && totalBuySignals > 0) {
    const alreadySuggested = suggestions.some((s) => s.parameter === "buySigma");
    if (!alreadySuggested) {
      suggestions.push({
        parameter: "buySigma",
        current: currentBuySigma,
        suggested: Math.round((currentBuySigma + 0.5) * 10) / 10,
        reason: `The worst-case scenario needs €${result.theoreticalEurRequired.toFixed(0)}, which exceeds your allocated capital. Making buys less sensitive reduces how much capital the strategy could need.`,
      });
    }
  }

  // If <40% of capital was ever deployed, capital is sitting idle
  if (
    allocatedEur != null && allocatedEur > 0 &&
    result.capitalSafe &&
    result.peakEurDrawdown / allocatedEur < 0.6 &&
    result.buyExecuted > 0 // only suggest if the strategy did trade
  ) {
    const deployedPct = Math.round((result.peakEurDrawdown / allocatedEur) * 100);
    const hasBuySigmaSuggestion = suggestions.some((s) => s.parameter === "buySigma");

    if (!hasBuySigmaSuggestion) {
      suggestions.push({
        parameter: "buySigma",
        current: currentBuySigma,
        suggested: Math.round((currentBuySigma - 0.3) * 10) / 10,
        reason: `Only ${deployedPct}% of your capital was ever used, the rest sat idle. Lowering buy sensitivity triggers more buys, putting your capital to work.`,
      });
    }

    if (buyEur != null && buyEur > 0) {
      suggestions.push({
        parameter: "buyEur",
        current: buyEur,
        suggested: Math.round(buyEur * 1.5),
        reason: `Only ${deployedPct}% of your capital was ever used. Increasing the buy amount deploys more per dip, so your capital works harder.`,
      });
    }
  }

  return suggestions;
}

// ── Helpers ──

function emptyResult(
  _buySigma: number,
  _sellSigma: number,
  feePct = 0.25,
): TriggerAnalysisResult {
  return {
    buyThresholdPct: 0,
    sellThresholdPct: 0,
    rows: [],
    totalCandles: 0,
    buyExecuted: 0,
    sellExecuted: 0,
    buyIgnoredNoCapital: 0,
    sellIgnoredNoInventory: 0,
    sellIgnoredBelowAcb: 0,
    sellIgnoredMinPosition: 0,
    buyIgnoredCooldown: 0,
    sellIgnoredCooldown: 0,
    buyIgnoredRsi: 0,
    sellIgnoredRsi: 0,
    buyIgnoredDisabled: 0,
    sellIgnoredDisabled: 0,
    holdCount: 0,
    maxNetBuys: 0,
    theoreticalEurRequired: 0,
    peakEurDrawdown: 0,
    capitalSafe: true,
    maxBuyStreakBeforeSell: 0,
    maxSellStreakBeforeBuy: 0,
    feePct,
    totalFeesEur: 0,
    totalVolumeEur: 0,
    finalFreeEur: 0,
    finalAssetUnits: 0,
    finalAcbPrice: null,
  };
}

// ── Benchmarks ──

export interface EquityCurvePoint {
  timestamp_ms: number;
  strategyValue: number;
  dcaValue: number;
  holdValue: number;
}

/**
 * Compute equity curve: strategy portfolio value at each candle,
 * plus DCA and buy-and-hold benchmarks for comparison.
 */
export function computeEquityCurve(
  rows: CandleAnalysisRow[],
  candles: CandleRecord[],
  allocatedEur: number,
  feePct: number,
): EquityCurveResult {
  if (candles.length < 2) return { curve: [], dcaUnits: 0, holdUnits: 0 };

  const feeMultiplier = 1 - feePct / 100;

  // ── Buy-and-hold: invest all at first candle ──
  const firstClose = candles[0].close;
  const holdUnits = firstClose > 0 ? (allocatedEur * feeMultiplier) / firstClose : 0;

  const holdCashLeft = 0; // all invested

  // ── DCA: one buy per calendar month ──
  // Identify which candle indices are "first of month"
  const monthBuyIndices: number[] = [];
  let lastMonth = -1;
  for (let i = 0; i < candles.length; i++) {
    const d = new Date(candles[i].timestamp_ms);
    const monthKey = d.getUTCFullYear() * 12 + d.getUTCMonth();
    if (monthKey !== lastMonth) {
      monthBuyIndices.push(i);
      lastMonth = monthKey;
    }
  }

  const dcaBuyAmount = monthBuyIndices.length > 0 ? allocatedEur / monthBuyIndices.length : 0;
  let dcaUnits = 0;
  let dcaCashLeft = allocatedEur;
  let nextDcaBuyIdx = 0;

  // Build curve
  const curve: EquityCurvePoint[] = [];

  for (let i = 0; i < candles.length; i++) {
    const close = candles[i].close;
    const ts = candles[i].timestamp_ms;

    // DCA: buy at this candle if it's a month boundary
    if (nextDcaBuyIdx < monthBuyIndices.length && i === monthBuyIndices[nextDcaBuyIdx]) {
      const effectiveEur = dcaBuyAmount * feeMultiplier;
      if (close > 0) {
        dcaUnits += effectiveEur / close;
      }
      dcaCashLeft -= dcaBuyAmount;
      nextDcaBuyIdx++;
    }

    // Strategy value from rows (rows[i] corresponds to candles[i])
    const row = i < rows.length ? rows[i] : rows[rows.length - 1];
    const strategyValue = row.freeEur + row.assetUnits * close;

    curve.push({
      timestamp_ms: ts,
      strategyValue,
      dcaValue: dcaCashLeft + dcaUnits * close,
      holdValue: holdCashLeft + holdUnits * close,
    });
  }

  return { curve, dcaUnits, holdUnits };
}

export interface EquityCurveResult {
  curve: EquityCurvePoint[];
  dcaUnits: number;
  holdUnits: number;
}

export interface BenchmarkSummary {
  strategyReturn: number;
  strategyReturnPct: number;
  dcaReturn: number;
  dcaReturnPct: number;
  holdReturn: number;
  holdReturnPct: number;
  strategyFees: number;
  dcaFees: number;
  holdFees: number;
  strategyUnits: number;
  dcaUnits: number;
  holdUnits: number;
}

export function computeBenchmarkSummary(
  equityResult: EquityCurveResult,
  allocatedEur: number,
  result: TriggerAnalysisResult,
  candles: CandleRecord[],
  feePct: number,
): BenchmarkSummary {
  const { curve, dcaUnits: finalDcaUnits, holdUnits: finalHoldUnits } = equityResult;

  if (curve.length === 0) {
    return {
      strategyReturn: 0, strategyReturnPct: 0,
      dcaReturn: 0, dcaReturnPct: 0,
      holdReturn: 0, holdReturnPct: 0,
      strategyFees: 0, dcaFees: 0, holdFees: 0,
      strategyUnits: 0, dcaUnits: 0, holdUnits: 0,
    };
  }

  const last = curve[curve.length - 1];

  const strategyReturn = last.strategyValue - allocatedEur;
  const dcaReturn = last.dcaValue - allocatedEur;
  const holdReturn = last.holdValue - allocatedEur;

  const pct = (v: number) => allocatedEur > 0 ? (v / allocatedEur) * 100 : 0;

  // DCA fees: count month boundaries
  let lastMonth = -1;
  let dcaMonths = 0;
  for (const c of candles) {
    const d = new Date(c.timestamp_ms);
    const mk = d.getUTCFullYear() * 12 + d.getUTCMonth();
    if (mk !== lastMonth) { dcaMonths++; lastMonth = mk; }
  }
  const dcaBuyAmount = dcaMonths > 0 ? allocatedEur / dcaMonths : 0;
  const dcaFees = dcaMonths * dcaBuyAmount * (feePct / 100);
  const holdFees = allocatedEur * (feePct / 100);

  return {
    strategyReturn,
    strategyReturnPct: pct(strategyReturn),
    dcaReturn,
    dcaReturnPct: pct(dcaReturn),
    holdReturn,
    holdReturnPct: pct(holdReturn),
    strategyFees: result.totalFeesEur,
    dcaFees,
    holdFees,
    strategyUnits: result.finalAssetUnits,
    dcaUnits: finalDcaUnits,
    holdUnits: finalHoldUnits,
  };
}

export interface UndersizedSides {
  buyUndersized: boolean;
  sellUndersized: boolean;
}

/**
 * Estimates, at the given reference price, whether the configured buy/sell
 * EUR amounts would size below the exchange's real minimum order quantity.
 * An approximation (price moves; the exchange re-checks at trade time) —
 * used only for an advisory warning, never to block anything.
 *
 * A side set to exactly 0 is disabled (a documented valid one-sided
 * strategy, not an accidentally-small amount) — never flagged as
 * undersized, since 0 / price is always < any positive minimum and would
 * otherwise warn on every disabled side, every time.
 */
export function estimateUndersizedSides(
  buyEur: number,
  sellEur: number,
  referencePrice: number,
  minOrderBaseUnits: number | null,
): UndersizedSides {
  if (!minOrderBaseUnits || minOrderBaseUnits <= 0 || referencePrice <= 0) {
    return { buyUndersized: false, sellUndersized: false };
  }
  return {
    buyUndersized: buyEur > 0 && buyEur / referencePrice < minOrderBaseUnits,
    sellUndersized: sellEur > 0 && sellEur / referencePrice < minOrderBaseUnits,
  };
}

export interface RsiConfigIssue {
  periodTooShort: boolean;
  buyOutOfRange: boolean;
  sellOutOfRange: boolean;
  // Soft heads-up, not a hard problem: a valid (0-100) threshold on the
  // "wrong" side of neutral (50) barely filters anything — e.g. a buy
  // ceiling of 90 lets almost every price dip through, since RSI is below
  // 90 most of the time. Not capped/blocked (legitimate strategies do use
  // tighter or looser bands than the 30/70 default), just flagged.
  buyAboveNeutral: boolean;
  sellBelowNeutral: boolean;
}

/**
 * Flags an RSI configuration that can never do what it looks like it does.
 * RSI is mathematically bounded to 0-100, so a threshold outside that range
 * makes the gate a permanent no-op (e.g. rsiMaxForBuy=120 always passes,
 * silently — the strategy/simulation would look RSI-confirmed without RSI
 * ever actually blocking anything). A period below 2 can't compute a
 * meaningful average gain/loss either. Same 0-100/≥2 bounds the submit-time
 * validators (CreateStrategy.tsx, strategyParamsFormHelpers.ts) already
 * enforce — this just surfaces the same check on surfaces with no submit
 * step (Lab Simulate, Quick Test), where those validators never run.
 */
export function checkRsiConfig(
  rsiPeriod: number,
  rsiMaxForBuy: number,
  rsiMinForSell: number,
): RsiConfigIssue {
  if (rsiPeriod <= 0) {
    return {
      periodTooShort: false,
      buyOutOfRange: false,
      sellOutOfRange: false,
      buyAboveNeutral: false,
      sellBelowNeutral: false,
    };
  }
  const buyOutOfRange = !(rsiMaxForBuy >= 0 && rsiMaxForBuy <= 100);
  const sellOutOfRange = !(rsiMinForSell >= 0 && rsiMinForSell <= 100);
  return {
    periodTooShort: rsiPeriod < 2,
    buyOutOfRange,
    sellOutOfRange,
    buyAboveNeutral: !buyOutOfRange && rsiMaxForBuy > 50,
    sellBelowNeutral: !sellOutOfRange && rsiMinForSell < 50,
  };
}
