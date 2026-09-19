import { describe, it, expect } from "vitest";
import { sma, ema, macd, rsi } from "../indicators";

describe("sma", () => {
  it("computes simple moving average correctly", () => {
    const closes = [10, 11, 12, 13, 14];
    const result = sma(closes, 3);
    expect(result[0]).toBeNull();
    expect(result[1]).toBeNull();
    expect(result[2]).toBeCloseTo(11); // (10+11+12)/3
    expect(result[3]).toBeCloseTo(12); // (11+12+13)/3
    expect(result[4]).toBeCloseTo(13); // (12+13+14)/3
  });

  it("returns all nulls when insufficient data", () => {
    const result = sma([10, 20], 5);
    expect(result).toEqual([null, null]);
  });

  it("handles single-period SMA (identity)", () => {
    const closes = [5, 10, 15];
    const result = sma(closes, 1);
    expect(result).toEqual([5, 10, 15]);
  });
});

describe("ema", () => {
  it("seeds from SMA and converges", () => {
    const closes = [22, 22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29];
    const result = ema(closes, 10);
    // First 9 values should be null
    for (let i = 0; i < 9; i++) expect(result[i]).toBeNull();
    // Index 9 = SMA of first 10 values
    const expectedSma = closes.slice(0, 10).reduce((a, b) => a + b, 0) / 10;
    expect(result[9]).toBeCloseTo(expectedSma, 4);
    // Index 10 = EMA smoothed
    expect(result[10]).not.toBeNull();
    expect(typeof result[10]).toBe("number");
  });

  it("returns all nulls when fewer points than period", () => {
    const result = ema([10, 20, 30], 5);
    expect(result).toEqual([null, null, null]);
  });
});

describe("macd", () => {
  it("returns line, signal, and histogram", () => {
    // 40 data points — enough for EMA-26 seed + 9 signal periods
    const closes = Array.from({ length: 40 }, (_, i) => 100 + Math.sin(i / 3) * 5);
    const result = macd(closes);
    expect(result.line).toHaveLength(40);
    expect(result.signal).toHaveLength(40);
    expect(result.histogram).toHaveLength(40);

    // First 25 values of line should be null (need EMA-26)
    for (let i = 0; i < 25; i++) expect(result.line[i]).toBeNull();
    // Line at index 25 should be a number
    expect(result.line[25]).not.toBeNull();
    expect(typeof result.line[25]).toBe("number");
  });

  it("histogram equals line minus signal", () => {
    const closes = Array.from({ length: 50 }, (_, i) => 100 + i * 0.3);
    const { line, signal, histogram } = macd(closes);
    for (let i = 0; i < closes.length; i++) {
      if (line[i] != null && signal[i] != null) {
        expect(histogram[i]).toBeCloseTo(line[i]! - signal[i]!, 10);
      }
    }
  });

  it("returns all nulls when insufficient data", () => {
    const result = macd([10, 20, 30]);
    expect(result.line.every((v) => v === null)).toBe(true);
    expect(result.signal.every((v) => v === null)).toBe(true);
    expect(result.histogram.every((v) => v === null)).toBe(true);
  });
});

describe("rsi", () => {
  it("computes RSI with known up/down sequence", () => {
    // 14-period RSI on a simple trend
    const closes: number[] = [];
    for (let i = 0; i < 30; i++) closes.push(100 + i * 0.5 + (i % 3 === 0 ? -1 : 0.5));
    const result = rsi(closes, 14);

    // First 14 values should be null
    for (let i = 0; i < 14; i++) expect(result[i]).toBeNull();

    // RSI at index 14 should be between 0 and 100
    expect(result[14]).not.toBeNull();
    expect(result[14]!).toBeGreaterThan(0);
    expect(result[14]!).toBeLessThan(100);
  });

  it("returns 100 when all gains", () => {
    const closes = Array.from({ length: 20 }, (_, i) => 100 + i);
    const result = rsi(closes, 14);
    expect(result[14]).toBe(100);
  });

  it("returns 0 when all losses", () => {
    const closes = Array.from({ length: 20 }, (_, i) => 100 - i);
    const result = rsi(closes, 14);
    expect(result[14]).toBe(0);
  });

  it("returns all nulls when insufficient data", () => {
    const result = rsi([10, 20, 30], 14);
    expect(result).toEqual([null, null, null]);
  });
});
