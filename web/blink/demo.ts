import type { MorseTiming } from "./decoder";
import { MORSE, SEND_CODE } from "./table";

export type DemoSegment = { closed: boolean; ms: number; note?: string };

/**
 * A scripted run of eye closures for "Watch a demo": two natural blinks (which must be
 * ignored), the word in Morse, then the AR prosign that speaks it. Timed from `t`.
 */
export function demoTimeline(word: string, t: MorseTiming): DemoSegment[] {
  const dot = Math.round((t.minDotMs + t.dotDashBoundaryMs) / 2);
  const dash = Math.round(Math.min(t.dotDashBoundaryMs + 500, (t.dotDashBoundaryMs + t.deleteMs) / 2));
  const segs: DemoSegment[] = [
    { closed: false, ms: 600, note: "Demo: two normal blinks first. They're ignored" },
    { closed: true, ms: 120 },
    { closed: false, ms: 900 },
    { closed: true, ms: 300 },
    { closed: false, ms: 1500, note: `Demo: blinking “${word}”` },
  ];
  const symbols = (code: string) => {
    for (const s of code) segs.push({ closed: true, ms: s === "." ? dot : dash }, { closed: false, ms: 350 });
    segs.push({ closed: false, ms: t.letterGapMs + 250 });
  };
  for (const ch of word.toUpperCase()) if (MORSE[ch]) symbols(MORSE[ch]);
  symbols(SEND_CODE);
  segs[segs.length - 1].note = "Demo finished";
  return segs;
}
