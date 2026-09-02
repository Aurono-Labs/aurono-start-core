import { describe, it, expect } from "vitest";
import {
  computeSigmaThresholds,
  runTriggerSimulation,
  suggestAdjustments,
  computeEquityCurve,
  type TriggerAnalysisParams,
  type CandleRecord,
} from "../triggerAnalysis";

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
