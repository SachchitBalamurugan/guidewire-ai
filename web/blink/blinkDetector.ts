export type BlinkParams = {
  /** Averaged eye signal must rise above this to count as closed. */
  closeThreshold: number;
  /** …and fall below this (lower) level to count as open again. */
  openThreshold: number;
  /** A reopening shorter than this is a flicker: the closure continues. */
  mergeGapMs: number;
};

export const DEFAULT_BLINK: BlinkParams = { closeThreshold: 0.5, openThreshold: 0.3, mergeGapMs: 100 };

/** Edges of a (debounced) closure. Times are real timestamps of the frames where the change happened. */
export type EyeEdge =
  | { type: "close"; t: number }
  | { type: "open"; t: number; closedAt: number; durationMs: number; merged: number };

export type DetectorFrame = {
  t: number;
  faceFound: boolean;
  /** Smoothed eyeBlinkLeft / eyeBlinkRight (0 open, 1 closed). */
  left: number;
  right: number;
  /** Mean of the smoothed left/right signals: what the thresholds apply to. */
  signal: number;
  /** Debounced eye state after hysteresis and flicker merging. */
  closed: boolean;
  edges: EyeEdge[];
};

/**
 * Turns per-frame blendshape scores into clean closures:
 * - light EMA smoothing (~50 ms time constant, computed from real timestamps);
 * - both eyes must be closed: the mean must pass closeThreshold AND each eye must be past openThreshold (rejects winks);
 * - hysteresis between closeThreshold and openThreshold;
 * - a reopening shorter than mergeGapMs is merged into the surrounding closure;
 * - a short face dropout (often at the bottom of a hard blink) keeps the state; a long one ends the closure.
 */
export class BlinkDetector {
  params: BlinkParams;
  private left = 0;
  private right = 0;
  private lastT = -1;
  private raw = false;
  private closed = false;
  private closedAt = 0;
  private reopenAt = -1;
  private merged = 0;
  private lostSince = -1;

  constructor(
    params: BlinkParams = DEFAULT_BLINK,
    private smoothingMs = 50,
    private lostMs = 300,
  ) {
    this.params = { ...params };
  }

  /** Feed one frame. Pass null scores when no face was found. */
  push(left: number | null, right: number | null, t: number): DetectorFrame {
    const edges: EyeEdge[] = [];
    const p = this.params;
    const faceFound = left !== null && right !== null;
    if (!faceFound) {
      if (this.lostSince < 0) this.lostSince = t;
      if (this.closed && this.reopenAt < 0 && t - this.lostSince >= this.lostMs) {
        // Face gone too long: end the closure where the face vanished, so a look-away never types a dash.
        this.reopenAt = this.lostSince;
        this.raw = false;
      }
    } else {
      this.lostSince = -1;
      const a = this.lastT < 0 ? 1 : 1 - Math.exp(-(t - this.lastT) / this.smoothingMs);
      this.left += a * (left - this.left);
      this.right += a * (right - this.right);
      this.lastT = t;
      const signal = (this.left + this.right) / 2;
      if (!this.raw && signal >= p.closeThreshold && Math.min(this.left, this.right) >= p.openThreshold) {
        this.raw = true;
        if (!this.closed) {
          this.closed = true;
          this.closedAt = t;
          this.merged = 0;
          edges.push({ type: "close", t });
        } else if (this.reopenAt >= 0) {
          this.reopenAt = -1;
          this.merged++;
        }
      } else if (this.raw && signal <= p.openThreshold) {
        this.raw = false;
        if (this.closed) this.reopenAt = t;
      }
    }
    if (this.closed && this.reopenAt >= 0 && t - this.reopenAt >= p.mergeGapMs) {
      const end = this.reopenAt;
      edges.push({ type: "open", t: end, closedAt: this.closedAt, durationMs: end - this.closedAt, merged: this.merged });
      this.closed = false;
      this.reopenAt = -1;
    }
    return { t, faceFound, left: this.left, right: this.right, signal: (this.left + this.right) / 2, closed: this.closed, edges };
  }
}
