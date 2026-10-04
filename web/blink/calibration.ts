import { NATURAL_BLINK_FLOOR_MS, type MorseTiming } from "./decoder";

export function percentile(xs: number[], p: number): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const i = (s.length - 1) * p;
  const lo = Math.floor(i);
  return s[lo] + (s[Math.min(lo + 1, s.length - 1)] - s[lo]) * (i - lo);
}

export const median = (xs: number[]) => percentile(xs, 0.5);

/** Eye-signal thresholds from the median open and closed levels. Throws if they're too close to tell apart. */
export function thresholdsFrom(open: number, closed: number): { closeThreshold: number; openThreshold: number } {
  if (closed - open < 0.15) throw new Error("Could not see a clear difference between open and closed eyes. Check the lighting and face the camera.");
  return {
    closeThreshold: +(open + 0.6 * (closed - open)).toFixed(3),
    openThreshold: +(open + 0.35 * (closed - open)).toFixed(3),
  };
}

export type BlinkSamples = {
  /** Durations (ms) of spontaneous blinks while looking at the screen. */
  natural: number[];
  /** Deliberate short / long blink durations (ms). */
  dots: number[];
  dashes: number[];
  /** Open gaps (ms) between consecutive prompted blinks. */
  gaps: number[];
};

export type TimingResult = {
  timing: MorseTiming;
  warnings: string[];
  /** The dot/dash step should be redone. */
  redoBlinks: boolean;
  stats: { naturalP95: number; dotMedian: number; dashMedian: number; shortestDot: number; gapP90: number };
};

export function timingFrom(s: BlinkSamples, base: MorseTiming): TimingResult {
  const warnings: string[] = [];
  let redoBlinks = false;
  const naturalP95 = percentile(s.natural, 0.95);
  const dotMedian = median(s.dots);
  const dashMedian = median(s.dashes);
  const shortestDot = s.dots.length ? Math.min(...s.dots) : 0;
  const gapP90 = percentile(s.gaps, 0.9);

  // Never below the natural-blink floor from the blink-data study, even if few natural blinks were seen.
  const minDotMs = Math.round(Math.max(naturalP95 + 50, NATURAL_BLINK_FLOOR_MS));
  if (minDotMs >= shortestDot) {
    warnings.push(`Some dots (shortest ${Math.round(shortestDot)} ms) were as short as natural blinks (cut-off ${minDotMs} ms). Make dots slower and more deliberate.`);
    redoBlinks = true;
  }
  if (dashMedian < 1.8 * dotMedian) {
    warnings.push(`Dashes (${Math.round(dashMedian)} ms) were not clearly longer than dots (${Math.round(dotMedian)} ms). Hold each dash longer.`);
    redoBlinks = true;
  }
  const dotDashBoundaryMs = Math.round(Math.sqrt(dotMedian * dashMedian));
  const letterGapMs = Math.round(Math.min(3500, Math.max(1200, gapP90 * 1.5)));
  const longestDash = s.dashes.length ? Math.max(...s.dashes) : 0;
  return {
    timing: {
      minDotMs,
      dotDashBoundaryMs,
      deleteMs: Math.max(base.deleteMs, Math.round(longestDash * 1.5)),
      letterGapMs,
    },
    warnings,
    redoBlinks,
    stats: { naturalP95, dotMedian, dashMedian, shortestDot, gapP90 },
  };
}
