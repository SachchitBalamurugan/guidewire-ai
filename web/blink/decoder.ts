import { CLEAR_CODE, SEND_CODE, SPACE_CODE, decodeCode } from "./table";

export type MorseTiming = {
  /** Closures shorter than this are natural blinks and are ignored. */
  minDotMs: number;
  /** Closures at least this long are dashes; shorter (but ≥ minDotMs) are dots. */
  dotDashBoundaryMs: number;
  /** Closures at least this long delete instead (fired on reopening). */
  deleteMs: number;
  /** Eyes open this long after a symbol commits the letter (when adaptive timing has no data, or is off). */
  letterGapMs: number;
};

/**
 * Natural blinks are not as short as they look: across 469 annotated blinks (Eyeblink8 + Talking Face,
 * eval/blink_results.json) the eye is fully closed for up to 600–800 ms in the tail. A 500 ms floor lets
 * only 1.7–5.5% of natural blinks through (vs 48–100% at 150 ms), so a dot is a deliberate half-second close.
 */
export const NATURAL_BLINK_FLOOR_MS = 450;

export const DEFAULT_TIMING: MorseTiming = {
  // Dot floor and dash boundary from the natural-blink study above (people-demos-datasets).
  minDotMs: 500,
  dotDashBoundaryMs: 1200,
  deleteMs: 3000,
  letterGapMs: 1500,
};

/** Adaptive letter gap: max(floor, factor × median intra-letter gap over the last `letters` letters), capped. */
export const ADAPTIVE = { floorMs: 1500, factor: 2.5, capMs: 5000, letters: 20, minSamples: 3 };

/** The open gap before a closure: within a letter, after a committed letter, or the very first. */
export type GapKind = "intra" | "letter" | "start";
type GapInfo = { gapBeforeMs: number | null; gapKind: GapKind };

export type MorseEvent =
  | ({ type: "symbol"; symbol: "." | "-"; buffer: string; durationMs: number; merged: number } & GapInfo)
  | ({ type: "ignored"; durationMs: number; merged: number } & GapInfo)
  /** target "pending" discards the symbols blinked so far; "letter" removes the last transcript character. */
  | ({ type: "delete"; target: "pending" | "letter"; durationMs: number; merged: number } & GapInfo)
  /** ········ was blinked: the pending sequence (including the 8 dots) was discarded. */
  | { type: "clear"; code: string }
  | { type: "letter"; char: string; code: string }
  /** A sequence that is not a Morse character: discarded, never typed. */
  | { type: "invalid"; code: string }
  /** Only from the ··−− code; pauses never add spaces. */
  | { type: "space" }
  | { type: "send" };

/** What the progress ring should show right now. */
export type DecoderPhase =
  | { kind: "idle" }
  | { kind: "closed"; ms: number; as: "ignored" | "dot" | "dash" | "delete" }
  | { kind: "letter"; progress: number; remainingMs: number };

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/**
 * Morse state machine driven by closure edges with real timestamps.
 * The camera (via BlinkDetector) and the keyboard (SPACE down/up) both call close()/open(),
 * and a periodic tick() commits letters when the letter gap elapses, so both inputs share all timing logic.
 *
 * Natural (ignored) blinks don't touch the gap timer.
 */
export class MorseDecoder {
  timing: MorseTiming;
  /** Adapt the letter gap to this user's own pauses within letters. */
  adaptive: boolean;
  buffer = "";
  private closedAt = -1;
  /** Reopening time of the last real symbol (-1 before the first). */
  private openedAt = -1;
  private gap: GapInfo = { gapBeforeMs: null, gapKind: "start" };
  /** Intra-letter gaps of the letter being blinked, and of the last committed letters. */
  private letterGaps: number[] = [];
  private history: number[][] = [];

  constructor(timing: MorseTiming = DEFAULT_TIMING, opts: { adaptive?: boolean } = {}) {
    this.timing = { ...timing };
    this.adaptive = opts.adaptive ?? false;
  }

  get closed(): boolean {
    return this.closedAt >= 0;
  }

  /** Intra-letter gaps the adaptive timing is currently based on. */
  get gapSamples(): number[] {
    return this.history.flat();
  }

  /** The letter gap in effect right now. */
  get letterGapMs(): number {
    const samples = this.gapSamples;
    if (!this.adaptive || samples.length < ADAPTIVE.minSamples) return this.timing.letterGapMs;
    return Math.round(Math.min(ADAPTIVE.capMs, Math.max(ADAPTIVE.floorMs, ADAPTIVE.factor * median(samples))));
  }

  reset(): void {
    this.buffer = "";
    this.closedAt = -1;
    this.letterGaps = [];
  }

  /** Eyes closed (or key down) at time t. Commits a letter whose gap elapsed before it. */
  close(t: number): MorseEvent[] {
    if (this.closed) return [];
    const events = this.tick(t);
    this.gap =
      this.openedAt < 0
        ? { gapBeforeMs: null, gapKind: "start" }
        : { gapBeforeMs: t - this.openedAt, gapKind: this.buffer ? "intra" : "letter" };
    this.closedAt = t;
    return events;
  }

  /** Eyes reopened (or key up) at time t: classify the closure. */
  open(t: number, merged = 0): MorseEvent[] {
    if (!this.closed) return [];
    const durationMs = t - this.closedAt;
    this.closedAt = -1;
    const gap = this.gap;
    const k = this.classify(durationMs);
    if (k === "ignored") return [{ type: "ignored", durationMs, merged, ...gap }];
    this.openedAt = t;
    if (k === "delete") {
      const target = this.buffer ? "pending" : "letter";
      this.buffer = "";
      this.letterGaps = [];
      return [{ type: "delete", target, durationMs, merged, ...gap }];
    }
    if (gap.gapKind === "intra" && gap.gapBeforeMs !== null) this.letterGaps.push(gap.gapBeforeMs);
    const symbol = k === "dash" ? "-" : ".";
    this.buffer += symbol;
    const events: MorseEvent[] = [{ type: "symbol", symbol, buffer: this.buffer, durationMs, merged, ...gap }];
    if (this.buffer.endsWith(CLEAR_CODE)) {
      events.push({ type: "clear", code: this.buffer });
      this.buffer = "";
      this.letterGaps = [];
    }
    return events;
  }

  /** Call regularly while eyes are open: commits the letter when the letter gap elapses. */
  tick(now: number): MorseEvent[] {
    if (this.closed || !this.buffer || now - this.openedAt < this.letterGapMs) return [];
    const code = this.buffer;
    this.buffer = "";
    if (this.letterGaps.length) {
      this.history.push(this.letterGaps);
      if (this.history.length > ADAPTIVE.letters) this.history.shift();
    }
    this.letterGaps = [];
    if (code === SEND_CODE) return [{ type: "send" }];
    if (code === SPACE_CODE) return [{ type: "space" }];
    const char = decodeCode(code);
    return [char ? { type: "letter", char, code } : { type: "invalid", code }];
  }

  classify(durationMs: number): "ignored" | "dot" | "dash" | "delete" {
    const t = this.timing;
    if (durationMs < t.minDotMs) return "ignored";
    if (durationMs >= t.deleteMs) return "delete";
    return durationMs >= t.dotDashBoundaryMs ? "dash" : "dot";
  }

  phase(now: number): DecoderPhase {
    if (this.closed) {
      const ms = now - this.closedAt;
      return { kind: "closed", ms, as: this.classify(ms) };
    }
    if (!this.buffer) return { kind: "idle" };
    const gapMs = this.letterGapMs;
    const open = now - this.openedAt;
    return { kind: "letter", progress: Math.min(1, open / gapMs), remainingMs: Math.max(0, gapMs - open) };
  }
}

/** Applies decoder events to a transcript string. */
export function applyEvent(text: string, e: MorseEvent): string {
  switch (e.type) {
    case "letter":
      return text + e.char;
    case "space":
      return !text || text.endsWith(" ") ? text : text + " ";
    case "delete":
      return e.target === "letter" ? text.trimEnd().slice(0, -1) : text;
    default:
      return text;
  }
}
