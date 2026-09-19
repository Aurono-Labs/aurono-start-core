/**
 * Technical indicator computations for candle data.
 * All functions are pure — no side effects, no API calls.
 */

/** Simple Moving Average. Returns null for indices with fewer than `period` data points. */
export function sma(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      result.push(null);
    } else {
      let sum = 0;
      for (let j = i - period + 1; j <= i; j++) sum += closes[j];
      result.push(sum / period);
    }
  }
  return result;
}

/** Exponential Moving Average. Returns null for indices before the initial SMA seed. */
export function ema(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  const k = 2 / (period + 1);

  // Seed with SMA of first `period` values
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      result.push(null);
    } else if (i === period - 1) {
      let sum = 0;
      for (let j = 0; j < period; j++) sum += closes[j];
      result.push(sum / period);
    } else {
      const prev = result[i - 1]!;
      result.push(closes[i] * k + prev * (1 - k));
    }
  }
  return result;
}

/** MACD (Moving Average Convergence Divergence). Returns three arrays: line, signal, histogram. */
export function macd(
  closes: number[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): { line: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } {
  const emaFast = ema(closes, fastPeriod);
  const emaSlow = ema(closes, slowPeriod);

  // MACD line = EMA(fast) - EMA(slow)
  const line: (number | null)[] = emaFast.map((f, i) => {
    const s = emaSlow[i];
    return f != null && s != null ? f - s : null;
  });

  // Signal line = EMA of MACD line (only over non-null values)
  // We need to run EMA on the non-null tail of `line`
  const firstValidIdx = line.findIndex((v) => v != null);
  if (firstValidIdx === -1) {
    return { line, signal: line.map(() => null), histogram: line.map(() => null) };
  }

  const macdValues = line.slice(firstValidIdx) as number[];
  const signalRaw = ema(macdValues, signalPeriod);

  const signal: (number | null)[] = new Array(firstValidIdx).fill(null);
  for (const v of signalRaw) signal.push(v);

  // Histogram = MACD line - signal
  const histogram: (number | null)[] = line.map((l, i) => {
    const s = signal[i];
    return l != null && s != null ? l - s : null;
  });

  return { line, signal, histogram };
}

/** RSI (Relative Strength Index). Uses Wilder's smoothing. Returns null for first `period` values. */
export function rsi(closes: number[], period: number): (number | null)[] {
  if (closes.length < period + 1) return closes.map(() => null);

  const result: (number | null)[] = [];

  // Calculate initial average gain/loss from first `period` changes
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    if (change > 0) avgGain += change;
    else avgLoss += Math.abs(change);
  }
  avgGain /= period;
  avgLoss /= period;

  // Fill nulls for indices 0..period-1
  for (let i = 0; i < period; i++) result.push(null);

  // First RSI value at index `period`
  const rs0 = avgLoss === 0 ? Infinity : avgGain / avgLoss;
  result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + rs0));

  // Wilder's smoothing for remaining values
  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rs = avgLoss === 0 ? Infinity : avgGain / avgLoss;
    result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + rs));
  }

  return result;
}
