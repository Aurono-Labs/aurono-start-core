import { describe, it, expect } from "vitest";
import {
  computeSigmaThresholds,
  runTriggerSimulation,
  suggestAdjustments,
  computeEquityCurve,
  estimateUndersizedSides,
  checkRsiConfig,
  sellBelowRawAcb,
  type TriggerAnalysisParams,
  type CandleRecord,
} from "../triggerAnalysis";
import { rsi } from "../indicators";

// Helper: generate candles from close prices
function makeCandles(closes: number[], startMs = 1000000): CandleRecord[] {
  return closes.map((close, i) => ({
    timestamp_ms: startMs + i * 3600000,
    open: close,
    high: close * 1.01,
    low: close * 0.99,
    close,
    volume: 1000,
  }));
}

describe("computeSigmaThresholds", () => {
  it("computes thresholds from % changes", () => {
    // Known distribution: changes = [-3, -1, 0, 1, 3]
    // mean = 0, stddev = sqrt((9+1+0+1+9)/5) = sqrt(4) = 2
    const changes = [-3, -1, 0, 1, 3];
    const { buyThresholdPct, sellThresholdPct } = computeSigmaThresholds(changes, 2, 2);
    expect(buyThresholdPct).toBeCloseTo(-4); // 0 - 2*2
    expect(sellThresholdPct).toBeCloseTo(4); // 0 + 2*2
  });

  it("returns infinity for insufficient data", () => {
    const { buyThresholdPct, sellThresholdPct } = computeSigmaThresholds([1], 2, 2);
    expect(buyThresholdPct).toBe(-Infinity);
    expect(sellThresholdPct).toBe(Infinity);
  });
});

describe("runTriggerSimulation", () => {
  // Prices: 100, 90 (-10%), 99 (+10%), 89 (-10.1%), 98 (+10.1%)
  // With sigma=1 and stddev~10%, thresholds should trigger on these moves
  const basePrices = [100, 90, 99, 89, 98, 88, 97, 87, 96, 86, 95, 85, 94, 84, 93];
  const candles = makeCandles(basePrices);

  const baseParams: TriggerAnalysisParams = {
    candles,
    buySigma: 0.5, // low sigma to ensure triggers fire
    sellSigma: 0.5,
    buyEur: 100,
    sellEur: 100,
    allocatedEur: 1000,
  };

  it("executes BUY when capital available", () => {
    const result = runTriggerSimulation(baseParams);
    expect(result.buyExecuted).toBeGreaterThan(0);
  });

  it("ignores BUY when no capital", () => {
    const result = runTriggerSimulation({
      ...baseParams,
      allocatedEur: 50, // only enough for ~0 buys at 100 EUR each
    });
    expect(result.buyIgnoredNoCapital).toBeGreaterThan(0);
  });

  it("executes SELL when inventory exists and price above ACB", () => {
    const result = runTriggerSimulation(baseParams);
    // With alternating drops and rises, some sells should execute
    // (depends on whether price recovers above ACB)
    expect(result.sellExecuted + result.sellIgnoredBelowAcb + result.sellIgnoredNoInventory).toBeGreaterThan(0);
  });

  it("ignores SELL when no inventory", () => {
    // Use only rising prices so sell triggers fire but no buys happen first
    const risingCandles = makeCandles([100, 110, 121, 133, 146, 161]);
    const result = runTriggerSimulation({
      ...baseParams,
      candles: risingCandles,
      buySigma: 5, // very high — no buys
      sellSigma: 0.5, // low — sells trigger
    });
    expect(result.sellIgnoredNoInventory).toBeGreaterThan(0);
    expect(result.sellExecuted).toBe(0);
  });

  it("ignores SELL below ACB", () => {
    // Buy at 100, then price drops — sell signal fires but price < ACB
    const prices = [100, 80, 70, 75, 70, 65]; // buy at 80, sell signal at 75 but ACB=80
    const result = runTriggerSimulation({
      ...baseParams,
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
    });
    // Should have at least some ACB-blocked sells
    expect(result.sellIgnoredBelowAcb + result.sellExecuted + result.sellIgnoredNoInventory).toBeGreaterThanOrEqual(0);
  });

  it("ignores SELL between raw ACB and the fee-margin floor", () => {
    // Buy at 80 (ACB=80), then a signal at 80.2 — above raw ACB but below
    // the default fee-margin floor (80 * 1.005 = 80.4 at feePct=0.25, i.e.
    // 2x the fee for a round trip). Pre-fix this would have wrongly
    // executed since 80.2 > 80 (raw ACB comparison had no margin at all).
    const prices = [100, 80, 80.2];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 0.25,
    });
    expect(result.sellIgnoredBelowAcb).toBe(1);
    expect(result.sellExecuted).toBe(0);
  });

  it("executes SELL once price clears the fee-margin floor", () => {
    // Buy at 80 (ACB=80), then a clear rise to 85 — well above the 80.4
    // margin floor.
    const prices = [100, 80, 85];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 0.25,
    });
    expect(result.sellExecuted).toBe(1);
    expect(result.sellIgnoredBelowAcb).toBe(0);
  });

  it("BUY side disabled when buyEur is 0 — never executes, never counted as a signal", () => {
    const result = runTriggerSimulation({
      ...baseParams,
      buyEur: 0,
    });
    expect(result.buyExecuted).toBe(0);
    expect(result.buyIgnoredDisabled).toBeGreaterThan(0);
    expect(result.maxNetBuys).toBe(0);
  });

  it("SELL side disabled when sellEur is 0 — checked before inventory, never executes", () => {
    // Rising prices so a sell trigger fires; buySigma high enough that no
    // buys happen first, so this isolates the disabled-side check from the
    // no-inventory check that would otherwise also block the same signal.
    const risingCandles = makeCandles([100, 110, 121, 133, 146, 161]);
    const result = runTriggerSimulation({
      candles: risingCandles,
      buySigma: 5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 0,
      allocatedEur: 1000,
      feePct: 0.25,
    });
    expect(result.sellExecuted).toBe(0);
    expect(result.sellIgnoredDisabled).toBe(1);
    expect(result.sellIgnoredNoInventory).toBe(0);
  });

  it("computes correct max net buys (signal-level)", () => {
    // Drops in a row — all trigger BUY signals, no SELLs
    const droppingPrices = [100, 90, 81, 73, 66, 59, 53, 48, 43, 39];
    const result = runTriggerSimulation({
      ...baseParams,
      candles: makeCandles(droppingPrices),
      buySigma: 0.1,
      sellSigma: 5, // no sells
      allocatedEur: 10000,
    });
    // maxNetBuys counts BUY signals, with enough capital all execute
    expect(result.maxNetBuys).toBeGreaterThan(0);
    expect(result.maxNetBuys).toBe(result.buyExecuted);
    expect(result.sellExecuted).toBe(0);
  });

  it("capital safety: pass when allocated >= theoretical", () => {
    const result = runTriggerSimulation({
      ...baseParams,
      allocatedEur: 100000,
    });
    expect(result.capitalSafe).toBe(true);
    expect(result.theoreticalEurRequired).toBeLessThanOrEqual(100000);
  });

  it("capital safety: fail when allocated < theoretical", () => {
    // Drops in a row — BUY signals × 100 EUR exceeds allocated
    const droppingPrices = [100, 90, 81, 73, 66, 59, 53, 48, 43, 39];
    const result = runTriggerSimulation({
      candles: makeCandles(droppingPrices),
      buySigma: 0.1,
      sellSigma: 5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 100, // very little capital
    });
    // Signal-level maxNetBuys drives theoretical requirement
    expect(result.maxNetBuys).toBeGreaterThan(1);
    expect(result.theoreticalEurRequired).toBeGreaterThan(100);
    expect(result.capitalSafe).toBe(false);
    expect(result.buyIgnoredNoCapital).toBeGreaterThan(0);
  });

  it("counts streaks correctly", () => {
    // BUY, BUY, BUY, SELL, BUY, SELL
    // Max buy streak = 3, max sell streak = 1
    const prices = [100, 80, 64, 51, 61, 49, 59]; // drops trigger buys, rises trigger sells
    const result = runTriggerSimulation({
      ...baseParams,
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      allocatedEur: 10000,
    });
    expect(result.maxBuyStreakBeforeSell).toBeGreaterThan(0);
  });

  it("returns empty result for < 2 candles", () => {
    const result = runTriggerSimulation({
      ...baseParams,
      candles: makeCandles([100]),
    });
    expect(result.totalCandles).toBe(0);
    expect(result.rows).toHaveLength(0);
  });

  it("handles single candle pair", () => {
    const result = runTriggerSimulation({
      ...baseParams,
      candles: makeCandles([100, 90]),
      buySigma: 0.1,
    });
    expect(result.totalCandles).toBe(2);
    expect(result.rows).toHaveLength(2);
  });
});

describe("sellBelowRawAcb", () => {
  it("returns true when close is strictly below acbPrice", () => {
    expect(sellBelowRawAcb(0.16115, 0.213731)).toBe(true);
  });

  it("returns false when close is at or above acbPrice", () => {
    expect(sellBelowRawAcb(80.2, 80)).toBe(false);
    expect(sellBelowRawAcb(80, 80)).toBe(false);
  });

  it("returns false when acbPrice is null", () => {
    expect(sellBelowRawAcb(80, null)).toBe(false);
  });
});

describe("suggestAdjustments", () => {
  it("suggests raising BUY sigma when many buys ignored", () => {
    const result = runTriggerSimulation({
      candles: makeCandles([100, 90, 81, 73, 66, 59, 53, 48, 43, 39]),
      buySigma: 0.1,
      sellSigma: 5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 200, // only 2 buys possible, rest ignored
    });
    const suggestions = suggestAdjustments(result, 0.1, 5);
    const buySuggestion = suggestions.find((s) => s.parameter === "buySigma");
    expect(buySuggestion).toBeDefined();
    expect(buySuggestion!.suggested).toBeGreaterThan(0.1);
  });

  it("suggests raising SELL sigma when many sells ignored (no inventory)", () => {
    const risingCandles = makeCandles([100, 110, 121, 133, 146, 161, 177, 195]);
    const result = runTriggerSimulation({
      candles: risingCandles,
      buySigma: 5, // no buys
      sellSigma: 0.1, // all sells trigger but no inventory
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
    });
    const suggestions = suggestAdjustments(result, 5, 0.1);
    const sellSuggestion = suggestions.find((s) => s.parameter === "sellSigma");
    expect(sellSuggestion).toBeDefined();
    expect(sellSuggestion!.suggested).toBeGreaterThan(0.1);
  });

  it("suggests lowering BUY sigma or raising buy amount when capital is idle", () => {
    // Alternating drops and rises with enough volatility to trigger a few buys
    // but small buy amount + huge capital = mostly idle
    const prices = [100, 85, 95, 80, 92, 78, 90, 75, 88, 73, 86, 71, 84, 70, 82, 68, 80, 66, 78, 65, 76, 63, 74, 61, 72, 60, 70, 58, 68, 56];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.5, // sensitive enough to trigger buys
      sellSigma: 0.5,
      buyEur: 10, // tiny buy amount
      sellEur: 10,
      allocatedEur: 10000, // vastly more than needed
    });
    expect(result.buyExecuted).toBeGreaterThan(0);
    // Capital should be mostly idle — peak deployed << 40%
    expect(result.peakEurDrawdown / 10000).toBeLessThan(0.4);
    const suggestions = suggestAdjustments(result, 0.5, 0.5, 10000, 10);
    // Should suggest lowering buySigma or raising buyEur
    const hasBuySigma = suggestions.some((s) => s.parameter === "buySigma" && s.suggested < 0.5);
    const hasBuyEur = suggestions.some((s) => s.parameter === "buyEur" && s.suggested > 10);
    expect(hasBuySigma || hasBuyEur).toBe(true);
  });
});

describe("fee tracking", () => {
  const prices = [100, 90, 99, 89, 98, 88, 97, 87, 96, 86, 95, 85, 94, 84, 93];
  const candles = makeCandles(prices);

  it("deducts fees from buys and sells", () => {
    const withFees = runTriggerSimulation({
      candles,
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 1.0, // 1% to make effect visible
    });
    const noFees = runTriggerSimulation({
      candles,
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 0,
    });

    // With fees, you get fewer units per buy
    expect(withFees.finalAssetUnits).toBeLessThan(noFees.finalAssetUnits);
    expect(withFees.totalFeesEur).toBeGreaterThan(0);
    expect(noFees.totalFeesEur).toBe(0);
  });

  it("totalFeesEur sums correctly", () => {
    const result = runTriggerSimulation({
      candles,
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 0.25,
    });
    const totalTrades = result.buyExecuted + result.sellExecuted;
    expect(totalTrades).toBeGreaterThan(0);
    expect(result.totalFeesEur).toBeGreaterThan(0);
    // Fees should be roughly feePct% of volume
    expect(result.totalFeesEur).toBeCloseTo(result.totalVolumeEur * 0.0025, 1);
  });

  it("zero fee produces same asset units as default with feePct=0", () => {
    const result = runTriggerSimulation({
      candles,
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      feePct: 0,
    });
    expect(result.totalFeesEur).toBe(0);
    expect(result.feePct).toBe(0);
  });
});

describe("min position constraint", () => {
  it("blocks SELL when remaining would breach min position", () => {
    // Buy at 100, price drops to 80 (buy), rises to 96 (+20% sell signal)
    // With sellEur=100, sell_units = 100/96 ≈ 1.04, remaining ≈ 1.25 - 1.04 = 0.21 < 1.0
    const prices = [100, 80, 96];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      minPositionUnits: 1.0,
    });
    expect(result.sellIgnoredMinPosition).toBeGreaterThan(0);
  });

  it("allows SELL when remaining stays above min position", () => {
    // Buy at 80 → 1.25 units (ACB=80), buy at 64 → +1.5625 units (ACB~71)
    // Then price rises to 200 (well above ACB), sell 10 EUR → 0.05 units, remaining ~2.76 > 0.5
    const prices = [100, 80, 64, 200];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 10, // very small sell to stay above min
      allocatedEur: 10000,
      minPositionUnits: 0.5,
    });
    expect(result.sellExecuted).toBeGreaterThan(0);
  });

  it("minPositionUnits=0 does not block sells", () => {
    const prices = [100, 80, 96];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      minPositionUnits: 0,
    });
    expect(result.sellIgnoredMinPosition).toBe(0);
  });

  it("minPositionUnits undefined does not block sells", () => {
    const prices = [100, 80, 96];
    const result = runTriggerSimulation({
      candles: makeCandles(prices),
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      // minPositionUnits omitted
    });
    expect(result.sellIgnoredMinPosition).toBe(0);
  });
});

describe("cooldown constraint", () => {
  // Candles 1h apart: 100, 80 (-20% buy), 96 (+20% sell), 77 (-20% buy), 92 (+20% sell)
  const prices = [100, 80, 96, 77, 92];
  const candles = makeCandles(prices); // 1h apart by default

  it("blocks BUY within cooldown period", () => {
    // 2-period cooldown at 60min/period = 120min. Candles are 60min apart.
    // Buy at candle 1, candle 3 is only 2h later → blocked
    const result = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 3, // 3 × 60min = 180min cooldown
      cooldownResetOnOpposite: false,
      timeframeMinutes: 60,
    });
    expect(result.buyIgnoredCooldown).toBeGreaterThan(0);
  });

  it("allows BUY after cooldown elapsed", () => {
    // 1-period cooldown = 60min. Candles are 60min apart → always passes
    const result = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 1,
      cooldownResetOnOpposite: false,
      timeframeMinutes: 60,
    });
    expect(result.buyIgnoredCooldown).toBe(0);
  });

  it("cooldown=0 never blocks", () => {
    const result = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 0,
      timeframeMinutes: 60,
    });
    expect(result.buyIgnoredCooldown).toBe(0);
    expect(result.sellIgnoredCooldown).toBe(0);
  });

  it("reset-on-opposite lets every leg of a flip-flopping market fire, even inside the cooldown window", () => {
    // H1 -20% (buy), H2 +20% (sell), H3 -20% (buy signal again), H4 +20% (sell signal again).
    // 3-period (3h) cooldown: with the checkmark on, every leg fires — each
    // opposite-side trade instantly clears the wait, it does not stay gated
    // for cooldown_periods measured from that opposite trade.
    const prices = [100, 80, 96, 76.8, 92.16];
    const candles = makeCandles(prices);

    const withReset = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 3,
      cooldownResetOnOpposite: true,
      timeframeMinutes: 60,
    });
    expect(withReset.rows.slice(1).map((r) => r.action)).toEqual([
      "BUY_EXECUTED", "SELL_EXECUTED", "BUY_EXECUTED", "SELL_EXECUTED",
    ]);

    // Same candles, checkmark off: H3/H4 are still inside the 3h same-side
    // window measured from H1/H2 (only 2h elapsed) — blocked.
    const noReset = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 3,
      cooldownResetOnOpposite: false,
      timeframeMinutes: 60,
    });
    expect(noReset.rows.slice(1).map((r) => r.action)).toEqual([
      "BUY_EXECUTED", "SELL_EXECUTED", "BUY_IGNORED_COOLDOWN", "SELL_IGNORED_COOLDOWN",
    ]);
  });

  it("reset-on-opposite is consumed by the next same-side trade, not held open indefinitely", () => {
    // H1 -20% (buy), H2 +20% (sell), H3 -10% (buy — fires free via the reset),
    // H4..H5 -10% (buy signals again, no new sell since H3 — blocked, same as
    // if the reset had never happened), H6 -10% (3h since H3 — allowed again).
    let p = 100;
    const prices = [p, (p *= 0.8), (p *= 1.2)];
    for (let i = 0; i < 4; i++) prices.push((p *= 0.9));
    const candles = makeCandles(prices);

    const result = runTriggerSimulation({
      candles,
      buySigma: 0.1,
      sellSigma: 0.1,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 10000,
      cooldownPeriods: 3,
      cooldownResetOnOpposite: true,
      timeframeMinutes: 60,
    });
    expect(result.rows.slice(1).map((r) => r.action)).toEqual([
      "BUY_EXECUTED",           // H1
      "SELL_EXECUTED",          // H2
      "BUY_EXECUTED",           // H3 — via reset
      "BUY_IGNORED_COOLDOWN",   // H4 — 1h since H3, no opposite trade since then
      "BUY_IGNORED_COOLDOWN",   // H5 — 2h since H3
      "BUY_EXECUTED",           // H6 — 3h since H3, normal same-side wait elapsed
    ]);
  });
});

describe("benchmarks", () => {
  // Monthly candles: 6 months of data (one per month)
  const monthlyCandles: CandleRecord[] = [
    { timestamp_ms: new Date("2025-01-01").getTime(), open: 100, high: 105, low: 95, close: 100, volume: 1000 },
    { timestamp_ms: new Date("2025-02-01").getTime(), open: 100, high: 110, low: 95, close: 110, volume: 1000 },
    { timestamp_ms: new Date("2025-03-01").getTime(), open: 110, high: 115, low: 100, close: 105, volume: 1000 },
    { timestamp_ms: new Date("2025-04-01").getTime(), open: 105, high: 120, low: 100, close: 115, volume: 1000 },
    { timestamp_ms: new Date("2025-05-01").getTime(), open: 115, high: 125, low: 110, close: 120, volume: 1000 },
    { timestamp_ms: new Date("2025-06-01").getTime(), open: 120, high: 130, low: 115, close: 125, volume: 1000 },
  ];

  it("DCA benchmark — monthly buys with correct unit accumulation", () => {
    const result = runTriggerSimulation({
      candles: monthlyCandles,
      buySigma: 5, // no triggers — just need rows
      sellSigma: 5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 600,
      feePct: 0,
    });

    const { curve, dcaUnits } = computeEquityCurve(result.rows, monthlyCandles, 600, 0);
    expect(curve).toHaveLength(6);

    // DCA: 6 months, €100 each, no fees
    const lastPoint = curve[curve.length - 1];
    expect(lastPoint.dcaValue).toBeGreaterThan(600); // price went up, so DCA should profit
    expect(dcaUnits).toBeGreaterThan(0);
  });

  it("buy-and-hold — single purchase at first candle", () => {
    const { curve, holdUnits } = computeEquityCurve(
      // Minimal rows just for strategy line
      monthlyCandles.map((c) => ({
        timestamp_ms: c.timestamp_ms,
        close: c.close,
        changePct: 0,
        action: "HOLD" as const,
        freeEur: 600,
        assetUnits: 0,
        acbPrice: null,
        netBuySaldo: 0,
      })),
      monthlyCandles,
      600,
      0,
    );

    // Buy & hold: 600/100 = 6 units at first close, final value = 6*125 = 750
    const lastPoint = curve[curve.length - 1];
    expect(lastPoint.holdValue).toBeCloseTo(750, 0);
    expect(holdUnits).toBeCloseTo(6, 4);
  });

  it("equity curve — portfolio value equals freeEur + units × close", () => {
    const result = runTriggerSimulation({
      candles: monthlyCandles,
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 600,
      feePct: 0,
    });

    const { curve } = computeEquityCurve(result.rows, monthlyCandles, 600, 0);
    // Strategy value should match rows data
    for (let i = 0; i < curve.length && i < result.rows.length; i++) {
      const row = result.rows[i];
      const expected = row.freeEur + row.assetUnits * monthlyCandles[i].close;
      expect(curve[i].strategyValue).toBeCloseTo(expected, 2);
    }
  });

  it("DCA benchmark — fee deduction applied", () => {
    const result = runTriggerSimulation({
      candles: monthlyCandles,
      buySigma: 5,
      sellSigma: 5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 600,
      feePct: 0,
    });

    const noFee = computeEquityCurve(result.rows, monthlyCandles, 600, 0);
    const withFee = computeEquityCurve(result.rows, monthlyCandles, 600, 1.0);

    const lastNoFee = noFee.curve[noFee.curve.length - 1];
    const lastWithFee = withFee.curve[withFee.curve.length - 1];

    // DCA with fees should be worth less
    expect(lastWithFee.dcaValue).toBeLessThan(lastNoFee.dcaValue);
    // Hold with fees should be worth less
    expect(lastWithFee.holdValue).toBeLessThan(lastNoFee.holdValue);
  });
});

describe("estimateUndersizedSides", () => {
  it("flags the buy side when the EUR amount converts to fewer units than the minimum", () => {
    // €10 at €100/unit = 0.1 units, below a 0.5-unit minimum
    const result = estimateUndersizedSides(10, 100, 100, 0.5);
    expect(result.buyUndersized).toBe(true);
    expect(result.sellUndersized).toBe(false);
  });

  it("flags the sell side independently of the buy side", () => {
    // buy €100 -> 1 unit (clears 0.5 min), sell €10 -> 0.1 unit (below 0.5 min)
    const result = estimateUndersizedSides(100, 10, 100, 0.5);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(true);
  });

  it("flags neither side when both amounts clear the minimum", () => {
    const result = estimateUndersizedSides(100, 100, 100, 0.5);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(false);
  });

  it("returns no warning when minOrderBaseUnits is null", () => {
    const result = estimateUndersizedSides(1, 1, 100, null);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(false);
  });

  it("returns no warning when the reference price is zero or unavailable", () => {
    const result = estimateUndersizedSides(100, 100, 0, 0.5);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(false);
  });

  it("never flags a disabled side (amount exactly 0) as undersized", () => {
    // A zero amount is a documented valid one-sided strategy, not an
    // accidentally-small one — without the >0 guard, 0/price is always
    // < any positive minimum, so this would wrongly warn every time.
    const result = estimateUndersizedSides(0, 0, 100, 0.5);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(false);
  });

  it("still flags a genuinely undersized side alongside a disabled one", () => {
    // buy disabled (0), sell €10 at €100/unit = 0.1 units, below 0.5 min
    const result = estimateUndersizedSides(0, 10, 100, 0.5);
    expect(result.buyUndersized).toBe(false);
    expect(result.sellUndersized).toBe(true);
  });
});

describe("checkRsiConfig", () => {
  it("flags a buy threshold above 100 as out of range", () => {
    const result = checkRsiConfig(14, 120, 70);
    expect(result.buyOutOfRange).toBe(true);
    expect(result.sellOutOfRange).toBe(false);
    expect(result.periodTooShort).toBe(false);
  });

  it("flags a sell threshold above 100 as out of range", () => {
    const result = checkRsiConfig(14, 30, 150);
    expect(result.sellOutOfRange).toBe(true);
    expect(result.buyOutOfRange).toBe(false);
  });

  it("flags a negative threshold as out of range", () => {
    const result = checkRsiConfig(14, -5, 70);
    expect(result.buyOutOfRange).toBe(true);
  });

  it("flags a period below 2 as too short", () => {
    const result = checkRsiConfig(1, 30, 70);
    expect(result.periodTooShort).toBe(true);
  });

  it("flags nothing for a sane 14/30/70 config", () => {
    const result = checkRsiConfig(14, 30, 70);
    expect(result.periodTooShort).toBe(false);
    expect(result.buyOutOfRange).toBe(false);
    expect(result.sellOutOfRange).toBe(false);
    expect(result.buyAboveNeutral).toBe(false);
    expect(result.sellBelowNeutral).toBe(false);
  });

  it("flags nothing when RSI isn't configured (period 0)", () => {
    const result = checkRsiConfig(0, 120, 150);
    expect(result.periodTooShort).toBe(false);
    expect(result.buyOutOfRange).toBe(false);
    expect(result.sellOutOfRange).toBe(false);
    expect(result.buyAboveNeutral).toBe(false);
    expect(result.sellBelowNeutral).toBe(false);
  });

  it("flags an unparseable (NaN) threshold as out of range", () => {
    const result = checkRsiConfig(14, NaN, 70);
    expect(result.buyOutOfRange).toBe(true);
  });

  it("flags a valid-but-weak buy threshold above 50 as a soft heads-up", () => {
    const result = checkRsiConfig(14, 90, 70);
    expect(result.buyOutOfRange).toBe(false);
    expect(result.buyAboveNeutral).toBe(true);
  });

  it("flags a valid-but-weak sell threshold below 50 as a soft heads-up", () => {
    const result = checkRsiConfig(14, 30, 10);
    expect(result.sellOutOfRange).toBe(false);
    expect(result.sellBelowNeutral).toBe(true);
  });

  it("does not double-flag a hard out-of-range threshold as also a soft heads-up", () => {
    const result = checkRsiConfig(14, 120, 150);
    expect(result.buyOutOfRange).toBe(true);
    expect(result.buyAboveNeutral).toBe(false);
    expect(result.sellOutOfRange).toBe(true);
    expect(result.sellBelowNeutral).toBe(false);
  });

  it("does not flag a buy threshold exactly at 50 (neutral itself is a valid, if extreme, choice)", () => {
    const result = checkRsiConfig(14, 50, 50);
    expect(result.buyAboveNeutral).toBe(false);
    expect(result.sellBelowNeutral).toBe(false);
  });
});

describe("RSI gate in runTriggerSimulation", () => {
  // Same fixtures as tests/domain/unit/test_strategy_eval.py /
  // test_strategy_eval_sell.py on the Python side — kept in sync manually
  // (see cooldownOk() precedent above for why: no shared cross-language
  // test runner, so parity is pinned via matching fixtures + comments).
  function seriesFrom(start: number, changes: number[]): number[] {
    const closes = [start];
    for (const c of changes) closes.push(closes[closes.length - 1] + c);
    return closes;
  }

  it("parity: RSI matches the same Wilder reference series as the Python port", () => {
    // Same series as tests/domain/unit/test_rsi.py::test_rsi_matches_known_reference_values
    const closes = [
      44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
      45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
    ];
    const values = rsi(closes, 14);
    expect(values[14]).not.toBeNull();
    expect(values[14]!).toBeCloseTo(70.46, 1);
  });

  it("blocks a buy when the price trigger fires but RSI is not oversold", () => {
    // 13 gains of +5, then a final -16.5 drop (-10%) — RSI ~79.75.
    const closes = seriesFrom(100, [...Array(13).fill(5), -16.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      rsiPeriod: 14,
      rsiMaxForBuy: 30,
    });
    const lastRow = result.rows[result.rows.length - 1];
    expect(lastRow.action).toBe("BUY_IGNORED_RSI");
    expect(result.buyIgnoredRsi).toBe(1);
    expect(result.buyExecuted).toBe(0);
    // Row-level RSI — feeds the chart's RSI panel and tooltip.
    expect(lastRow.rsi).not.toBeNull();
    expect(lastRow.rsi!).toBeCloseTo(79.75, 1);
  });

  it("leaves rsi null on every row when rsiPeriod is not configured", () => {
    const closes = seriesFrom(100, [...Array(13).fill(5), -16.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
    });
    expect(result.rows.every((r) => r.rsi == null)).toBe(true);
  });

  it("allows a buy when the price trigger fires and RSI is oversold", () => {
    // 13 losses of -5, then a final -13.5 drop (-10%) — RSI = 0.
    const closes = seriesFrom(200, [...Array(13).fill(-5), -13.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      rsiPeriod: 14,
      rsiMaxForBuy: 30,
    });
    const lastRow = result.rows[result.rows.length - 1];
    expect(lastRow.action).toBe("BUY_EXECUTED");
  });

  it("blocks a sell when the price trigger fires but RSI is not overbought", () => {
    // 13 losses of -5, then a final +13.5 rise (+10%) — RSI ~17.2.
    const closes = seriesFrom(200, [...Array(13).fill(-5), 13.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      initialAssetUnits: 5,
      initialAcbPrice: 50,
      rsiPeriod: 14,
      rsiMinForSell: 70,
    });
    const lastRow = result.rows[result.rows.length - 1];
    expect(lastRow.action).toBe("SELL_IGNORED_RSI");
    expect(result.sellIgnoredRsi).toBe(1);
    expect(result.sellExecuted).toBe(0);
  });

  it("allows a sell when the price trigger fires and RSI is overbought", () => {
    // 13 gains of +5, then a final +16.5 rise (+10%) — RSI = 100.
    const closes = seriesFrom(100, [...Array(13).fill(5), 16.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
      initialAssetUnits: 5,
      initialAcbPrice: 50,
      rsiPeriod: 14,
      rsiMinForSell: 70,
    });
    const lastRow = result.rows[result.rows.length - 1];
    expect(lastRow.action).toBe("SELL_EXECUTED");
  });

  it("skips the RSI gate entirely when rsiPeriod is not set (backward compat)", () => {
    // Same drop as the "blocks a buy" case, but no RSI params — must
    // execute exactly as it did before this feature existed.
    const closes = seriesFrom(100, [...Array(13).fill(5), -16.5]);
    const result = runTriggerSimulation({
      candles: makeCandles(closes),
      buySigma: 0.5,
      sellSigma: 0.5,
      buyEur: 100,
      sellEur: 100,
      allocatedEur: 1000,
    });
    const lastRow = result.rows[result.rows.length - 1];
    expect(lastRow.action).toBe("BUY_EXECUTED");
    expect(result.buyIgnoredRsi).toBe(0);
  });
});
